"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : scripts/process_claim.py
Purpose: Run one or more sample claims from the terminal and watch every step of the agent.

Usage (from the project root):
    python -m scripts.process_claim CLM-1001            # one claim
    python -m scripts.process_claim CLM-1001 CLM-1014   # several claims
    python -m scripts.process_claim --all               # all 20 sample claims
"""

import sys

from app import config, db
from app.service import process_claim, sample_claim_dir


def main(claim_ids: list[str]) -> None:
    db.init_db()
    for claim_id in claim_ids:
        print(f"\n=== {claim_id} ===")
        claim = process_claim(claim_id, sample_claim_dir(claim_id), verbose=True)
        cost = claim["details"].get("cost_inr", 0)
        print(f"  -> status {claim['status']} | fraud score {claim['fraud_score']} | "
              f"{claim['details'].get('llm_calls', 0)} LLM calls | cost Rs.{cost:.2f}")
        if claim["letter"]:
            print("\n" + "\n".join("    " + line for line in claim["letter"].splitlines()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    ids = sorted(p.name for p in config.SAMPLE_CLAIMS_DIR.iterdir()) if args == ["--all"] else args
    main(ids)
