"""
ShiftGuard — Clinical Handoff Intelligence MCP Server

A production-grade MCP server that transforms unstructured clinical handoff
notes into FHIR R4-compliant patient summaries with AI-powered risk detection
and SBAR brief generation.

Tools:
    1. parse_handoff_note — Extract structured FHIR data from raw handoff notes
    2. flag_critical_risks — Detect patient safety risks using rules + AI
    3. generate_handoff_brief — Generate professional SBAR clinical briefs

Author: Meet Desai
License: MIT
"""

import os
import sys
import json
import time
import logging
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from fastmcp import FastMCP

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fhir.builder import (
    build_fhir_bundle,
    build_patient,
    build_observation,
    build_allergy,
    build_medication_request,
    build_condition,
)
from fhir.client import (
    push_bundle_to_fhir,
    get_patient_from_fhir,
    get_patient_observations,
    get_patient_allergies,
)
from rules.risk_rules import (
    check_vital_ranges,
    check_missing_critical_info,
    check_allergy_medication_conflicts,
)

# ──────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────

load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shiftguard")

# Gemini AI client
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY not set. LLM calls will fail.")

ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODEL_NAME = "gemini-2.5-flash"

# Prompt directory
PROMPTS_DIR = Path(__file__).parent / "prompts"

# MCP Server
mcp = FastMCP("ShiftGuard — Clinical Handoff Intelligence")


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / filename
    if not prompt_path.exists():
        logger.error(f"Prompt file not found: {prompt_path}")
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _call_gemini(prompt: str, context: str = "", max_retries: int = 3) -> str:
    """
    Call the Gemini API with a prompt and optional context.
    Logs latency and retries on transient errors (503, 429).
    """
    if not ai_client:
        raise RuntimeError("Gemini API key not configured")

    full_prompt = prompt
    if context:
        full_prompt = prompt.replace("{note}", context).replace(
            "{fhir_bundle}", context
        ).replace("{flagged_risks}", context)

    for attempt in range(1, max_retries + 1):
        start_time = time.time()
        logger.info(f"Calling Gemini ({MODEL_NAME}) — attempt {attempt}/{max_retries}, prompt length: {len(full_prompt)} chars")

        try:
            response = ai_client.models.generate_content(
                model=MODEL_NAME,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temp for consistent structured output
                    max_output_tokens=4096,
                ),
            )

            latency = time.time() - start_time
            result_text = response.text.strip() if response.text else ""
            logger.info(
                f"Gemini response received — latency: {latency:.2f}s, "
                f"response length: {len(result_text)} chars"
            )
            return result_text

        except Exception as e:
            latency = time.time() - start_time
            error_str = str(e)
            # Retry on transient errors (503, 429)
            if attempt < max_retries and ("503" in error_str or "429" in error_str or "UNAVAILABLE" in error_str):
                wait_time = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(f"Gemini API transient error (attempt {attempt}): {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
                continue
            logger.error(f"Gemini API call failed after {latency:.2f}s: {e}")
            raise


def _parse_json_response(text: str) -> dict | list:
    """
    Parse JSON from an LLM response, stripping markdown artifacts.
    """
    cleaned = text.strip()

    # Strip markdown code block wrappers
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed: {e}")
        logger.error(f"Raw response (first 500 chars): {cleaned[:500]}")
        raise ValueError(f"Failed to parse LLM response as JSON: {e}")


def _merge_sharp_context_data(
    resources: list, sharp_context: dict | None
) -> list:
    """
    If SHARP context provides a patient_id, fetch existing data from FHIR
    and merge it with extracted resources.
    """
    if not sharp_context or not sharp_context.get("patient_id"):
        return resources

    patient_id = sharp_context["patient_id"]
    logger.info(f"SHARP context: merging existing data for patient {patient_id}")

    try:
        # Fetch existing patient data
        existing_patient = get_patient_from_fhir(patient_id, sharp_context)
        if existing_patient:
            logger.info(f"Found existing patient record on FHIR server")

        # Fetch existing observations
        existing_obs = get_patient_observations(patient_id, sharp_context)
        if existing_obs:
            logger.info(f"Found {len(existing_obs)} existing observations")
            resources.extend(existing_obs)

        # Fetch existing allergies
        existing_allergies = get_patient_allergies(patient_id, sharp_context)
        if existing_allergies:
            logger.info(f"Found {len(existing_allergies)} existing allergies")
            resources.extend(existing_allergies)

    except Exception as e:
        logger.warning(f"Failed to fetch SHARP context data: {e}")

    return resources


# ──────────────────────────────────────────────────────────────
# Tool 1: Parse Handoff Note
# ──────────────────────────────────────────────────────────────


@mcp.tool
def parse_handoff_note(
    note: str,
    sharp_context: dict | None = None,
) -> dict:
    """
    Parse a raw clinical handoff note into a structured FHIR R4 Bundle.

    Takes an unstructured handoff note — the kind a tired nurse writes during
    shift change — and uses AI to extract structured clinical data. The output
    is a valid FHIR R4 Bundle containing Patient, Observation, AllergyIntolerance,
    MedicationRequest, and Condition resources.

    The bundle is also pushed to the HAPI FHIR test server for interoperability
    demonstration.

    Args:
        note: Raw unstructured handoff note text from a nurse or doctor.
              Can contain abbreviations, typos, Hindi/Hinglish, and informal language.
        sharp_context: Optional SHARP context dict with patient_id, fhir_base_url,
                       and auth_token for existing patient data merging.

    Returns:
        A FHIR R4 Bundle dict containing all extracted clinical resources.
        Includes a '_shiftguard_meta' field with extraction metadata.
    """
    logger.info(f"=== Tool 1: parse_handoff_note ===")
    logger.info(f"Input note length: {len(note)} chars")

    extraction_meta = {
        "tool": "parse_handoff_note",
        "input_length": len(note),
        "resources_extracted": 0,
        "fhir_push_status": "not_attempted",
        "fhir_server_url": None,
        "errors": [],
    }

    try:
        # Step 1: Call Gemini to extract structured data
        prompt = _load_prompt("parse_prompt.txt")
        prompt_with_note = prompt.replace("{note}", note)
        raw_response = _call_gemini(prompt_with_note)
        parsed_data = _parse_json_response(raw_response)

        logger.info(f"Extracted data for {len(parsed_data.get('patients', []))} patient(s)")

    except Exception as e:
        logger.error(f"LLM extraction failed: {e}")
        extraction_meta["errors"].append(f"LLM extraction failed: {str(e)}")
        # Return minimal bundle with error
        error_bundle = build_fhir_bundle([])
        error_bundle["_shiftguard_meta"] = extraction_meta
        return error_bundle

    # Step 2: Build FHIR resources from extracted data
    all_resources = []

    for patient_data in parsed_data.get("patients", []):
        try:
            # Build Patient resource
            patient_resource = build_patient(
                patient_id=patient_data.get("patient_id"),
                name=patient_data.get("name", "Unknown"),
                age=patient_data.get("age"),
                gender=patient_data.get("gender"),
                bed_number=patient_data.get("bed_number"),
            )
            patient_fhir_id = patient_resource["id"]
            all_resources.append(patient_resource)

            # Build Observation resources (vitals)
            for vital in patient_data.get("vitals", []):
                try:
                    obs = build_observation(
                        patient_id=patient_fhir_id,
                        vital_type=vital.get("type", "unknown"),
                        value=vital.get("value", 0),
                        unit=vital.get("unit", ""),
                    )
                    all_resources.append(obs)
                except Exception as e:
                    logger.warning(f"Failed to build observation: {e}")
                    extraction_meta["errors"].append(f"Observation build error: {str(e)}")

            # Build AllergyIntolerance resources
            for allergy in patient_data.get("allergies", []):
                try:
                    allergy_resource = build_allergy(
                        patient_id=patient_fhir_id,
                        substance=allergy.get("substance", "Unknown"),
                        severity=allergy.get("severity", "moderate"),
                        criticality="high" if allergy.get("severity") == "severe" else "high",
                        reaction_description=allergy.get("reaction"),
                    )
                    all_resources.append(allergy_resource)
                except Exception as e:
                    logger.warning(f"Failed to build allergy: {e}")
                    extraction_meta["errors"].append(f"Allergy build error: {str(e)}")

            # Build MedicationRequest resources
            for med in patient_data.get("medications", []):
                try:
                    med_status = med.get("status", "active")
                    # Map our statuses to FHIR statuses
                    status_map = {
                        "active": "active",
                        "overdue": "active",  # Still an active order, just overdue
                        "not_given": "active",
                        "completed": "completed",
                        "stopped": "stopped",
                    }
                    fhir_status = status_map.get(med_status, "active")

                    notes = med.get("notes", "")
                    if med_status in ("overdue", "not_given"):
                        notes = f"[{med_status.upper()}] {notes}".strip()

                    med_resource = build_medication_request(
                        patient_id=patient_fhir_id,
                        medication_name=med.get("name", "Unknown"),
                        dosage=med.get("dosage"),
                        timing=med.get("timing"),
                        status=fhir_status,
                        notes=notes if notes else None,
                    )
                    all_resources.append(med_resource)
                except Exception as e:
                    logger.warning(f"Failed to build medication: {e}")
                    extraction_meta["errors"].append(f"Medication build error: {str(e)}")

            # Build Condition resources
            for condition in patient_data.get("conditions", []):
                try:
                    cond_status = condition.get("status", "active")
                    status_map = {
                        "active": "active",
                        "resolved": "resolved",
                        "suspected": "active",  # FHIR doesn't have "suspected" as clinical status
                    }
                    fhir_status = status_map.get(cond_status, "active")

                    cond_resource = build_condition(
                        patient_id=patient_fhir_id,
                        description=condition.get("description", "Unknown condition"),
                        clinical_status=fhir_status,
                        severity=condition.get("severity"),
                    )
                    all_resources.append(cond_resource)
                except Exception as e:
                    logger.warning(f"Failed to build condition: {e}")
                    extraction_meta["errors"].append(f"Condition build error: {str(e)}")

        except Exception as e:
            logger.error(f"Failed to process patient data: {e}")
            extraction_meta["errors"].append(f"Patient processing error: {str(e)}")

    # Step 3: Merge SHARP context data if available
    all_resources = _merge_sharp_context_data(all_resources, sharp_context)

    # Step 4: Build FHIR bundle
    fhir_bundle = build_fhir_bundle(all_resources)
    extraction_meta["resources_extracted"] = len(all_resources)

    logger.info(f"Built FHIR bundle with {len(all_resources)} resources")

    # Step 5: Push to HAPI FHIR server
    try:
        push_result = push_bundle_to_fhir(fhir_bundle, sharp_context)
        if push_result.get("error"):
            extraction_meta["fhir_push_status"] = "failed"
            extraction_meta["fhir_push_error"] = push_result.get("message", "Unknown")
            logger.warning(f"FHIR push failed: {push_result.get('message')}")
        else:
            extraction_meta["fhir_push_status"] = "success"
            # Extract server-assigned IDs
            server_entries = push_result.get("entry", [])
            fhir_urls = []
            for entry in server_entries:
                location = entry.get("response", {}).get("location", "")
                if location:
                    fhir_urls.append(location)
            extraction_meta["fhir_server_urls"] = fhir_urls
            fhir_base = os.getenv("FHIR_BASE_URL", "https://hapi.fhir.org/baseR4")
            extraction_meta["fhir_server_url"] = fhir_base
            logger.info(f"FHIR push successful. {len(fhir_urls)} resources created on server.")
    except Exception as e:
        extraction_meta["fhir_push_status"] = "error"
        extraction_meta["fhir_push_error"] = str(e)
        logger.warning(f"FHIR push error (non-fatal): {e}")

    # Attach metadata to bundle
    fhir_bundle["_shiftguard_meta"] = extraction_meta

    # Also attach the raw extracted data for downstream tools
    fhir_bundle["_shiftguard_extracted"] = parsed_data

    return fhir_bundle


# ──────────────────────────────────────────────────────────────
# Tool 2: Flag Critical Risks
# ──────────────────────────────────────────────────────────────


@mcp.tool
def flag_critical_risks(
    fhir_bundle: dict,
    sharp_context: dict | None = None,
) -> list:
    """
    Analyze a FHIR Bundle and flag critical patient safety risks.

    Combines programmatic rule-based detection (vital ranges, allergy conflicts)
    with AI-powered clinical reasoning (drug interactions, contextual risks).
    Returns a prioritized list of risks sorted by severity.

    Risk categories:
    - ALLERGY_CONFLICT: Medication prescribed despite documented allergy
    - DRUG_INTERACTION: Dangerous drug-drug interaction
    - ABNORMAL_VITAL: Vital sign outside normal range
    - MISSING_INFO: Critical clinical information absent
    - OVERDUE_MEDICATION: Medication not given or overdue

    Args:
        fhir_bundle: A FHIR R4 Bundle dict (output from parse_handoff_note).
        sharp_context: Optional SHARP context for enhanced risk detection.

    Returns:
        A list of risk dicts sorted by severity (CRITICAL first), each containing:
        risk_id, severity, category, description, recommended_action, fhir_reference.
    """
    logger.info(f"=== Tool 2: flag_critical_risks ===")

    all_risks = []

    # Step 1: Programmatic rule-based checks
    logger.info("Running programmatic risk detection...")

    # Check vital ranges
    vital_risks = check_vital_ranges(fhir_bundle)
    all_risks.extend(vital_risks)
    logger.info(f"Vital range check: {len(vital_risks)} risks")

    # Check missing critical info
    missing_risks = check_missing_critical_info(fhir_bundle)
    all_risks.extend(missing_risks)
    logger.info(f"Missing info check: {len(missing_risks)} risks")

    # Check allergy-medication conflicts
    allergy_risks = check_allergy_medication_conflicts(fhir_bundle)
    all_risks.extend(allergy_risks)
    logger.info(f"Allergy-medication check: {len(allergy_risks)} risks")

    # Step 2: LLM-based clinical reasoning
    logger.info("Running AI-powered risk reasoning...")
    try:
        prompt = _load_prompt("risk_prompt.txt")
        bundle_json = json.dumps(fhir_bundle, indent=2, default=str)
        prompt_with_data = prompt.replace("{fhir_bundle}", bundle_json)
        raw_response = _call_gemini(prompt_with_data)
        llm_risks = _parse_json_response(raw_response)

        if isinstance(llm_risks, list):
            logger.info(f"LLM risk reasoning: {len(llm_risks)} additional risks")

            # Add LLM risks, but deduplicate against programmatic risks
            existing_descriptions = {
                r["description"].lower()[:50] for r in all_risks
            }

            for risk in llm_risks:
                # Ensure required fields exist
                if not all(k in risk for k in ("severity", "category", "description")):
                    continue

                # Basic deduplication
                desc_prefix = risk["description"].lower()[:50]
                if desc_prefix not in existing_descriptions:
                    # Ensure risk_id exists
                    if "risk_id" not in risk:
                        risk["risk_id"] = f"LLM-{len(all_risks):03d}"
                    if "recommended_action" not in risk:
                        risk["recommended_action"] = "Review and assess."
                    if "fhir_reference" not in risk:
                        risk["fhir_reference"] = "Bundle"
                    all_risks.append(risk)
                    existing_descriptions.add(desc_prefix)
        else:
            logger.warning("LLM returned non-list response for risks")

    except Exception as e:
        logger.error(f"LLM risk reasoning failed: {e}")
        # Non-fatal — we still have programmatic risks

    # Step 3: Check for overdue medications from extracted data
    extracted = fhir_bundle.get("_shiftguard_extracted", {})
    for patient in extracted.get("patients", []):
        for med in patient.get("medications", []):
            if med.get("status") in ("overdue", "not_given"):
                med_name = med.get("name", "Unknown")
                # Check if already flagged
                already_flagged = any(
                    med_name.lower() in r.get("description", "").lower()
                    and r.get("category") == "OVERDUE_MEDICATION"
                    for r in all_risks
                )
                if not already_flagged:
                    # Determine severity based on medication type
                    severity = "HIGH"
                    if "insulin" in med_name.lower():
                        severity = "CRITICAL"

                    all_risks.append({
                        "risk_id": f"OVERDUE-{med_name[:6].upper()}",
                        "severity": severity,
                        "category": "OVERDUE_MEDICATION",
                        "description": (
                            f"{med_name} is documented as {med.get('status', 'overdue').upper()}. "
                            f"{med.get('notes', '')}"
                        ),
                        "recommended_action": (
                            f"Administer {med_name} immediately and document."
                            if severity == "CRITICAL"
                            else f"Administer {med_name} as soon as possible and document delay."
                        ),
                        "fhir_reference": "MedicationRequest",
                    })

    # Step 4: Sort by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    all_risks.sort(key=lambda r: severity_order.get(r.get("severity", "LOW"), 4))

    logger.info(f"Total risks flagged: {len(all_risks)}")
    severity_counts = {}
    for r in all_risks:
        sev = r.get("severity", "UNKNOWN")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    logger.info(f"Severity breakdown: {severity_counts}")

    return all_risks


# ──────────────────────────────────────────────────────────────
# Tool 3: Generate Handoff Brief
# ──────────────────────────────────────────────────────────────


@mcp.tool
def generate_handoff_brief(
    fhir_bundle: dict,
    flagged_risks: list,
    sharp_context: dict | None = None,
) -> str:
    """
    Generate a professional SBAR clinical handoff brief from structured data.

    Creates a concise, clinical-grade handoff brief in SBAR format (Situation,
    Background, Assessment, Recommendation). The brief is designed to be read
    by the incoming clinician at shift change.

    The brief:
    - Uses professional clinical language
    - Leads with CRITICAL risks
    - Is under 250 words
    - Ends with a numbered action list
    - Sounds like it was written by a senior charge nurse

    Args:
        fhir_bundle: A FHIR R4 Bundle dict (output from parse_handoff_note).
        flagged_risks: List of flagged risks (output from flag_critical_risks).
        sharp_context: Optional SHARP context (unused in this tool, included for
                       API consistency).

    Returns:
        A formatted SBAR brief string ready for the incoming clinician.
    """
    logger.info(f"=== Tool 3: generate_handoff_brief ===")
    logger.info(f"Bundle entries: {len(fhir_bundle.get('entry', []))}, Risks: {len(flagged_risks)}")

    try:
        prompt = _load_prompt("brief_prompt.txt")

        # Serialize inputs as clean JSON
        bundle_json = json.dumps(fhir_bundle, indent=2, default=str)
        risks_json = json.dumps(flagged_risks, indent=2, default=str)

        # Fill prompt template
        filled_prompt = prompt.replace("{fhir_bundle}", bundle_json).replace(
            "{flagged_risks}", risks_json
        )

        # Call Gemini
        brief_text = _call_gemini(filled_prompt)

        # Hard truncation at 250 words
        words = brief_text.split()
        if len(words) > 250:
            brief_text = " ".join(words[:250]) + "\n\n[Brief truncated to 250 words]"
            logger.warning(f"Brief truncated from {len(words)} to 250 words")

        logger.info(f"Brief generated: {len(brief_text)} chars, {len(brief_text.split())} words")
        return brief_text

    except Exception as e:
        logger.error(f"Brief generation failed: {e}")
        # Return a fallback brief
        return _generate_fallback_brief(fhir_bundle, flagged_risks)


def _generate_fallback_brief(fhir_bundle: dict, flagged_risks: list) -> str:
    """Generate a basic fallback brief if LLM fails."""
    lines = [
        "═══ SHIFTGUARD HANDOFF BRIEF (FALLBACK) ═══",
        "",
        "S — SITUATION",
        "AI-generated brief unavailable. Review data below.",
        "",
        "B — BACKGROUND",
    ]

    # Extract basic patient info
    for entry in fhir_bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            name = resource.get("name", [{}])[0].get("text", "Unknown")
            lines.append(f"Patient: {name}")
            break

    lines.extend([
        "",
        "A — ASSESSMENT",
        f"Total risks flagged: {len(flagged_risks)}",
    ])

    critical_risks = [r for r in flagged_risks if r.get("severity") == "CRITICAL"]
    if critical_risks:
        lines.append("")
        lines.append("⚠️ CRITICAL RISKS:")
        for r in critical_risks:
            lines.append(f"  - {r.get('description', 'Unknown risk')}")

    lines.extend([
        "",
        "R — RECOMMENDATION",
        "Review the full FHIR bundle and risk list manually.",
    ])

    for i, r in enumerate(critical_risks, 1):
        lines.append(f"{i}. {r.get('recommended_action', 'Assess immediately')}")

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Server startup
# ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting ShiftGuard MCP server on port {port}")
    logger.info(f"Gemini model: {MODEL_NAME}")
    logger.info(f"FHIR server: {os.getenv('FHIR_BASE_URL', 'https://hapi.fhir.org/baseR4')}")
    logger.info(f"Tools: parse_handoff_note, flag_critical_risks, generate_handoff_brief")

    # Run the MCP server
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
