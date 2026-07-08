"""ExtractionLM — single-call direct measurement extraction.

Native port of scholarlm's ``MeasurementLMAblation1`` ablation: rather than
running a multi-step pipeline (entity extraction, provenance, attribute
detection, value extraction, standardize, deduplicate), this makes a single
LLM call per document that extracts a flat list of (entity, event, attribute,
value, units) records directly. No provenance fields (page_number,
table_number, etc.) are produced — each record's ``context`` field is the
full OCR'd document text.

Implemented as a standalone class rather than inheriting scholarlm's full
``MeasurementLM`` base, since ablation1 only ever uses the base class's
``_acall``/``_call_batch`` async-dispatch helpers — not the entity/provenance/
table-parsing machinery that pipeline needs.
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any, Callable

from openai import AsyncOpenAI
from pydantic import BaseModel, create_model

DIRECT_TRIPLE_EXTRACTION_INSTRUCTIONS = """You are an expert in data extraction for systematic scientific literature reviews. Your task is to extract a complete list of measurement records from a research paper document in a single pass. Each record captures an entity, an attribute for measurement, the conditions of a specific measurement event, and its value.

Guidelines:
- You will be provided with dataset-specific extraction instructions describing the entities to identify, the target attributes, and the measurement event fields, along with the full document text.
- Identify all entities of the specified type present in the document, following the entity identification rules in the dataset-specific instructions.
- For each identified entity, identify all distinct measurement events and all attributes for which a direct numerical measurement is reported.
- Return one item per (entity, attribute, event) combination where a direct numerical measurement exists.
- Only include items where a direct numerical measurement is reported — omit absent data, model parameters, goodness-of-fit statistics, and qualitative descriptions.
- Extract the value exactly as it appears in the document — do not convert, round, or modify it.
- Do not include uncertainty measures, confidence intervals, or range bounds in the value field.
- If there are multiple types of values reported (e.g., mean, min, max), extract the mean or central value unless the attribute description directs otherwise.
- Give the value only in the value field; do not include any units, descriptors, or explanation there.
- For units, use the best fitting option from the attribute's listed preferred units if possible; otherwise specify the unit exactly as it appears in the text. Set units to null if no units are reported.
- Do NOT infer, guess, or derive any field value. If a field is not explicitly stated in the document, set it to null.
- Structure your response as a JSON object with an "items" list, where each item contains the entity fields, event fields, and "attribute", "value", and "units" fields as specified in the dataset-specific instructions.
"""


def response_validator(response_structure: type[BaseModel], response: str) -> dict[str, Any]:
    """Validate and parse an LLM JSON response against a pydantic model.

    Strips any leading prose or markdown fences before the JSON object/array —
    some models prepend text like "Here is the JSON:" even when
    response_format is set; raw_decode stops at the first complete top-level
    value.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(response):
        if ch in ("{", "["):
            try:
                obj, _ = decoder.raw_decode(response, i)
                response = json.dumps(obj)
                break
            except json.JSONDecodeError:
                continue
    pyd = response_structure.model_validate_json(response)
    return pyd.model_dump()


