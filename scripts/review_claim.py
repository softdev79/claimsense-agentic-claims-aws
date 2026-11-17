"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : scripts/review_claim.py
Purpose: Act as the adjuster / SIU officer from the terminal: resume a paused claim with a decision.

Usage (from the project root):
    python -m scripts.review_claim CLM-1008 APPROVE 90000 "Asha Verma (Adjuster)"
    python -m scripts.review_claim CLM-1014 REJECT 0 "Asha Verma (Adjuster)" "Driver was drunk as per FIR"
"""

import sys

from app.schemas import ReviewDecision
from app.service import review_claim

if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    claim_id, decision, amount, reviewer = sys.argv[1:5]
    notes = sys.argv[5] if len(sys.argv) > 5 else ""
    claim = review_claim(claim_id, ReviewDecision(decision=decision, approved_amount=float(amount),
                                                  reviewer=reviewer, notes=notes), verbose=True)
    print(f"  -> status {claim['status']} | final amount Rs.{claim['final_amount']:,.0f}\n")
    print("\n".join("    " + line for line in claim["letter"].splitlines()))
