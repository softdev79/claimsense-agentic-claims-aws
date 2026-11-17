"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/graph.py
Purpose: The agent itself — a LangGraph state machine that moves one claim through these steps:

    intake -> policy_check -+-> coverage_check -> assess_amount -+-> fraud_check -> route_claim -+-> finalize
                            |                                   |                              |
                            +------ (policy failed) ------------+                              +-> human_review -> finalize
                                                                                                  (pauses here)

Why a graph and not one big "agent with tools" loop?
  Claims handling is a REGULATED, KNOWN process. The order of steps never changes, so we hard-code it.
  The LLM is used inside steps (read documents, judge coverage, spot contradictions), not to decide
  which step comes next. This makes every run predictable, testable and auditable.

Why LangGraph specifically?
  1. interrupt() lets the graph PAUSE for a human (adjuster / SIU) and RESUME days later.
  2. The SQLite checkpointer saves state after every step, so a paused claim survives a server restart.
  3. Conditional edges make the routing logic explicit and easy to draw for stakeholders.
"""

import json
import operator
import sqlite3
from functools import lru_cache
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app import config, db, rules
from app.intake import extract_documents
from app.judgments import check_consistency, check_coverage, write_letter


# ---------------------------------------------------------------------------
# State: everything we know about ONE claim. Each node reads it and returns only what it changes.
# ---------------------------------------------------------------------------
class ClaimState(TypedDict, total=False):
    claim_id: str
    docs_dir: str
    extracted: dict                 # {"claim_form": {...}, "survey": {...}, "fir": {...} | None}
    policy: dict | None             # row from the policies table
    policy_issues: list[dict]       # hard failures (expired policy, TP-only, expired licence...)
    coverage: dict | None           # LLM coverage decision with clause citations
    retrieved_clauses: list[dict]   # what the retriever showed the LLM (for the audit trail)
    assessment: dict | None         # depreciation / deductible / payable breakdown
    fraud: dict                     # {"score", "signals", "inconsistencies"}
    route: str                      # AUTO_APPROVE | ADJUSTER_REVIEW | SIU_INVESTIGATION
    recommendation: str             # APPROVE | REJECT
    reasons: list[str]
    human_decision: dict | None     # filled when an adjuster / SIU officer resumes the graph
    final_decision: str
    final_amount: float
    letter: str
    llm_usage: Annotated[list[dict], operator.add]   # every node APPENDS its token usage (reducer = list +)


def snapshot(state: dict) -> str:
    """Compact JSON of the state for the claims table, so the UI never has to read LangGraph internals."""
    usage = state.get("llm_usage", [])
    return json.dumps({
        "extracted": state.get("extracted"),
        "policy": state.get("policy"),
        "policy_issues": state.get("policy_issues", []),
        "coverage": state.get("coverage"),
        "retrieved_clauses": [{"id": c["id"], "title": c["title"], "score": c["score"]}
                              for c in state.get("retrieved_clauses", [])],
        "assessment": state.get("assessment"),
        "fraud": state.get("fraud"),
        "reasons": state.get("reasons", []),
        "human_decision": state.get("human_decision"),
        "llm_calls": len(usage),
        "tokens": sum(u["input_tokens"] + u["output_tokens"] for u in usage),
        "cost_inr": round(sum(u["cost_inr"] for u in usage), 4),
    }, default=str)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
def intake(state: ClaimState) -> dict:
    """LLM: read every PDF into typed JSON."""
    extracted, usages = extract_documents(state["docs_dir"])
    db.add_audit(state["claim_id"], "intake", "agent",
                 {"documents_read": [k for k, v in extracted.items() if v],
                  "policy_number": extracted["claim_form"]["policy_number"]})
    return {"extracted": extracted, "llm_usage": usages}


def policy_check(state: ClaimState) -> dict:
    """Python: is there a valid policy for this car, on this date, with own-damage cover and a licensed driver?"""
    form = state["extracted"]["claim_form"]
    policy = db.get_policy(form["policy_number"])
    issues = rules.check_policy(policy, form)
    db.save_claim(state["claim_id"], policy_number=form["policy_number"])
    db.add_audit(state["claim_id"], "policy_check", "agent", {"issues": issues})
    return {"policy": policy, "policy_issues": issues}


def coverage_check(state: ClaimState) -> dict:
    """RAG + LLM: does any exclusion in the policy wording apply?"""
    coverage, clauses, usages = check_coverage(state["extracted"])
    db.add_audit(state["claim_id"], "coverage_check", "agent",
                 {**coverage, "retrieved": [c["id"] for c in clauses]})
    return {"coverage": coverage, "retrieved_clauses": clauses, "llm_usage": usages}


def assess_amount(state: ClaimState) -> dict:
    """Python: depreciation, deductible, IDV cap, total loss -> payable amount."""
    assessment = rules.assess_amount(state["extracted"]["survey"]["items"], state["policy"],
                                     state["extracted"]["claim_form"]["loss_date"])
    db.add_audit(state["claim_id"], "assess_amount", "agent",
                 {k: v for k, v in assessment.items() if k != "lines"})
    return {"assessment": assessment}


def fraud_check(state: ClaimState) -> dict:
    """LLM finds contradictions between documents, Python adds up all red flags into a score."""
    extracted, policy = state["extracted"], state["policy"]
    form = extracted["claim_form"]
    inconsistencies, usage = check_consistency(extracted)
    prior_claims = db.count_prior_claims(policy["policy_number"], form["loss_date"]) if policy else 0
    fraud = rules.fraud_score(policy, form, extracted["survey"], extracted["fir"], prior_claims,
                              db.get_garage(form["garage_id"]), inconsistencies)
    fraud["inconsistencies"] = inconsistencies
    db.add_audit(state["claim_id"], "fraud_check", "agent", fraud)
    return {"fraud": fraud, "llm_usage": [usage]}


def route_claim(state: ClaimState) -> dict:
    """Python: decide who handles the claim next, and record it so the right queue can see it."""
    decision = rules.decide_route(state.get("policy_issues", []), state.get("coverage"),
                                  state.get("assessment"), state["fraud"])
    new_state = {**state, **decision}
    status = {"AUTO_APPROVE": "PROCESSING", "ADJUSTER_REVIEW": "PENDING_ADJUSTER",
              "SIU_INVESTIGATION": "PENDING_SIU"}[decision["route"]]
    db.save_claim(state["claim_id"], status=status, route=decision["route"],
                  recommendation=decision["recommendation"], fraud_score=state["fraud"]["score"],
                  payable_amount=(state.get("assessment") or {}).get("payable"),
                  details_json=snapshot(new_state))
    db.add_audit(state["claim_id"], "route_claim", "agent", decision)
    return decision


def human_review(state: ClaimState) -> dict:
    """
    PAUSE the graph until an adjuster / SIU officer decides (via the API or portal).
    Important LangGraph detail: on resume this node runs again FROM THE TOP, and interrupt() then
    returns the human's answer. So never put side effects (DB writes, emails) BEFORE interrupt().
    """
    decision = interrupt({
        "claim_id": state["claim_id"],
        "queue": "SIU" if state["route"] == "SIU_INVESTIGATION" else "ADJUSTER",
        "ai_recommendation": state["recommendation"],
        "payable": (state.get("assessment") or {}).get("payable"),
        "fraud_score": state["fraud"]["score"],
        "reasons": state["reasons"],
    })
    db.add_audit(state["claim_id"], "human_review", decision["reviewer"], decision)
    return {"human_decision": decision}


def finalize(state: ClaimState) -> dict:
    """Record the final decision and write the customer letter (LLM writes words, Python gave the numbers)."""
    human = state.get("human_decision")
    if human:
        decision, actor = human["decision"], human["reviewer"]
        amount = human["approved_amount"] if decision == "APPROVE" else 0
    else:
        decision, actor, amount = "APPROVE", "agent (auto-approve)", state["assessment"]["payable"]

    letter, usage = write_letter(state, decision, amount)
    final = {"final_decision": decision, "final_amount": amount, "letter": letter}
    db.save_claim(state["claim_id"], status="APPROVED" if decision == "APPROVE" else "REJECTED",
                  final_decision=decision, final_amount=amount, reviewer=actor, letter=letter,
                  details_json=snapshot({**state, **final, "llm_usage": state.get("llm_usage", []) + [usage]}))
    db.add_audit(state["claim_id"], "finalize", actor, {"decision": decision, "amount": amount})
    return {**final, "llm_usage": [usage]}


# ---------------------------------------------------------------------------
# Conditional edges (the routing logic, visible in one place)
# ---------------------------------------------------------------------------
def after_policy_check(state: ClaimState) -> str:
    # A lapsed / TP-only / unlicensed claim is already a REJECT recommendation:
    # skip the coverage LLM call and the amount calculation (saves time and money).
    return "fraud_check" if state["policy_issues"] else "coverage_check"


def after_route(state: ClaimState) -> str:
    return "finalize" if state["route"] == "AUTO_APPROVE" else "human_review"


def build_graph() -> StateGraph:
    graph = StateGraph(ClaimState)
    graph.add_node("intake", intake)
    graph.add_node("policy_check", policy_check)
    graph.add_node("coverage_check", coverage_check)
    graph.add_node("assess_amount", assess_amount)
    graph.add_node("fraud_check", fraud_check)
    graph.add_node("route_claim", route_claim)
    graph.add_node("human_review", human_review)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "policy_check")
    graph.add_conditional_edges("policy_check", after_policy_check, ["coverage_check", "fraud_check"])
    graph.add_edge("coverage_check", "assess_amount")
    graph.add_edge("assess_amount", "fraud_check")
    graph.add_edge("fraud_check", "route_claim")
    graph.add_conditional_edges("route_claim", after_route, ["finalize", "human_review"])
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph


@lru_cache
def get_app():
    """Compiled graph with a SQLite checkpointer (one shared instance per process)."""
    conn = sqlite3.connect(config.CHECKPOINT_DB_PATH, check_same_thread=False)
    return build_graph().compile(checkpointer=SqliteSaver(conn))
