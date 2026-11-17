"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/policy_rag.py
Purpose: Retrieval over the policy wording, so the coverage decision can quote the exact clause.

Design choices:
  * Chunk by CLAUSE, not by fixed 500-token windows. A clause is the unit a claims officer cites
    ("rejected under EXC-4"), so each chunk must be one complete clause with its ID.
  * Capstone change: BM25 keyword search instead of embeddings. The Bedrock OpenAI-compatible endpoint
    offers no embedding model, and with ~20 clauses a local BM25 ranking is instant, free and needs
    no extra service. A short synonym list bridges claimant wording ("Ola", "drunk") and policy wording
    ("hire or reward", "intoxicating liquor"). With thousands of documents you would switch to
    embeddings (e.g. Amazon Titan) in a vector store (OpenSearch, pgvector, S3 Vectors).
"""

import math
import re
from collections import Counter

from app import config

HEADING = re.compile(r"^## \[(?P<id>[A-Z0-9-]+)\] (?P<title>.+)$", re.MULTILINE)
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = set("""a an and are as at be by for from has have in is it its of on or that the this to was were
with which who will shall not no any all such under than then there their they he she his her i my we our""".split())
# claimant / surveyor words -> words the policy uses
SYNONYMS = {
    "drunk": "intoxicating liquor alcohol influence", "alcohol": "intoxicating liquor influence",
    "breath": "intoxicating liquor alcohol", "ola": "hire reward commercial", "uber": "hire reward commercial",
    "taxi": "hire reward commercial", "cab": "hire reward commercial", "ride": "hire reward commercial",
    "passengers": "hire reward", "fare": "hire reward", "licence": "licence driving licence valid",
    "expired": "licence valid", "engine": "mechanical breakdown wear", "breakdown": "mechanical breakdown wear",
    "seized": "mechanical breakdown wear", "flood": "flood inundation", "theft": "theft burglary",
    "fire": "fire explosion", "lapsed": "period insurance", "police": "police report fir",
}
_K1, _B = 1.5, 0.75
_index_cache = None


def _tokens(text: str) -> list[str]:
    words = [w for w in WORD.findall(text.lower()) if w not in STOPWORDS]
    return words + [s for w in words for s in SYNONYMS.get(w, "").split()]


def load_clauses() -> list[dict]:
    """Split policy_wording.md into clauses: [{"id": "EXC-4", "title": ..., "text": ...}, ...]."""
    text = config.POLICY_WORDING_PATH.read_text(encoding="utf-8")
    matches = list(HEADING.finditer(text))
    clauses = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        clauses.append({"id": match["id"], "title": match["title"], "text": text[match.end():end].strip()})
    return clauses


def get_index():
    """Return (clauses, term counts per clause, document frequencies, average clause length)."""
    global _index_cache
    if _index_cache is None:
        clauses = load_clauses()
        docs = [Counter(_tokens(f"{c['title']} {c['title']} {c['text']}")) for c in clauses]  # title counts double
        df = Counter(term for doc in docs for term in doc)
        avg_len = sum(sum(d.values()) for d in docs) / len(docs)
        _index_cache = (clauses, docs, df, avg_len)
    return _index_cache


def retrieve(query: str, k: int = config.RETRIEVAL_TOP_K) -> tuple[list[dict], dict]:
    """Top-k clauses for the query by BM25 score (higher = more relevant)."""
    clauses, docs, df, avg_len = get_index()
    n = len(docs)
    scores = []
    for doc in docs:
        length, score = sum(doc.values()), 0.0
        for term in set(_tokens(query)):
            if term in doc:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                tf = doc[term]
                score += idf * tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * length / avg_len))
        scores.append(score)
    top = sorted(range(n), key=lambda i: -scores[i])[:k]
    usage = {"step": "coverage:bm25_search", "model": "bm25-local", "input_tokens": 0, "output_tokens": 0,
             "cost_inr": 0.0}
    return [{**clauses[i], "score": round(scores[i], 2)} for i in top], usage
