"""OCRLM — VLM-based OCR of PDF documents into tagged markdown text.

Native, fast-mode-only port of scholarlm's ``DocumentLM``. Renders each PDF
page as an image, sends it through a vision-language model with an OCR
prompt, and reassembles page outputs into a single markdown document with
``<page number="N">``/``<table number="N">`` tags.
"""

from __future__ import annotations

import asyncio
import re
import time
import warnings
import yaml
from itertools import count
from typing import Any

import structlog
from openai import AsyncOpenAI

from coastal_crawler.extraction.pdf_render import render_pdf_pages

log = structlog.get_logger(__name__)

# Fast-preset constants (the only quality mode this pipeline needs).
_TARGET_LONGEST_DIM = 1024
_DEFAULT_MAX_TOKENS = 8192
_MAX_RETRY_ROUNDS = 1

_DEFAULT_OCR_PROMPT = (
    "Convert the pdf document to markdown text as accurately as possible. "
    "Display tables in html format. Rotate tables if they are presented sideways. "
    "Use hash symbols (e.g. #, ##) to indicate headings. "
    "Do not start a new line for italic items."
)


class OCRLM:
    """
    A vision-language model (VLM) wrapper for converting PDF documents to markdown text.

    Fast-mode only: 1024px page renders, no orientation correction, 8192 max
    tokens, 1 retry round.

    Args:
        model_name: Name or path of the VLM to use for OCR, served via a
            vLLM OpenAI-compatible endpoint.
        ocr_prompt: System prompt for the OCR task. Defaults to a standard
            instruction to convert PDF pages to markdown with HTML tables.
        sampling_params: Sampling parameters for text generation.
            ``max_tokens`` here overrides the fast-preset default.
        api_base: Base URL of the vLLM OpenAI-compatible endpoint.
        api_key: API key for the endpoint (use "EMPTY" for local vLLM).
        max_concurrent: Maximum number of concurrent async API calls.
    """

    def __init__(
        self,
        model_name: str,
        ocr_prompt: str | None = None,
        sampling_params: dict[str, Any] | None = None,
        api_base: str | None = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        max_concurrent: int = 32,
    ):
        self.model_name = model_name
        self.max_concurrent = max_concurrent

        self.sampling_params = {
            "temperature": 0.1,
            "max_tokens": _DEFAULT_MAX_TOKENS,
        } | (sampling_params or {})
        self.max_tokens: int = self.sampling_params.get("max_tokens", _DEFAULT_MAX_TOKENS)

        self.ocr_prompt = ocr_prompt if ocr_prompt is not None else _DEFAULT_OCR_PROMPT

        self.async_client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=600.0, max_retries=3)

    # -----------------------------------------------------------------------
    # Core API call helpers
    # -----------------------------------------------------------------------

    async def _acall_with_usage(
        self,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, int]:
        """Single async API call; returns (response_text, completion_tokens)."""
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.sampling_params.get("temperature", 0.1),
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        extra: dict[str, Any] = {}
        if "top_k" in self.sampling_params:
            extra["top_k"] = self.sampling_params["top_k"]
        if "repetition_penalty" in self.sampling_params:
            extra["repetition_penalty"] = self.sampling_params["repetition_penalty"]
        if extra:
            kwargs["extra_body"] = extra
        t0 = time.monotonic()
        try:
            response = await self.async_client.chat.completions.create(**kwargs)
            seconds = time.monotonic() - t0
            text = response.choices[0].message.content or ""
            completion_tokens = response.usage.completion_tokens if response.usage else 0
            log.info(
                "ocr_call_completed",
                seconds=round(seconds, 2),
                completion_tokens=completion_tokens,
                model=self.model_name,
            )
            return text, completion_tokens
        except Exception as e:
            seconds = time.monotonic() - t0
            log.warning("ocr_call_failed", seconds=round(seconds, 2), error=str(e))
            return "", 0

    def _call_batch_with_usage(
        self,
        message_sets: list[list[dict[str, Any]]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> list[tuple[str, int]]:
        """Dispatch all message sets concurrently; return (text, completion_tokens) pairs in order."""

        async def _run() -> list[tuple[str, int]]:
            sem = asyncio.Semaphore(self.max_concurrent)

            async def _limited(msgs: list[dict[str, Any]]) -> tuple[str, int]:
                async with sem:
                    return await self._acall_with_usage(msgs, temperature, max_tokens)

            tasks = [_limited(msgs) for msgs in message_sets]
            return list(await asyncio.gather(*tasks))

        return asyncio.run(_run())

    # -----------------------------------------------------------------------
    # PDF OCR pipeline
    # -----------------------------------------------------------------------

    def fit(self, filepaths: list[str]) -> list[str]:
        """
        OCR all pages of the provided PDF files and return the combined markdown text.

        Renders each PDF page as an image, sends it through the VLM with the
        OCR prompt, and reassembles page outputs into a single document per
        file. Pages that exceed max_tokens or fail to produce expected table
        tags are retried with progressively higher temperature (up to one
        retry round).

        Output text wraps each page in ``<page number="N">`` tags and numbers
        tables sequentially with ``<table number="N">`` tags.

        Args:
            filepaths: Paths to the PDF files to process.

        Returns:
            Processed markdown text, one string per document.
        """
        t_fit0 = time.monotonic()
        log.info("ocr_batch_started", documents=len(filepaths))

        paper_images: dict[int, list[str]] = {}
        for i, filepath in enumerate(filepaths):
            try:
                paper_images[i] = render_pdf_pages(filepath, target_longest_dim=_TARGET_LONGEST_DIM)
            except Exception as e:
                warnings.warn(f"Failed to process {filepath} with error: {e}. Skipping this file.")

        messages: list[list[dict[str, Any]]] = []
        message_paper_ids: list[int] = []
        for i in range(len(filepaths)):
            if i not in paper_images:
                continue
            for img in paper_images[i]:
                image_data_uri = f"data:image/png;base64,{img}"
                messages.append(
                    [
                        {"role": "system", "content": self.ocr_prompt},
                        {
                            "role": "user",
                            "content": [{"type": "image_url", "image_url": {"url": image_data_uri}}],
                        },
                    ]
                )
                message_paper_ids.append(i)

        results = self._call_batch_with_usage(messages)
        total_calls = len(messages)

        # Retry with higher temperature if max tokens exceeded or tables failed to be extracted.
        retry_round = 0
        temp = self.sampling_params.get("temperature", 0.1)
        while retry_round < _MAX_RETRY_ROUNDS and temp <= 1.0:
            retry_messages: list[list[dict[str, Any]]] = []
            retry_message_ids: list[int] = []
            for i, (text, completion_tokens) in enumerate(results):
                if completion_tokens >= self.max_tokens:
                    log.info("ocr_page_retry", reason="max_tokens_exceeded", message_index=i)
                    retry_messages.append(messages[i])
                    retry_message_ids.append(i)
                    continue

                front_matter_match = re.match(r"^---\s*\n([\s\S]*?)\n---", text)
                if front_matter_match:
                    try:
                        metadata = yaml.safe_load(front_matter_match.group(1))
                    except yaml.YAMLError:
                        metadata = {}

                    is_table = metadata.get("is_table", False)
                    content_after_front_matter = text[front_matter_match.end():]
                    has_table_tags = "<table" in content_after_front_matter.lower()

                    if is_table and not has_table_tags:
                        log.info("ocr_page_retry", reason="missing_table_tags", message_index=i)
                        retry_messages.append(
                            [
                                {
                                    "role": "system",
                                    "content": self.ocr_prompt + " Focus on accurately extracting tables in html format.",
                                },
                                messages[i][1],
                            ]
                        )
                        retry_message_ids.append(i)

            if not retry_messages:
                break
            log.info("ocr_retry_round", count=len(retry_messages), temperature=round(temp + 0.2, 1))
            temp += 0.2
            retry_round += 1
            retry_results = self._call_batch_with_usage(retry_messages, temperature=temp)
            total_calls += len(retry_messages)
            for i, result in enumerate(retry_results):
                results[retry_message_ids[i]] = result

        response_texts = [text for text, _ in results]
        documents: list[dict[str, str]] = [{} for _ in range(len(filepaths))]
        for i, text in enumerate(response_texts):
            paper_id = message_paper_ids[i]
            page_id = str(len(documents[paper_id]))
            cleaned_text = re.sub(r"^---[\s\S]*?---\s*", "", text)
            documents[paper_id][page_id] = cleaned_text

        texts: list[str] = []
        for document in documents:
            doc_text = ""
            pages = list(document.keys())
            pages.sort(key=lambda x: int(x))
            for page_id in pages:
                chunk = document[page_id]
                doc_text += f'<page number="{int(page_id)}">\n\n' + chunk + "\n\n</page>\n\n"

            counter = count()
            doc_text = re.sub(r"<table>", lambda m: f'<table number="{next(counter) + 1}">', doc_text)

            texts.append(doc_text)

        total_completion_tokens = sum(completion_tokens for _, completion_tokens in results)
        log.info(
            "ocr_batch_summary",
            documents=len(filepaths),
            pages=len(messages),
            calls=total_calls,
            completion_tokens=total_completion_tokens,
            seconds=round(time.monotonic() - t_fit0, 2),
        )

        return texts
