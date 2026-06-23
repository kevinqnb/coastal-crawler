"""LLM-based abstract relevance filter using logprob confidence scoring.

Confidence is computed as p_true / (p_true + p_false), where p_true and
p_false are summed from the top-20 token logprobs at the single generated
position (max_tokens=1).  Case variants (true/True/TRUE, false/False/FALSE)
are all counted.  When neither appears in the top 20, the paper is conservatively
rejected and confidence is stored as NULL.
"""

from __future__ import annotations

import math

import structlog
from openai import OpenAI

from coastal_crawler.db import store
from coastal_crawler.db.engine import get_session

log = structlog.get_logger(__name__)

# Appended to every user-supplied system prompt so the model knows the
# expected output format.
_FORMAT_INSTRUCTION = (
    "\n\nRespond with only the single word true if the paper is relevant, "
    "or false if it is not. No other text."
)


class AbstractFilter:
    """Classifies a paper abstract via an OpenAI-compatible chat endpoint.

    Sends one chat completion request per paper with max_tokens=1 and
    top_logprobs, then extracts a calibrated confidence score from the
    returned logprobs.
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        system_prompt: str,
        seed: int = 0,
        temperature: float = 0.0,
        top_logprobs: int = 20,
    ) -> None:
        self.client = client
        self.model = model
        self._system_prompt = system_prompt + _FORMAT_INSTRUCTION
        self._seed = seed
        self._temperature = temperature
        self._top_logprobs = top_logprobs

    def classify(
        self, title: str | None, abstract: str | None
    ) -> tuple[bool, float | None]:
        """Return (is_relevant, confidence) for one paper.

        confidence is None when the model's top-N logprobs contained no
        boolean token — caller should treat as irrelevant (conservative).
        """
        if not abstract:
            return False, None

        user_content = f"Title: {title or '(no title)'}\n\nAbstract: {abstract}"

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=1,
            logprobs=True,
            top_logprobs=self._top_logprobs,
            temperature=self._temperature,
            seed=self._seed,
        )

        choice = response.choices[0]
        top = (
            choice.logprobs.content[0].top_logprobs
            if choice.logprobs and choice.logprobs.content
            else []
        )

        p_true = sum(
            math.exp(t.logprob) for t in top if t.token.strip().lower() == "true"
        )
        p_false = sum(
            math.exp(t.logprob) for t in top if t.token.strip().lower() == "false"
        )

        if p_true + p_false == 0.0:
            log.warning(
                "filter_no_boolean_token",
                top_tokens=[t.token for t in top[:5]],
                top_logprobs_n=self._top_logprobs,
            )
            return False, None

        confidence = p_true / (p_true + p_false)
        return confidence >= 0.5, confidence


def run_filter(batch_size: int = 50) -> tuple[int, int, int]:
    """Claim a batch of discovered papers and classify each as relevant or irrelevant.

    Status transitions:
      discovered → filtering  (claimed)
      filtering  → relevant   (passed)
      filtering  → irrelevant (failed or no abstract)
      filtering  → discovered (API error — reset for retry next run)

    Returns:
        (relevant, irrelevant, errors) where errors are papers reset to
        'discovered' due to API failures.
    """
    from coastal_crawler.config import get_settings

    settings = get_settings()

    if not settings.filter_model or not settings.filter_relevance_prompt:
        raise RuntimeError(
            "FILTER_MODEL and FILTER_RELEVANCE_PROMPT must be configured to run the filter."
        )

    client = OpenAI(
        base_url=settings.filter_base_url,
        api_key=settings.filter_api_key,
    )
    abstract_filter = AbstractFilter(
        client=client,
        model=settings.filter_model,
        system_prompt=settings.filter_relevance_prompt,
        seed=settings.filter_seed,
        temperature=settings.filter_temperature,
        top_logprobs=settings.filter_top_logprobs,
    )

    with get_session() as session:
        papers = store.claim_batch_for_filter(batch_size, session)
        paper_data = [(p.id, p.title, p.abstract) for p in papers]

    log.info("filter_batch_claimed", count=len(paper_data))

    relevant = irrelevant = errors = 0

    for paper_id, title, abstract in paper_data:
        if not abstract:
            with get_session() as session:
                store.mark_irrelevant(paper_id, None, session)
            irrelevant += 1
            log.info("paper_irrelevant", paper_id=paper_id, reason="no_abstract")
            continue

        try:
            is_relevant, confidence = abstract_filter.classify(title, abstract)
            with get_session() as session:
                if is_relevant:
                    store.mark_relevant(paper_id, confidence, session)
                    relevant += 1
                else:
                    store.mark_irrelevant(paper_id, confidence, session)
                    irrelevant += 1
            log.info(
                "paper_filtered",
                paper_id=paper_id,
                relevant=is_relevant,
                confidence=round(confidence, 4) if confidence is not None else None,
            )
        except Exception as exc:
            log.warning("filter_api_error", paper_id=paper_id, error=str(exc))
            with get_session() as session:
                store.reset_to_discovered(paper_id, session)
            errors += 1

    log.info("filter_batch_done", relevant=relevant, irrelevant=irrelevant, errors=errors)
    return relevant, irrelevant, errors
