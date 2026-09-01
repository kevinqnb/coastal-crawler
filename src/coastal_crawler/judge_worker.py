"""Judge worker — scores each claimed extraction's validity via
JudgementLM's next-token p_true and a trained probe, plus per-context-token
attribution scores from both attribution methods.

Claims extraction rows with ``judge_status='pending'`` (see
db/store.py's claim_batch_for_judge) and processes them one at a time —
unlike worker.py's chunked adapter.extract_batch() calls, JudgementLM has no
continuous-batching server to hide latency behind; each row is one
sequential generate() plus two attribute() calls against the same
in-process-loaded model (see adapter.build_judge/JudgeComponents).
"""

from __future__ import annotations

import time
from typing import Any, Callable

import structlog
from scholarlm.judgementlm import tokenize

from coastal_crawler.adapter import JudgeComponents
from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session
from coastal_crawler.site.snippets import find_snippet

log = structlog.get_logger(__name__)

# Paper statuses that mean more 'pending' extraction rows could still show up
# (extraction hasn't reached a terminal state for that paper yet) — mirrors
# worker.py's _UPSTREAM_STATUSES, one stage further downstream: judge's
# upstream is extraction, not OCR.
_UPSTREAM_STATUSES = ("relevant", "ocr_processing", "ocr_done", "processing")


def _build_query(data: dict[str, Any]) -> str:
    """Per-extraction query built from its own fields — not a Settings value
    (see JUDGE_INSTRUCTIONS_PROMPT's docstring): attribute/value/units vary
    per row, unlike the static instructions describing the judgement task.
    """
    attribute = data.get("attribute")
    value = data.get("value")
    units = data.get("units")
    return (
        f"Is the following extracted measurement supported by the context? "
        f"Attribute: {attribute!r}, value: {value!r}, units: {units!r}."
    )


def run_judge_worker(
    batch_size: int = 10,
    components: JudgeComponents | None = None,
) -> tuple[int, int, int]:
    """Claim a batch of unjudged extractions and run judgement + attribution.

    The batch-claim transaction commits immediately so other judge workers
    see judge_status='judging' and skip these rows. Each extraction then
    gets its own short DB transaction: either mark_judged + two
    insert_attribution calls, or mark_judge_failed with the error text —
    except a missing PaperOcrContext row, which is a data-consistency
    problem rather than a judgement failure, so that row is reset back to
    'pending' (counted in ``requeued``) instead of 'judge_failed'.

    Args:
        batch_size: Maximum extraction rows to claim in one run.
        components: JudgeComponents from adapter.build_judge(settings).
            Required — there is no stub judge; tests inject a fake with
            matching .generate()/.attribute() methods instead.

    Returns:
        (judged, failed, requeued) counts for the batch.
    """
    if components is None:
        raise RuntimeError(
            "components must be provided (adapter.build_judge(settings)) — "
            "there is no default/stub judge."
        )
    judge = components.judge
    attribution_methods = components.attribution_methods
    instructions = components.instructions_prompt

    with get_session() as session:
        extractions = store.claim_batch_for_judge(batch_size, session)
        claimed = [(e.id, e.paper_id, e.data) for e in extractions]
    # judge_status='judging' now committed; session closed

    log.info("judge_batch_claimed", count=len(claimed))
    if not claimed:
        return 0, 0, 0

    judged = failed = requeued = 0

    for extraction_id, paper_id, data in claimed:
        with get_session() as session:
            ocr_context = store.get_paper_ocr_context(session, paper_id)

        if ocr_context is None:
            log.warning(
                "judge_ocr_context_missing", extraction_id=extraction_id, paper_id=paper_id
            )
            with get_session() as session:
                store.reset_judging_to_pending(extraction_id, session)
            requeued += 1
            continue

        with get_session() as session:
            try:
                if data is None:
                    raise ValueError("extraction row has no data (nothing to judge)")

                snippet = find_snippet(
                    ocr_context, data.get("value"), data.get("attribute"), data.get("units")
                )
                context = snippet.text
                query = _build_query(data)

                response = judge.generate(instructions, context, query)
                confidence = response["p_true"]

                # tokenize() is called independently (not derived from
                # attribute()'s return) to get each context token's own
                # decoded string for storage — attribute() only returns
                # scores + context_token_indices, per
                # notes/coastal-crawler/builds/2026-08-18-judgement-attribution-01.md
                # ("attributions storage shape"). Same args as attribute()'s
                # own internal tokenize() call, so context_token_indices
                # line up exactly.
                tokenized_prompt, _instr_idx, context_token_idx, _query_idx = tokenize(
                    instructions,
                    context,
                    query,
                    judge.tokenizer,
                    use_chat_template=judge.use_chat_template,
                    answer_cue=judge.answer_cue,
                )
                tokens = [
                    judge.tokenizer.decode([tokenized_prompt[i]]) for i in context_token_idx
                ]

                probe_score: float | None = None
                for method_name, method in attribution_methods.items():
                    result = method.attribute(instructions, context, query)
                    scores = [float(s) for s in result["scores"]]
                    token_indices = [int(i) for i in result["context_token_indices"]]
                    if token_indices != context_token_idx:
                        raise ValueError(
                            f"{method_name}'s context_token_indices do not match "
                            f"the independently tokenized indices used for `tokens` "
                            f"— tokenize() call is out of sync with attribute()'s "
                            f"internal one."
                        )
                    store.insert_attribution(
                        extraction_id, method_name, scores, token_indices, tokens, context, session
                    )
                    if method_name == "probe":
                        probe_score = float(result["probe_output"])

                store.mark_judged(extraction_id, confidence, probe_score, session)
                judged += 1
                log.info(
                    "extraction_judged",
                    extraction_id=extraction_id,
                    confidence=confidence,
                    probe_score=probe_score,
                )
            except Exception as exc:
                session.rollback()
                store.mark_judge_failed(extraction_id, str(exc)[:2000], session)
                failed += 1
                log.warning("extraction_judge_failed", extraction_id=extraction_id, error=str(exc))

    log.info("judge_batch_done", judged=judged, failed=failed, requeued=requeued)
    return judged, failed, requeued


