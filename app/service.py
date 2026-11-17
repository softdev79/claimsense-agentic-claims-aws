"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/service.py
Purpose: The thin layer that the API, the CLI scripts and the evals all call.
         Keeping this separate means the same logic runs everywhere (no copy-paste between UI and API).
"""

import uuid
from pathlib import Path

from langgraph.types import Command

from app import config, db
from app.graph import get_app
from app.schemas import ReviewDecision


def describe(node: str, update: dict) -> str:
    """One readable line per graph step (used by the CLI to show the agent 'thinking')."""
    if node == "__interrupt__":
        payload = update[0].value
        return f"PAUSED - waiting for {payload['queue']} decision (AI recommends {payload['ai_recommendation']})"
    if node == "intake":
        docs = [k for k, v in update["extracted"].items() if v]
        form = update["extracted"]["claim_form"]
        return f"read {len(docs)} documents {docs} | policy {form['policy_number']} | accident {form['loss_date']}"
    if node == "policy_check":
        issues = update["policy_issues"]
        return "policy OK" if not issues else "ISSUES: " + "; ".join(i["code"] for i in issues)
    if node == "coverage_check":
        c = update["coverage"]
        clauses = [x["id"] for x in update["retrieved_clauses"]]
        return (f"covered={c['covered']} exclusions={c['exclusion_clause_ids']} confidence={c['confidence']} "
                f"| retrieved {clauses}")
    if node == "assess_amount":
        a = update["assessment"]
        return (f"gross Rs.{a['gross_assessed']:,.0f} - depreciation Rs.{a['total_depreciation']:,.0f} "
                f"- deductible Rs.{a['deductible']:,} = payable Rs.{a['payable']:,}"
                + (" | TOTAL LOSS" if a["total_loss"] else ""))
    if node == "fraud_check":
        f = update["fraud"]
        return f"fraud score {f['score']} | signals {[s['code'] for s in f['signals']] or 'none'}"
    if node == "route_claim":
        return f"{update['route']} (AI recommends {update['recommendation']})"
    if node == "human_review":
        h = update["human_decision"]
        return f"{h['reviewer']} decided {h['decision']} Rs.{h['approved_amount']:,.0f}"
    if node == "finalize":
        return f"FINAL {update['final_decision']} Rs.{update['final_amount']:,.0f} | letter written"
    return ""


def process_claim(claim_id: str, docs_dir: str | Path, verbose: bool = False) -> dict:
    """Run a claim through the graph until it finishes (auto-approve) or pauses (human review)."""
    db.reset_claim(claim_id)                              # re-processing starts a clean run
    thread_id = f"{claim_id}-{uuid.uuid4().hex[:8]}"     # one LangGraph thread per run
    db.save_claim(claim_id, docs_dir=str(docs_dir), thread_id=thread_id, status="PROCESSING")
    db.add_audit(claim_id, "received", "agent", {"docs_dir": str(docs_dir)})

    graph_config = {"configurable": {"thread_id": thread_id}}
    try:
        for step in get_app().stream({"claim_id": claim_id, "docs_dir": str(docs_dir), "llm_usage": []},
                                     graph_config, stream_mode="updates"):
            for node, update in step.items():
                if verbose:
                    print(f"  [{node:<14}] {describe(node, update)}")
    except Exception as error:   # any failure -> claim goes to FAILED with the reason, never silently lost
        db.save_claim(claim_id, status="FAILED", error=f"{type(error).__name__}: {error}")
        db.add_audit(claim_id, "error", "agent", {"error": str(error)})
        if verbose:
            print(f"  [error         ] {type(error).__name__}: {error}")
    return db.get_claim(claim_id)


def review_claim(claim_id: str, review: ReviewDecision, verbose: bool = False) -> dict:
    """Resume a paused claim with the adjuster's / SIU officer's decision."""
    claim = db.get_claim(claim_id)
    if claim is None:
        raise ValueError(f"Claim {claim_id} not found")
    if claim["status"] not in ("PENDING_ADJUSTER", "PENDING_SIU"):
        raise ValueError(f"Claim {claim_id} is {claim['status']}, not waiting for review")

    idv = (claim["details"].get("policy") or {}).get("idv")
    if review.decision == "APPROVE" and idv and review.approved_amount > idv:
        raise ValueError(f"Approved amount cannot exceed the IDV of Rs.{idv:,.0f}")
    if review.decision != claim["recommendation"] and not review.notes.strip():
        raise ValueError("Notes are required when overriding the AI recommendation")

    graph_config = {"configurable": {"thread_id": claim["thread_id"]}}
    for step in get_app().stream(Command(resume=review.model_dump()), graph_config, stream_mode="updates"):
        for node, update in step.items():
            if verbose:
                print(f"  [{node:<14}] {describe(node, update)}")
    return db.get_claim(claim_id)


def save_upload(files: dict[str, bytes]) -> tuple[str, Path]:
    """Store uploaded PDFs as data/uploads/<claim_id>/<claim_form|survey_report|fir>.pdf."""
    claim_id = f"CLM-U{uuid.uuid4().hex[:6].upper()}"
    folder = config.UPLOADS_DIR / claim_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / f"{name}.pdf").write_bytes(content)
    return claim_id, folder


def sample_claim_dir(claim_id: str) -> Path:
    folder = config.SAMPLE_CLAIMS_DIR / claim_id
    if not folder.exists():
        raise ValueError(f"No sample claim {claim_id}")
    return folder
