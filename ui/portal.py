"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : ui/portal.py
Purpose: One Streamlit app with four role-based views:
           1. Claimant   -> submit a claim (upload PDFs or pick a sample) and see the outcome
           2. Adjuster   -> work queue of claims the AI routed for human review
           3. SIU        -> work queue of suspicious claims (Special Investigation Unit)
           4. Dashboard  -> all claims, straight-through rate, audit trail
         The UI only talks to the REST API (never to the database), exactly like a real front end.

Run (API must be running):
    streamlit run ui/portal.py
"""

import os

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
st.set_page_config(page_title="ClaimSense - Motor Claims", layout="wide")


def api(method: str, path: str, **kwargs):
    """Call the backend and show a readable error instead of a stack trace."""
    response = requests.request(method, f"{API_URL}{path}", timeout=180, **kwargs)
    if response.status_code >= 400:
        st.error(f"API error {response.status_code}: {response.json().get('detail', response.text)}")
        return None
    return response.json()


def rupees(value) -> str:
    return "-" if value is None else f"Rs.{value:,.0f}"


def show_outcome(claim: dict) -> None:
    """What the customer sees after submitting."""
    status = claim["status"]
    if status == "APPROVED":
        st.success(f"Claim {claim['claim_id']} approved for {rupees(claim['final_amount'])}")
    elif status == "REJECTED":
        st.error(f"Claim {claim['claim_id']} was not approved")
    elif status in ("PENDING_ADJUSTER", "PENDING_SIU"):
        st.info(f"Claim {claim['claim_id']} received. Our claims team is reviewing it and will contact you shortly.")
    else:
        st.warning(f"Claim {claim['claim_id']} could not be processed: {claim.get('error')}")
    if claim.get("letter"):
        st.text_area("Decision letter", claim["letter"], height=260)


# ---------------------------------------------------------------------------
# 1. Claimant view
# ---------------------------------------------------------------------------
def claimant_view():
    st.header("Submit a motor claim")
    tab_upload, tab_sample = st.tabs(["Upload documents", "Use a sample claim"])

    with tab_upload:
        claim_form = st.file_uploader("Claim form (PDF)", type="pdf")
        survey = st.file_uploader("Surveyor report (PDF)", type="pdf")
        fir = st.file_uploader("Police FIR (PDF, optional)", type="pdf")
        if st.button("Submit claim", disabled=not (claim_form and survey)):
            files = {"claim_form": (claim_form.name, claim_form.getvalue(), "application/pdf"),
                     "survey_report": (survey.name, survey.getvalue(), "application/pdf")}
            if fir:
                files["fir"] = (fir.name, fir.getvalue(), "application/pdf")
            with st.spinner("The AI agent is reading your documents..."):
                claim = api("POST", "/claims", files=files)
            if claim:
                show_outcome(claim)

    with tab_sample:
        samples = api("GET", "/claims/samples") or []
        sample = st.selectbox("Sample claim", samples)
        if st.button("Process sample claim"):
            with st.spinner(f"Processing {sample}..."):
                claim = api("POST", f"/claims/sample/{sample}")
            if claim:
                show_outcome(claim)


# ---------------------------------------------------------------------------
# 2 & 3. Reviewer views (adjuster and SIU share the same screen, different queue)
# ---------------------------------------------------------------------------
def claim_details(claim: dict):
    """Everything a reviewer needs to decide, on one screen."""
    d = claim["details"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("AI recommendation", claim["recommendation"])
    c2.metric("Payable (calculated)", rupees(claim["payable_amount"]))
    c3.metric("Fraud score", claim["fraud_score"])
    c4.metric("AI cost for this claim", f"Rs.{d.get('cost_inr', 0):.2f}")
    st.write("**Why it is in your queue:**")
    for reason in d.get("reasons", []):
        st.write(f"- {reason}")

    if d.get("policy_issues"):
        st.error("Policy issues: " + "; ".join(i["detail"] for i in d["policy_issues"]))
    if d.get("coverage"):
        cov = d["coverage"]
        (st.success if cov["covered"] else st.error)(
            f"Coverage: {'COVERED' if cov['covered'] else 'EXCLUDED ' + str(cov['exclusion_clause_ids'])} "
            f"(confidence {cov['confidence']}) - {cov['reasoning']}")
        st.caption("Clauses retrieved: " + ", ".join(f"{c['id']} ({c['score']})" for c in d["retrieved_clauses"]))

    if d.get("assessment"):
        a = d["assessment"]
        st.write(f"**Assessment** (vehicle age {a['vehicle_age_months']} months, zero-dep: {a['zero_dep']}, "
                 f"IDV {rupees(a['idv'])}{', TOTAL LOSS' if a['total_loss'] else ''})")
        st.dataframe(pd.DataFrame(a["lines"]), hide_index=True)
        st.write(f"Gross {rupees(a['gross_assessed'])} - depreciation {rupees(a['total_depreciation'])} "
                 f"- deductible {rupees(a['deductible'])} = **{rupees(a['payable'])}**")

    fraud = d.get("fraud") or {}
    if fraud.get("signals"):
        st.write("**Fraud signals**")
        st.dataframe(pd.DataFrame(fraud["signals"]), hide_index=True)

    with st.expander("Data extracted from the documents"):
        st.json(d.get("extracted"))


def review_view(queue_status: str, title: str):
    st.header(title)
    queue = api("GET", "/claims", params={"status": queue_status}) or []
    if not queue:
        st.info("Queue is empty. Process some sample claims from the Claimant view.")
        return
    st.dataframe(pd.DataFrame(queue)[["claim_id", "recommendation", "fraud_score", "payable_amount", "updated_at"]],
                 hide_index=True)
    claim_id = st.selectbox("Open claim", [c["claim_id"] for c in queue])
    claim = api("GET", f"/claims/{claim_id}")
    if not claim:
        return
    claim_details(claim)

    st.subheader("Your decision")
    with st.form(f"review-{claim_id}"):
        decision = st.radio("Decision", ["APPROVE", "REJECT"], horizontal=True,
                            index=0 if claim["recommendation"] == "APPROVE" else 1)
        amount = st.number_input("Amount to pay (Rs.)", min_value=0.0,
                                 value=float(claim["payable_amount"] or 0), step=500.0)
        reviewer = st.text_input("Your name", value="Asha Verma (Adjuster)" if queue_status == "PENDING_ADJUSTER"
                                 else "Rahul Nair (SIU)")
        notes = st.text_area("Note for the customer letter (required if you disagree with the AI)")
        if st.form_submit_button("Submit decision"):
            result = api("POST", f"/claims/{claim_id}/review",
                         json={"decision": decision, "approved_amount": amount if decision == "APPROVE" else 0,
                               "reviewer": reviewer, "notes": notes})
            if result:
                st.success(f"{claim_id} is now {result['status']}")
                st.text_area("Letter sent to the customer", result["letter"], height=240)


# ---------------------------------------------------------------------------
# 4. Dashboard
# ---------------------------------------------------------------------------
def dashboard_view():
    st.header("Claims operations dashboard")
    claims = api("GET", "/claims") or []
    if not claims:
        st.info("No claims yet.")
        return
    df = pd.DataFrame(claims)
    total = len(df)
    auto = int((df["route"] == "AUTO_APPROVE").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Claims processed", total)
    c2.metric("Straight-through (no human)", f"{auto / total:.0%}")
    c3.metric("Waiting for adjuster", int((df["status"] == "PENDING_ADJUSTER").sum()))
    c4.metric("With SIU", int((df["status"] == "PENDING_SIU").sum()))
    st.dataframe(df, hide_index=True)

    claim_id = st.selectbox("Audit trail for claim", df["claim_id"].tolist())
    claim = api("GET", f"/claims/{claim_id}")
    if claim:
        st.dataframe(pd.DataFrame(claim["audit_trail"]), hide_index=True)
        if claim.get("letter"):
            st.text_area("Customer letter", claim["letter"], height=220)


# ---------------------------------------------------------------------------
st.sidebar.title("ClaimSense")
st.sidebar.caption("Agentic motor claims - by Vishal Gupta · based on ClaimSense by Shivank Agrawal")
view = st.sidebar.radio("I am a...", ["Claimant", "Adjuster", "SIU investigator", "Claims manager (dashboard)"])
if view == "Claimant":
    claimant_view()
elif view == "Adjuster":
    review_view("PENDING_ADJUSTER", "Adjuster work queue")
elif view == "SIU investigator":
    review_view("PENDING_SIU", "Special Investigation Unit queue")
else:
    dashboard_view()