def run_judge_worker_until_idle(
    batch_size: int = 10,
    components: JudgeComponents | None = None,
    poll_interval: float = 60.0,
    idle_timeout: float = 1800.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.monotonic,
) -> tuple[int, int, int]:
    """Repeatedly claim and judge batches, waiting for new pending extractions.

    Mirrors worker.py's ``run_worker_until_idle`` one stage downstream: calls
    ``run_judge_worker()`` in a loop so judging can start as soon as the first
    extraction rows land and keep picking up newly ``pending`` rows as a
    concurrently-running extract job produces them. The idle clock resets on
    any batch that judges or fails at least one row — ``requeued`` (missing
    PaperOcrContext, a data-consistency issue) does NOT count as progress,
    same rationale as worker.py's version: it would otherwise busy-loop
    claim/requeue against the DB forever with no sleep in between. Returns
    once ``idle_timeout`` seconds have elapsed with no new judged/failed rows,
    or immediately once there is no upstream extraction work left at all
    (``relevant``/``ocr_processing``/``ocr_done``/``processing`` all empty)
    rather than waiting out the full idle window for a queue that's provably
    drained.

    ``sleep_fn``/``now_fn`` are injectable so tests don't sleep for real.

    Returns:
        (judged, failed, requeued) counts summed across every batch.
    """
    total_judged = total_failed = total_requeued = 0
    idle_start: float | None = None

    while True:
        judged, failed, requeued = run_judge_worker(batch_size, components)
        total_judged += judged
        total_failed += failed
        total_requeued += requeued

        if judged + failed > 0:
            idle_start = None
            continue

        if idle_start is None:
            idle_start = now_fn()
        if now_fn() - idle_start >= idle_timeout:
            break

        with get_session() as session:
            counts = store.count_by_status(session)
        if sum(counts.get(s, 0) for s in _UPSTREAM_STATUSES) == 0:
            break

        sleep_fn(poll_interval)

    return total_judged, total_failed, total_requeued


def requeue_judge_processing() -> int:
    """Reset all extractions with judge_status='judging' back to 'pending'.

    Rescues extraction rows stranded mid-batch by a judge job that was
    killed (walltime limit, OOM, node preemption) before it could mark them
    judged or judge_failed.

    Returns:
        Count of extraction rows requeued.
    """
    with get_session() as session:
        return store.requeue_judge_processing(session)
