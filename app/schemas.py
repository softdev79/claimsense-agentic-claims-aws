"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : app/schemas.py
Purpose: Pydantic models that define EXACTLY what the LLM must return.
         We pass these to OpenAI "structured outputs", so the model is forced to reply with valid
         JSON that matches the schema. No regex parsing of free text, no "the model forgot a field".

Rule of thumb used here: the LLM only READS and JUDGES. Every number that affects money
(depreciation, deductible, payable amount, fraud score) is calculated by Python in app/rules.py.
"""

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. Document extraction (one schema per document type)
# ---------------------------------------------------------------------------
class ClaimForm(BaseModel):
    """Fields read from the claim form the policyholder filled in."""
    policy_number: str
    claimant_name: str
    vehicle_reg: str = Field(description="Registration number exactly as written, e.g. KA-01-AB-1234")
    loss_date: str = Field(description="Date of accident in YYYY-MM-DD")
    loss_time: str = Field(description="Time of accident in 24h HH:MM")
    loss_location: str
    intimation_date: str = Field(description="Date the claim was reported to the insurer, YYYY-MM-DD")
    driver_name: str
    driver_licence_no: str
    licence_valid_till: str = Field(description="Driving licence expiry date in YYYY-MM-DD")
    vehicle_use_at_time: str = Field(description="What the car was being used for at the time, in the claimant's words")
    accident_description: str
    damaged_areas: list[str] = Field(description="Parts of the car the claimant says were damaged")
    fir_filed: bool
    witness_available: bool
    garage_id: str = Field(description="Garage code, e.g. G03")
    garage_estimate: float = Field(description="Total repair estimate from the garage in rupees")


class AssessedItem(BaseModel):
    """One line of the surveyor's assessment table."""
    description: str
    category: Literal["metal", "plastic_rubber", "fibre_glass", "glass", "labour"]
    amount: float = Field(description="Assessed amount in rupees before depreciation")


class SurveyReport(BaseModel):
    """Fields read from the independent surveyor's inspection report."""
    surveyor_name: str
    inspection_date: str = Field(description="YYYY-MM-DD")
    damaged_areas: list[str] = Field(description="Parts the surveyor actually found damaged")
    observations: str
    cause_consistent_with_claim: bool = Field(description="Surveyor's own opinion as written in the report")
    items: list[AssessedItem]


class FIRReport(BaseModel):
    """Fields read from the police First Information Report (only some claims have one)."""
    fir_number: str
    incident_date: str = Field(description="YYYY-MM-DD")
    incident_time: str = Field(description="24h HH:MM")
    location: str
    summary: str
    driver_intoxicated: bool
    breath_alcohol_mg_per_100ml: float | None = Field(description="Breath analyser reading if recorded, else null")


# ---------------------------------------------------------------------------
# 2. Coverage decision (RAG over policy wording)
# ---------------------------------------------------------------------------
class CoverageDecision(BaseModel):
    covered: bool = Field(description="True if the loss is covered by the policy wording")
    exclusion_clause_ids: list[str] = Field(description="Clause IDs (from the provided clauses only) that exclude the claim; empty if covered")
    reasoning: str = Field(description="2-4 sentences citing clause IDs and the facts that triggered them")
    confidence: Literal["high", "medium", "low"]


# ---------------------------------------------------------------------------
# 3. Cross-document consistency (fraud signal)
# ---------------------------------------------------------------------------
class Inconsistency(BaseModel):
    topic: str = Field(description="What disagrees, e.g. 'damaged area' or 'cause of damage'")
    claim_form_says: str
    other_document_says: str
    severity: Literal["high", "medium", "low"]


class ConsistencyReport(BaseModel):
    inconsistencies: list[Inconsistency]


# ---------------------------------------------------------------------------
# 4. Human review input (adjuster / SIU decision sent to the API)
# ---------------------------------------------------------------------------
class ReviewDecision(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    approved_amount: float = Field(ge=0, description="Amount to pay in rupees; 0 if rejected")
    reviewer: str = Field(min_length=2)
    notes: str = Field(default="", description="Shown to the customer in the decision letter. "
                                                "Required when the reviewer overrides the AI recommendation.")
