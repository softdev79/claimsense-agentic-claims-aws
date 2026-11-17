"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/config.py
Purpose: One place for every setting: models, prices, file paths and business thresholds.
         Business rules (limits, thresholds) live here so a claims manager can change them
         without touching agent code.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # reads OPENAI_API_KEY (and optional overrides) from a .env file in the project root

# ---------------------------------------------------------------------------
# Models (AWS capstone edition: Amazon Bedrock via its OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------
# The OpenAI SDK reads OPENAI_API_KEY (a Bedrock API key), OPENAI_BASE_URL
# (https://bedrock-mantle.<region>.api.aws/v1) and OPENAI_PROJECT_ID from the environment.
# Model chosen with scripts/check_bedrock_models.py + evals/run_evals.py (see AWS_DEPLOY_GUIDE.md).
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen.qwen3-vl-235b-a22b-instruct")

# Price per 1 million tokens in USD: (input, output).
# APPROXIMATE - check https://aws.amazon.com/bedrock/pricing/ for your region before quoting costs.
PRICES_USD_PER_1M = {
    "qwen.qwen3-vl-235b-a22b-instruct": (0.53, 2.66),
    "mistral.ministral-3-14b-instruct": (0.20, 0.20),
    "openai.gpt-oss-120b": (0.15, 0.60),
    "bm25-local": (0.0, 0.0),
}
USD_TO_INR = float(os.getenv("USD_TO_INR", "88"))  # update to today's exchange rate

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "claims.db"                  # business data: policies, claims, audit log
CHECKPOINT_DB_PATH = DATA_DIR / "checkpoints.db"  # LangGraph state, so a paused claim can resume later
SAMPLE_CLAIMS_DIR = DATA_DIR / "sample_claims"    # 20 ready-made claims (PDFs) for demos and evals
UPLOADS_DIR = DATA_DIR / "uploads"                # claims submitted through the API / portal
POLICY_WORDING_PATH = DATA_DIR / "policy_wording.md"
EVAL_LABELS_PATH = DATA_DIR / "eval_labels.csv"

# ---------------------------------------------------------------------------
# Business rules (the "risk appetite" of the insurer)
# ---------------------------------------------------------------------------
AUTO_APPROVE_LIMIT_INR = 50_000      # above this, a human must approve even a clean claim
FRAUD_SIU_THRESHOLD = 50             # fraud score >= 50 -> Special Investigation Unit
FRAUD_REVIEW_THRESHOLD = 25          # fraud score 25-49 -> claims adjuster review
TOTAL_LOSS_RATIO = 0.75              # repair cost > 75% of IDV -> constructive total loss
INTIMATION_DAYS_LIMIT = 7            # claim should be reported within 7 days of the accident
EARLY_CLAIM_DAYS = 30                # accident within 30 days of policy start is a fraud signal
ESTIMATE_INFLATION_RATIO = 1.40      # garage estimate > 140% of surveyor assessment is a fraud signal
RETRIEVAL_TOP_K = 6                  # how many policy clauses to retrieve for the coverage check
