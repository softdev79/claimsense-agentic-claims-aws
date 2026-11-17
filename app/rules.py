"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/rules.py
Purpose: Deterministic business rules. NO LLM in this file.

Why plain Python here?
  * Money must be exact and repeatable: the same claim must always give the same payable amount.
  * Regulators and auditors ask "why was this amount paid?" -> every rule below is explainable line by line.
  * LLMs are good at reading messy documents, bad at arithmetic and calibrated scores.
So the LLM extracts facts (app/intake.py) and Python decides numbers (this file).
"""

import re
from datetime import date

from app import config

# Depreciation rates for non-metal parts (policy clause DEP-1). Labour is never depreciated (OD-2).
CATEGORY_DEPRECIATION = {"plastic_rubber": 0.50, "fibre_glass": 0.30, "glass": 0.0, "labour": 0.0}

# Points added to the fraud score for each signal. Tuned by the fraud team, not by the LLM.
FRAUD_POINTS = {
    "EARLY_CLAIM": 25,                 # accident within 30 days of buying the policy
    "WATCHLIST_GARAGE": 25,            # garage has a history of inflated / staged claims
    "FIR_DATE_MISMATCH": 25,           # police report date differs from claim form date
    "REPEAT_CLAIMANT": 20,             # 2 or more claims on this policy in the last 12 months
    "SURVEYOR_DOUBTS_CAUSE": 20,       # surveyor says damage does not match the story
    "LATE_INTIMATION": 15,             # reported more than 7 days after the accident
    "ESTIMATE_INFLATED": 15,           # garage estimate far above surveyor's assessment
    "UNWITNESSED_NIGHT_ACCIDENT": 10,  # 10 pm - 5 am, no FIR, no witness
}
INCONSISTENCY_POINTS = {"high": 25, "medium": 10, "low": 0}  # from the LLM consistency check


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def to_date(value: str) -> date:
    """Convert 'YYYY-MM-DD' into a date object."""
    return date.fromisoformat(value.strip())


def normalise_reg(reg: str) -> str:
    """'ka-01 ab 1234' and 'KA-01-AB-1234' should be treated as the same car."""
    return re.sub(r"[^A-Z0-9]", "", reg.upper())


def months_between(start: date, end: date) -> int:
    """Whole months from start to end (used for vehicle age)."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def metal_depreciation_rate(vehicle_age_months: int) -> float:
    """Clause DEP-1: metal parts depreciate with the age of the car."""
    if vehicle_age_months <= 6:
        return 0.0
    if vehicle_age_months <= 12:
        return 0.05
    if vehicle_age_months <= 24:
        return 0.10
    if vehicle_age_months <= 36:
        return 0.15
    if vehicle_age_months <= 48:
        return 0.25
    if vehicle_age_months <= 60:
        return 0.35
    if vehicle_age_months <= 120:
        return 0.40
    return 0.50


# ---------------------------------------------------------------------------
# Step 1: Hard policy checks (crisp facts -> no LLM needed)
# ---------------------------------------------------------------------------
def check_policy(policy: dict | None, form: dict) -> list[dict]:
    """
    Returns a list of issues. An empty list means the policy itself is fine.
    Each issue = {"code", "clause", "detail"} so the adjuster sees exactly what failed.
    """
    if policy is None:
        return [{"code": "POLICY_NOT_FOUND", "clause": None,
                 "detail": f"No policy found with number {form['policy_number']}"}]

    issues = []
    loss_date = to_date(form["loss_date"])

    if normalise_reg(form["vehicle_reg"]) != normalise_reg(policy["vehicle_reg"]):
        issues.append({"code": "VEHICLE_MISMATCH", "clause": None,
                       "detail": f"Claim is for {form['vehicle_reg']} but policy covers {policy['vehicle_reg']}"})

    if not (to_date(policy["start_date"]) <= loss_date <= to_date(policy["end_date"])):
        issues.append({"code": "POLICY_NOT_ACTIVE", "clause": None,
                       "detail": f"Accident on {loss_date} is outside policy period "
                                 f"{policy['start_date']} to {policy['end_date']}"})

    if policy["policy_type"] == "THIRD_PARTY":
        issues.append({"code": "NO_OWN_DAMAGE_COVER", "clause": "TP-1",
                       "detail": "Liability-only policy: damage to the insured's own car is not covered"})

    if to_date(form["licence_valid_till"]) < loss_date:
        issues.append({"code": "LICENCE_EXPIRED", "clause": "EXC-5",
                       "detail": f"Driver's licence expired on {form['licence_valid_till']}, "
                                 f"before the accident on {loss_date}"})
    return issues