class ExtractionLM:
    """
    Single-call direct measurement extraction.

    Args:
        model_name: The name or path of the model, served via a vLLM
            OpenAI-compatible endpoint.
        direct_extraction_schema: Flat pydantic model combining entity fields,
            measurement event fields, and "attribute", "value", "units"
            fields — one item per (entity, event, attribute) record.
        direct_extraction_prompt: Dataset-specific instructions describing
            the entities, measurement events, and attributes to extract, in a
            single combined block.
        sampling_params: Sampling parameters for text generation.
        api_base: Base URL of the vLLM OpenAI-compatible endpoint.
        api_key: API key for the endpoint (use "EMPTY" for local vLLM).
        max_concurrent: Maximum number of concurrent async API calls.
        use_extra_body: Whether to forward top_k/repetition_penalty/
            enable_thinking via extra_body (vLLM-specific sampling knobs not
            part of the OpenAI API surface).
    """

    def __init__(
        self,
        model_name: str,
        direct_extraction_schema: type[BaseModel],
        direct_extraction_prompt: str,
        sampling_params: dict[str, Any] | None = None,
        api_base: str | None = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        max_concurrent: int = 1,
        use_extra_body: bool = True,
    ):
        self.model_name = model_name
        self.direct_extraction_schema = direct_extraction_schema
        self.direct_extraction_prompt = direct_extraction_prompt
        # Deliberate deviation from scholarlm's original merge (which used a
        # mutable dict default and a dead `is None` check that could never
        # trigger): merge onto real defaults so an empty/partial override
        # dict behaves as documented.
        self.sampling_params: dict[str, Any] = {
            "temperature": 0.90,
            "top_p": 0.95,
            "top_k": 64,
            "repetition_penalty": 1.0,
            "max_tokens": 2048,
            "enable_thinking": False,
        } | (sampling_params or {})
        self.max_concurrent = max_concurrent
        self.use_extra_body = use_extra_body
        self.max_prompt_tokens: int = 0
        self.async_client = AsyncOpenAI(api_key=api_key, base_url=api_base, timeout=2400.0)

    # -----------------------------------------------------------------------
    # Core API call helpers
    # -----------------------------------------------------------------------

    async def _acall(
        self,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 600.0,
    ) -> str:
        """Single async API call to the vLLM OpenAI-compatible endpoint."""
        # Frontier models may require 'max_completion_tokens' (OpenAI o-series / gpt-5+)
        # instead of 'max_tokens'. Detect the right key from sampling_params so the
        # caller's explicit max_tokens value is forwarded under the correct parameter name.
        _TOKEN_KEYS = ("max_completion_tokens", "max_tokens")
        token_param = next((k for k in _TOKEN_KEYS if k in self.sampling_params), "max_tokens")
        token_value = max_tokens if max_tokens is not None else self.sampling_params.get(token_param, 2048)
        # Some frontier models (e.g. gpt-5-mini) reject temperature/top_p entirely.
        # Only include them when explicitly provided or present in sampling_params.
        effective_temp = temperature if temperature is not None else self.sampling_params.get("temperature")
        effective_top_p = self.sampling_params.get("top_p")
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            token_param: token_value,
        }
        if effective_temp is not None:
            kwargs["temperature"] = effective_temp
        if effective_top_p is not None:
            kwargs["top_p"] = effective_top_p
        if response_format is not None:
            kwargs["response_format"] = response_format
        if self.use_extra_body:
            extra: dict[str, Any] = {}
            if "top_k" in self.sampling_params:
                extra["top_k"] = self.sampling_params["top_k"]
            if "repetition_penalty" in self.sampling_params:
                extra["repetition_penalty"] = self.sampling_params["repetition_penalty"]
            if "enable_thinking" in self.sampling_params:
                extra["chat_template_kwargs"] = {"enable_thinking": self.sampling_params["enable_thinking"]}
            if extra:
                kwargs["extra_body"] = extra
        try:
            response = await self.async_client.chat.completions.create(**kwargs, timeout=timeout)
            if response.usage is not None and response.usage.prompt_tokens:
                if response.usage.prompt_tokens > self.max_prompt_tokens:
                    self.max_prompt_tokens = response.usage.prompt_tokens
            return response.choices[0].message.content or ""
        except Exception as e:
            print(f"API call failed: {e}")
            return ""

    def _call_batch(
        self,
        message_sets: list[list[dict[str, Any]]],
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 2,
        validator: Callable[[str], Any] | None = None,
        timeout: float = 600.0,
        max_concurrent: int | None = None,
    ) -> list[str]:
        """Dispatch all message sets concurrently; return response texts in order.

        If max_retries > 0, any response that is empty or causes validator to raise
        is retried up to max_retries times with exponential backoff between rounds.
        validator is called only to detect failure — its return value is ignored.
        max_concurrent overrides self.max_concurrent for this call only, allowing
        per-step concurrency tuning without changing the instance default.
        """

        async def _run() -> list[str]:
            sem = asyncio.Semaphore(max_concurrent if max_concurrent is not None else self.max_concurrent)

            async def _limited(msgs: list[dict[str, Any]]) -> str:
                async with sem:
                    return await self._acall(msgs, response_format, temperature, max_tokens, timeout)

            results = list(await asyncio.gather(*[_limited(msgs) for msgs in message_sets]))

            for attempt in range(max_retries):
                failed = []
                for i, resp in enumerate(results):
                    if not resp:
                        failed.append(i)
                        continue
                    if validator is not None:
                        try:
                            validator(resp)
                        except Exception:
                            failed.append(i)

                if not failed:
                    break

                print(f"Retrying {len(failed)} failed responses (attempt {attempt + 1}/{max_retries})...")
                await asyncio.sleep(2**attempt)
                retried = await asyncio.gather(*[_limited(message_sets[i]) for i in failed])
                for local_i, global_i in enumerate(failed):
                    results[global_i] = retried[local_i]

            return results

        return asyncio.run(_run())

    # -----------------------------------------------------------------------
    # Single extraction step: extract all records directly
    # -----------------------------------------------------------------------

    def _extract_triples(self) -> list[dict[str, Any]]:
        """
        Extract all measurement records from each document in a single LLM call.

        Returns a list of records — no provenance fields (page_number,
        table_number, etc.) are produced.
        """
        schema = self.direct_extraction_schema
        DirectExtractionList = create_model(
            "DirectExtractionList",
            items=(list[schema], ...),  # type: ignore[valid-type]
        )
        direct_extraction_list_json = DirectExtractionList.model_json_schema()

        messages: list[list[dict[str, Any]]] = []
        for datapoint in self.data:
            context = datapoint["context"]
            query = "Extract all measurement records from this document as described in the instructions."
            prompt = (
                f"## INSTRUCTIONS:\n{DIRECT_TRIPLE_EXTRACTION_INSTRUCTIONS}\n\n"
                f"## DATASET SPECIFIC INSTRUCTIONS:\n{self.direct_extraction_prompt}\n\n"
                f"## CONTEXT:\n{context}\n\n## QUERY:\n{query}"
            )
            messages.append([{"role": "user", "content": prompt}])

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "direct_extraction_list",
                "schema": direct_extraction_list_json,
            },
        }
        response_texts = self._call_batch(
            messages,
            response_format=response_format,
            max_tokens=32768,
            max_retries=4,
            max_concurrent=1,
            validator=partial(response_validator, DirectExtractionList),
            timeout=600.0,
        )

        triple_data: list[dict[str, Any]] = []
        for i, r in enumerate(response_texts):
            try:
                resp_validated = response_validator(DirectExtractionList, r)
            except Exception as e:
                print(f"Validation error in direct extraction response: {e}")
                print(f"Response text: {r}")
                resp_validated = {"items": []}

            for j, item in enumerate(resp_validated["items"]):
                if item.get("value") is None:
                    continue
                entity_id = f"doc_{i}_entity_{j}"
                triple_data.append(self.data[i] | item | {"entity_id": entity_id, "attribute_terms": []})

        return triple_data

    # -----------------------------------------------------------------------
    # Full pipeline (single extraction step)
    # -----------------------------------------------------------------------

    def fit(self, documents: list[str]) -> list[dict[str, Any]]:
        """Run direct extraction on the provided documents.

        Args:
            documents: OCR text strings, one per document.

        Returns:
            Measurement records extracted from the documents.
        """
        self.data: list[dict[str, Any]] = [{"document_id": i, "context": doc} for i, doc in enumerate(documents)]
        self.data = self._extract_triples()
        return self.data
