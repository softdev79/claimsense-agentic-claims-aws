"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition: Amazon Bedrock) · based on ClaimSense by Shivank Agrawal
File   : app/llm.py
Purpose: The ONLY file that talks to the LLM. Every call returns (result, usage) so we can show
         the exact cost of processing each claim — a number every client asks for.

Capstone change: the model now runs on Amazon Bedrock through its OpenAI-compatible endpoint
(OPENAI_BASE_URL = https://bedrock-mantle.<region>.api.aws/v1). Tested on that endpoint:
  * only gpt-oss models support /v1/responses  -> we use /v1/chat/completions (all models support it)
  * no model reads a PDF sent as a file        -> we extract the PDF text locally with pypdf
  * no embedding model is offered              -> policy search uses local BM25 (app/policy_rag.py)

Helpers:
  parse() -> structured output (a Pydantic object)
  write() -> free text (used for the customer letter)
"""

import json
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from app import config


@lru_cache
def client() -> OpenAI:
    """Created on first use. Reads OPENAI_API_KEY, OPENAI_BASE_URL and OPENAI_PROJECT_ID from the environment."""
    return OpenAI()


def _usage(step: str, model: str, input_tokens: int, output_tokens: int) -> dict:
    """Token counts -> cost in rupees, tagged with the graph step that spent it."""
    price_in, price_out = config.PRICES_USD_PER_1M.get(model, (0.0, 0.0))
    cost_usd = (input_tokens * price_in + output_tokens * price_out) / 1_000_000
    return {"step": step, "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cost_inr": round(cost_usd * config.USD_TO_INR, 4)}


def _usage_from(step: str, response) -> dict:
    u = response.usage
    return _usage(step, config.OPENAI_MODEL, u.prompt_tokens if u else 0, u.completion_tokens if u else 0)


def pdf_part(path: Path) -> dict:
    """
    Turn a PDF into text the model can read. pypdf keeps each table row on one line, which is
    enough for the survey's assessment table. Scanned (image-only) PDFs would need OCR (e.g. Amazon Textract).
    """
    pages = [page.extract_text() or "" for page in PdfReader(path).pages]
    text = "\n\n".join(f"--- page {i} ---\n{t.strip()}" for i, t in enumerate(pages, 1))
    return text_part(f"DOCUMENT: {path.name}\n{text}")


def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def _json_from(text: str) -> str:
    """Some open models wrap JSON in chatter ('Sure{...}', '**{...}**'): keep the outermost {...}."""
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def parse(step: str, instructions: str, content: list[dict], schema: type[BaseModel]) -> tuple[BaseModel, dict]:
    """
    Ask the model for an answer that MUST match `schema` (structured outputs via JSON schema).
    temperature=0 -> same document gives (almost) the same extraction every time.
    Fallback: if the model wraps its JSON in extra text (the SDK then raises), ask again without the
    strict parser, with the schema in the prompt, and clean the reply ourselves.
    """
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": content}]
    try:
        response = client().chat.completions.parse(
            model=config.OPENAI_MODEL, messages=messages, response_format=schema, temperature=0)
        message = response.choices[0].message
        parsed = message.parsed or schema.model_validate_json(_json_from(message.content or ""))
        return parsed, _usage_from(step, response)
    except ValidationError:
        pass

    schema_hint = json.dumps(schema.model_json_schema())
    fallback = [{"role": "system", "content": f"{instructions}\n\nReply with ONLY a JSON object (no prose, "
                                              f"no markdown) that matches this JSON schema:\n{schema_hint}"},
                {"role": "user", "content": content}]
    response = client().chat.completions.create(model=config.OPENAI_MODEL, messages=fallback, temperature=0)
    parsed = schema.model_validate_json(_json_from(response.choices[0].message.content or ""))
    return parsed, _usage_from(step, response)


def write(step: str, instructions: str, text: str) -> tuple[str, dict]:
    """Plain-text generation (customer letters)."""
    response = client().chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "system", "content": instructions}, {"role": "user", "content": text}],
        temperature=0.3,
    )
    return (response.choices[0].message.content or "").strip(), _usage_from(step, response)