# ---------------------------------------------------------------------------
# Step 2: How much do we pay? (clauses OD-2, OD-3, DEP-1, DED-1, ADD-ZD)
# ---------------------------------------------------------------------------
def assess_amount(items: list[dict], policy: dict, loss_date_str: str) -> dict:
    """
    items  = surveyor's assessment lines [{"description", "category", "amount"}]
    Returns a full breakdown so the adjuster (and the claimant's letter) can show every rupee.
    """
    loss_date = to_date(loss_date_str)
    vehicle_age_months = months_between(to_date(policy["registration_date"]), loss_date)
    zero_dep = bool(policy["zero_dep"])

    lines = []
    for item in items:
        if zero_dep:
            rate = 0.0  # ADD-ZD: no depreciation on parts
        elif item["category"] == "metal":
            rate = metal_depreciation_rate(vehicle_age_months)
        else:
            rate = CATEGORY_DEPRECIATION[item["category"]]
        depreciation = round(item["amount"] * rate, 2)
        lines.append({**item, "dep_rate": rate, "depreciation": depreciation,
                      "net": round(item["amount"] - depreciation, 2)})

    gross = round(sum(line["amount"] for line in lines), 2)
    total_depreciation = round(sum(line["depreciation"] for line in lines), 2)
    deductible = 2000 if policy["engine_cc"] > 1500 else 1000   # DED-1
    idv = float(policy["idv"])

    # OD-3: repair cost above 75% of IDV -> constructive total loss, settled on IDV basis.
    total_loss = gross > config.TOTAL_LOSS_RATIO * idv
    if total_loss:
        payable = idv - deductible
    else:
        payable = min(max(gross - total_depreciation - deductible, 0), idv)

    return {
        "vehicle_age_months": vehicle_age_months,
        "zero_dep": zero_dep,
        "lines": lines,
        "gross_assessed": gross,
        "total_depreciation": total_depreciation,
        "deductible": deductible,
        "idv": idv,
        "total_loss": total_loss,
        "payable": round(payable),  # pay in whole rupees
    }


# ---------------------------------------------------------------------------
# Step 3: Fraud score (rules + LLM-found inconsistencies)
# ---------------------------------------------------------------------------
def fraud_score(policy: dict | None, form: dict, survey: dict, fir: dict | None,
                prior_claims_12m: int, garage: dict | None, inconsistencies: list[dict]) -> dict:
    """Adds up points for every red flag. Returns {"score", "signals"} where signals explain the score."""
    signals = []

    def flag(code: str, detail: str, points: int | None = None):
        signals.append({"code": code, "points": points if points is not None else FRAUD_POINTS[code],
                        "detail": detail})

    loss_date = to_date(form["loss_date"])

    if policy:  # policy can be None when the policy number was not found
        days_since_start = (loss_date - to_date(policy["start_date"])).days
        if 0 <= days_since_start < config.EARLY_CLAIM_DAYS:
            flag("EARLY_CLAIM", f"Accident {days_since_start} days after policy start")

    if garage and garage["watchlist"]:
        flag("WATCHLIST_GARAGE", f"Garage {garage['garage_id']} ({garage['name']}) is on the fraud watchlist")

    if fir and fir["incident_date"] != form["loss_date"]:
        flag("FIR_DATE_MISMATCH", f"FIR says {fir['incident_date']}, claim form says {form['loss_date']}")

    if prior_claims_12m >= 2:
        flag("REPEAT_CLAIMANT", f"{prior_claims_12m} claims on this policy in the last 12 months")

    if not survey["cause_consistent_with_claim"]:
        flag("SURVEYOR_DOUBTS_CAUSE", "Surveyor reports damage is not consistent with the claimed cause")

    delay = (to_date(form["intimation_date"]) - loss_date).days
    if delay > config.INTIMATION_DAYS_LIMIT:
        flag("LATE_INTIMATION", f"Reported {delay} days after the accident (limit {config.INTIMATION_DAYS_LIMIT})")

    surveyor_total = sum(item["amount"] for item in survey["items"])
    if surveyor_total > 0 and form["garage_estimate"] > config.ESTIMATE_INFLATION_RATIO * surveyor_total:
        ratio = form["garage_estimate"] / surveyor_total
        flag("ESTIMATE_INFLATED", f"Garage estimate Rs.{form['garage_estimate']:,.0f} is "
                                  f"{ratio:.0%} of surveyor's Rs.{surveyor_total:,.0f}")

    hour = int(form["loss_time"].split(":")[0])
    if (hour >= 22 or hour < 5) and not form["fir_filed"] and not form["witness_available"]:
        flag("UNWITNESSED_NIGHT_ACCIDENT", f"Accident at {form['loss_time']} with no FIR and no witness")

    for item in inconsistencies:
        points = INCONSISTENCY_POINTS[item["severity"]]
        if points:
            flag("DOCUMENT_INCONSISTENCY",
                 f"{item['topic']}: claim form says '{item['claim_form_says']}', "
                 f"other document says '{item['other_document_says']}'", points)

    return {"score": min(sum(s["points"] for s in signals), 100), "signals": signals}


