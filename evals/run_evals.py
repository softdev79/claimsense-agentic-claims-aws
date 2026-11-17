"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : evals/run_evals.py
Purpose: Measure the agent on the 20 labelled sample claims. This is what turns a demo into an
         engineering project: every prompt / model change is judged by these numbers, not by vibes.

Metrics
  route accuracy          : did the claim go to the right place (auto / adjuster / SIU)?
  recommendation accuracy : did the AI suggest APPROVE / REJECT correctly?
  fraud precision/recall  : of claims sent to SIU, how many were really suspicious (precision);
                            of the really suspicious ones, how many did we catch (recall)
  payable accuracy        : for approvals, is the amount within Rs.1 of the correct amount?
                            (tests whether the LLM read the survey table correctly)
  straight-through rate   : share of claims settled with no human touch
  cost and latency        : rupees and seconds per claim

Usage (from the project root, needs OPENAI_API_KEY):
    python -m evals.run_evals
"""

import csv
import time
from datetime import datetime
from pathlib import Path

from app import config, db
from app.service import process_claim, sample_claim_dir


def pct(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.0%} ({numerator}/{denominator})" if denominator else "n/a"


def main() -> None:
    db.init_db()
    with open(config.EVAL_LABELS_PATH) as fh:
        labels = list(csv.DictReader(fh))

    rows = []
    print(f"{'claim':<9} {'expected route':<18} {'got route':<21} {'rec':<8} {'payable exp/got':<16} {'fraud':>5} {'Rs.':>5} {'sec':>5}")
    for label in labels:
        start = time.time()
        claim = process_claim(label["claim_id"], sample_claim_dir(label["claim_id"]))
        seconds = time.time() - start

        expected_payable = float(label["expected_payable"]) if label["expected_payable"] else None
        got_payable = claim["payable_amount"]
        row = {
            "claim_id": label["claim_id"],
            "expected_route": label["expected_route"], "got_route": claim["route"] or claim["status"],
            "expected_recommendation": label["expected_recommendation"], "got_recommendation": claim["recommendation"],
            "is_fraud": label["is_fraud"] == "True", "flagged_siu": claim["route"] == "SIU_INVESTIGATION",
            "expected_payable": expected_payable, "got_payable": got_payable,
            "payable_ok": None if expected_payable is None else (got_payable is not None and abs(got_payable - expected_payable) <= 1),
            "fraud_score": claim["fraud_score"], "cost_inr": claim["details"].get("cost_inr", 0),
            "seconds": round(seconds, 1), "error": claim["error"] or "",
        }
        rows.append(row)
        mark = "OK " if row["got_route"] == row["expected_route"] else "XX "
        payable = f"{expected_payable or 0:.0f}/{got_payable or 0:.0f}" if expected_payable else "-"
        print(f"{row['claim_id']:<9} {row['expected_route']:<18} {mark}{row['got_route']:<18} "
              f"{str(row['got_recommendation']):<8} {payable:<16} {str(row['fraud_score']):>5} "
              f"{row['cost_inr']:>5.2f} {row['seconds']:>5}")

    n = len(rows)
    route_ok = sum(r["got_route"] == r["expected_route"] for r in rows)
    rec_ok = sum(r["got_recommendation"] == r["expected_recommendation"] for r in rows)
    true_pos = sum(r["is_fraud"] and r["flagged_siu"] for r in rows)
    flagged = sum(r["flagged_siu"] for r in rows)
    actual_fraud = sum(r["is_fraud"] for r in rows)
    payable_rows = [r for r in rows if r["payable_ok"] is not None]
    auto = sum(r["got_route"] == "AUTO_APPROVE" for r in rows)

    print("\n================ EVAL SUMMARY ================")
    print(f"Model                    : {config.OPENAI_MODEL}")
    print(f"Route accuracy           : {pct(route_ok, n)}")
    print(f"Recommendation accuracy  : {pct(rec_ok, n)}")
    print(f"Fraud precision (SIU)    : {pct(true_pos, flagged)}")
    print(f"Fraud recall (SIU)       : {pct(true_pos, actual_fraud)}")
    print(f"Payable amount accuracy  : {pct(sum(r['payable_ok'] for r in payable_rows), len(payable_rows))}")
    print(f"Straight-through rate    : {pct(auto, n)}")
    print(f"Avg cost per claim       : Rs.{sum(r['cost_inr'] for r in rows) / n:.2f}")
    print(f"Avg latency per claim    : {sum(r['seconds'] for r in rows) / n:.1f} s")
    print(f"Failed claims            : {sum(bool(r['error']) for r in rows)}")

    out = Path(__file__).parent / f"results_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Per-claim results saved to {out.relative_to(config.ROOT_DIR)}")


if __name__ == "__main__":
    main()
