"""
ClaimSense AWS capstone - which Bedrock model can run ClaimSense?
Author: Vishal Gupta

Uses the Chat Completions API (/v1/chat/completions): on Bedrock's OpenAI-compatible endpoint only the
gpt-oss models support /v1/responses, while every model supports chat completions.

Runs the SAME kinds of calls ClaimSense makes, against each candidate model, on a real sample claim:
  1. pdf+schema : read claim_form.pdf (sent as a file) into the ClaimForm schema   -> intake step
  2. text+schema: same document as plain text (pypdf) into the schema             -> fallback if (1) fails
  3. letter     : free-text generation                                             -> finalize step
plus one embeddings test (policy-clause search).

Needs in .env: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_PROJECT_ID (your Bedrock OpenAI-compatible endpoint).

Usage (project root, venv active):
    python -m scripts.check_bedrock_models
    python -m scripts.check_bedrock_models openai.gpt-5.4 anthropic.claude-haiku-4-5
Costs a fraction of a US cent per model.
"""

import base64
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
from app.schemas import ClaimForm  # noqa: E402  (after load_dotenv)

DEFAULT_MODELS = [
    "openai.gpt-5.4",                     # OpenAI GPT-5.4 family: closest to the original gpt-4.1-mini design
    "openai.gpt-5.6-luna",
    "anthropic.claude-haiku-4-5",         # fast + cheap, strong at documents
    "qwen.qwen3-vl-235b-a22b-instruct",   # vision-language open model
    "openai.gpt-oss-120b",                # text-only open-weight model (used in your course notebooks)
    "mistral.ministral-3-14b-instruct",   # small, cheap
]
EMBEDDING_CANDIDATES = ["amazon.titan-embed-text-v2:0", "text-embedding-3-small", "cohere.embed-v4:0"]

# Ground truth for CLM-1001 (from scripts/generate_data.py)
PDF = Path("data/sample_claims/CLM-1001/claim_form.pdf")
EXPECTED = {"policy_number": "NS-MOT-240101", "claimant_name": "Ananya Rao", "vehicle_reg": "KA-03-MN-4521"}
INSTRUCTIONS = "You extract fields from Indian motor-insurance claim documents. Copy values exactly as written."


def client() -> OpenAI:
    missing = [n for n in ("OPENAI_API_KEY", "OPENAI_BASE_URL") if not os.getenv(n)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    # OpenAI() also reads OPENAI_BASE_URL and OPENAI_PROJECT_ID from the environment automatically
    return OpenAI(project=os.getenv("OPENAI_PROJECT_ID"))


def pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)


def score(parsed: ClaimForm) -> str:
    got = parsed.model_dump()
    ok = sum(got[k].strip().lower() == v.lower() for k, v in EXPECTED.items())
    return f"{ok}/{len(EXPECTED)} key fields correct (garage_estimate={got['garage_estimate']:.0f})"


def run(fn) -> str:
    start = time.time()
    try:
        detail = fn()
        return f"PASS {time.time() - start:5.1f}s  {detail}"
    except Exception as exc:  # report and continue with the next test
        return f"FAIL {time.time() - start:5.1f}s  {type(exc).__name__}: {str(exc)[:160]}"


def check_model(c: OpenAI, model: str, text: str) -> None:
    data = base64.b64encode(PDF.read_bytes()).decode()
    pdf_part = {"type": "file", "file": {"filename": PDF.name, "file_data": f"data:application/pdf;base64,{data}"}}
    system = {"role": "system", "content": INSTRUCTIONS}

    def parse(content) -> str:
        messages = [system, {"role": "user", "content": content}]
        note = ""
        try:
            r = c.chat.completions.parse(model=model, messages=messages, response_format=ClaimForm, temperature=0)
        except Exception as exc:
            if "temperature" not in str(exc).lower():
                raise
            # reasoning models reject temperature -> app/llm.py must drop it for them
            r = c.chat.completions.parse(model=model, messages=messages, response_format=ClaimForm)
            note = "  [needs temperature removed]"
        parsed = r.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"no structured output; model said: {(r.choices[0].message.content or '')[:100]!r}")
        return score(parsed) + note

    def letter() -> str:
        r = c.chat.completions.create(model=model, temperature=0.3, messages=[
            {"role": "user", "content": "Write a two-sentence claim approval note for Ananya Rao."}])
        return f"{len((r.choices[0].message.content or '').split())} words"

    print(f"\n=== {model}")
    print("  pdf+schema :", run(lambda: parse([pdf_part, {"type": "text", "text": "Extract the fields."}])))
    if text:
        print("  text+schema:", run(lambda: parse("Extract the fields from this document:\n\n" + text)))
    else:
        print("  text+schema: skipped (pip install pypdf to test the text-extraction fallback)")
    print("  letter     :", run(letter))


def check_embeddings(c: OpenAI) -> None:
    print("\n=== embeddings (policy-clause search)")
    for model in EMBEDDING_CANDIDATES:
        result = run(lambda m=model: f"dim={len(c.embeddings.create(model=m, input=['own damage cover']).data[0].embedding)}")
        print(f"  {model:32s}", result)


if __name__ == "__main__":
    c = client()
    text = pdf_text(PDF)
    for m in sys.argv[1:] or DEFAULT_MODELS:
        check_model(c, m, text)
    check_embeddings(c)
    print("\nPick the cheapest model with PASS and 3/3 on pdf+schema (or text+schema), and a PASS on letter.")