# ---------------------------------------------------------------------------
# Step 4: Routing — who handles this claim next?
# ---------------------------------------------------------------------------
def decide_route(policy_issues: list[dict], coverage: dict | None,
                 assessment: dict | None, fraud: dict) -> dict:
    """
    Returns {"route", "recommendation", "reasons"}.
      route          : AUTO_APPROVE | ADJUSTER_REVIEW | SIU_INVESTIGATION
      recommendation : APPROVE | REJECT  (the AI's suggestion; a human makes the final call
                       on anything that is not auto-approved)
    Design choice: the AI may auto-APPROVE small clean claims, but it never auto-REJECTS.
    A rejection hurts a customer, so a human always signs it off.
    """
    reasons = []

    if policy_issues:
        recommendation = "REJECT"
        reasons += [f"{i['code']}: {i['detail']}" for i in policy_issues]
    elif coverage and not coverage["covered"]:
        recommendation = "REJECT"
        reasons.append(f"Excluded by {', '.join(coverage['exclusion_clause_ids'])}: {coverage['reasoning']}")
    else:
        recommendation = "APPROVE"

    if fraud["score"] >= config.FRAUD_SIU_THRESHOLD:
        route = "SIU_INVESTIGATION"
        reasons.append(f"Fraud score {fraud['score']} >= {config.FRAUD_SIU_THRESHOLD}")
    elif recommendation == "REJECT":
        route = "ADJUSTER_REVIEW"
        reasons.append("Rejections always need human sign-off")
    elif fraud["score"] >= config.FRAUD_REVIEW_THRESHOLD:
        route = "ADJUSTER_REVIEW"
        reasons.append(f"Fraud score {fraud['score']} needs a second look")
    elif assessment and assessment["total_loss"]:
        route = "ADJUSTER_REVIEW"
        reasons.append("Constructive total loss (repair cost > 75% of IDV)")
    elif assessment and assessment["payable"] > config.AUTO_APPROVE_LIMIT_INR:
        route = "ADJUSTER_REVIEW"
        reasons.append(f"Payable Rs.{assessment['payable']:,} is above the auto-approve limit "
                       f"Rs.{config.AUTO_APPROVE_LIMIT_INR:,}")
    elif coverage and coverage["confidence"] != "high":
        route = "ADJUSTER_REVIEW"
        reasons.append(f"Coverage decision confidence is {coverage['confidence']}")
    else:
        route = "AUTO_APPROVE"
        reasons.append("Covered, low fraud risk, amount within auto-approve limit")

    return {"route": route, "recommendation": recommendation, "reasons": reasons}
