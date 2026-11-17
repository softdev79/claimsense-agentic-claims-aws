"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/api.py
Purpose: REST API. This is how the insurer's existing systems (core policy admin, mobile app,
         call-centre tools) would integrate with the agent — an FDE almost always ships an API, not just a UI.

Run (from the project root):
    uvicorn app.api:app --reload --port 8000
Open http://localhost:8000/docs for interactive Swagger docs.
"""

from fastapi import FastAPI, File, HTTPException, UploadFile

from app import config, db
from app.schemas import ReviewDecision
from app.service import process_claim, review_claim, sample_claim_dir, save_upload

app = FastAPI(title="ClaimSense - Agentic Motor Claims API",
              description="Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal. Upload claim documents, get an AI decision, "
                          "and let adjusters / SIU approve or reject.")
db.init_db()


@app.get("/health")
def health():
    return {"status": "ok", "model": config.OPENAI_MODEL}


@app.post("/claims")
def submit_claim(claim_form: UploadFile = File(..., description="Claim form PDF"),
                 survey_report: UploadFile = File(..., description="Surveyor report PDF"),
                 fir: UploadFile | None = File(None, description="Police FIR PDF (optional)")):
    """Upload a new claim's PDFs and process it. Returns the claim with its status and AI decision."""
    files = {"claim_form": claim_form, "survey_report": survey_report, "fir": fir}
    contents = {}
    for name, upload in files.items():
        if upload is None:
            continue
        if not upload.filename.lower().endswith(".pdf"):
            raise HTTPException(400, f"{name} must be a PDF")
        contents[name] = upload.file.read()
    claim_id, folder = save_upload(contents)
    # Runs synchronously (~10-20 s). In production: put the claim on a queue (SQS) and return 202 immediately.
    return process_claim(claim_id, folder)


@app.post("/claims/sample/{claim_id}")
def run_sample_claim(claim_id: str):
    """Process one of the 20 bundled sample claims (handy for demos)."""
    try:
        folder = sample_claim_dir(claim_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    return process_claim(claim_id, folder)


@app.get("/claims/samples")
def list_sample_claims():
    return sorted(p.name for p in config.SAMPLE_CLAIMS_DIR.iterdir() if p.is_dir())


@app.get("/claims")
def list_all_claims(status: str | None = None):
    """Work queues, e.g. /claims?status=PENDING_ADJUSTER or /claims?status=PENDING_SIU"""
    return db.list_claims(status)


@app.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    claim = db.get_claim(claim_id)
    if claim is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return {**claim, "audit_trail": db.get_audit(claim_id)}


@app.post("/claims/{claim_id}/review")
def submit_review(claim_id: str, review: ReviewDecision):
    """Adjuster / SIU decision. Resumes the paused LangGraph run and writes the customer letter."""
    try:
        return review_claim(claim_id, review)
    except ValueError as error:
        raise HTTPException(400, str(error))
