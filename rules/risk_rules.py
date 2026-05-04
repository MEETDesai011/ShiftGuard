"""
ShiftGuard — Programmatic Risk Detection Rules

Defines normal vital sign ranges and programmatic checks for clinical risks.
These rules run BEFORE the LLM-based risk reasoning to catch deterministic,
well-defined risks with zero latency.

Author: Meet Desai
"""

import uuid
import logging

logger = logging.getLogger("shiftguard.rules")


# ──────────────────────────────────────────────────────────────
# Normal vital sign ranges
# ──────────────────────────────────────────────────────────────

VITAL_RANGES = {
    "blood_pressure_systolic": {
        "min": 90,
        "max": 140,
        "unit": "mmHg",
        "display": "Systolic Blood Pressure",
        "loinc": "8480-6",
    },
    "blood_pressure_diastolic": {
        "min": 60,
        "max": 90,
        "unit": "mmHg",
        "display": "Diastolic Blood Pressure",
        "loinc": "8462-4",
    },
    "heart_rate": {
        "min": 60,
        "max": 100,
        "unit": "bpm",
        "display": "Heart Rate",
        "loinc": "8867-4",
    },
    "spo2": {
        "min": 95,
        "max": 100,
        "unit": "%",
        "display": "Oxygen Saturation (SpO2)",
        "loinc": "59408-5",
    },
    "temperature": {
        "min": 36.1,
        "max": 37.2,
        "unit": "°C",
        "display": "Body Temperature",
        "loinc": "8310-5",
    },
    "respiratory_rate": {
        "min": 12,
        "max": 20,
        "unit": "breaths/min",
        "display": "Respiratory Rate",
        "loinc": "9279-1",
    },
}

# LOINC code to vital type mapping
LOINC_TO_VITAL = {v["loinc"]: k for k, v in VITAL_RANGES.items()}

# Additional LOINC codes for composite observations
LOINC_BP_PANEL = "55284-4"  # Blood pressure panel

# Known penicillin-class antibiotics for allergy-drug conflict detection
PENICILLIN_CLASS_DRUGS = {
    "amoxicillin", "ampicillin", "penicillin", "penicillin v",
    "penicillin g", "piperacillin", "nafcillin", "oxacillin",
    "dicloxacillin", "flucloxacillin", "amoxicillin/clavulanate",
    "ampicillin/sulbactam", "piperacillin/tazobactam", "augmentin",
    "amoxiclav", "co-amoxiclav",
}


def _generate_risk_id() -> str:
    """Generate a unique risk ID."""
    return f"RISK-{uuid.uuid4().hex[:8].upper()}"


def _classify_severity(vital_type: str, value: float, range_info: dict) -> str:
    """
    Classify severity based on how far the value is from normal range.

    - CRITICAL: dangerously outside range (e.g., SpO2 < 90%, systolic > 180)
    - HIGH: significantly outside range
    - MEDIUM: mildly outside range
    """
    min_val = range_info["min"]
    max_val = range_info["max"]

    # SpO2 below 95% is always CRITICAL (per spec requirement)
    if vital_type == "spo2" and value < 95:
        return "CRITICAL" if value < 90 else "CRITICAL"

    # Critical thresholds
    critical_rules = {
        "blood_pressure_systolic": lambda v: v > 180 or v < 70,
        "blood_pressure_diastolic": lambda v: v > 120 or v < 40,
        "heart_rate": lambda v: v > 150 or v < 40,
        "temperature": lambda v: v > 39.5 or v < 34.0,
        "respiratory_rate": lambda v: v > 30 or v < 8,
    }

    if vital_type in critical_rules and critical_rules[vital_type](value):
        return "CRITICAL"

    # How far outside the range
    if value < min_val:
        deviation_pct = (min_val - value) / min_val * 100
    elif value > max_val:
        deviation_pct = (value - max_val) / max_val * 100
    else:
        return "LOW"  # Within range, shouldn't normally be called

    if deviation_pct > 20:
        return "HIGH"
    elif deviation_pct > 10:
        return "HIGH"
    else:
        return "MEDIUM"


def check_vital_ranges(fhir_bundle: dict) -> list[dict]:
    """
    Check all Observation resources in a FHIR Bundle against normal vital ranges.

    Iterates through Observation resources, extracts vital sign values, and
    flags any values outside the defined normal ranges.

    Args:
        fhir_bundle: A FHIR R4 Bundle dict containing Observation resources.

    Returns:
        List of risk dicts for any vitals outside normal range. Each dict has:
        risk_id, severity, category, description, recommended_action, fhir_reference.
    """
    risks = []
    entries = fhir_bundle.get("entry", [])

    for entry in entries:
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Observation":
            continue

        resource_id = resource.get("id", "unknown")

        # Check for blood pressure panel (has components)
        code = resource.get("code", {})
        codings = code.get("coding", [])
        loinc_code = None
        for coding in codings:
            if coding.get("system") == "http://loinc.org":
                loinc_code = coding.get("code")
                break

        # Handle BP panel with components
        if loinc_code == LOINC_BP_PANEL:
            components = resource.get("component", [])
            for comp in components:
                comp_codings = comp.get("code", {}).get("coding", [])
                comp_loinc = None
                for cc in comp_codings:
                    if cc.get("system") == "http://loinc.org":
                        comp_loinc = cc.get("code")
                        break

                if comp_loinc and comp_loinc in LOINC_TO_VITAL:
                    vital_type = LOINC_TO_VITAL[comp_loinc]
                    value_quantity = comp.get("valueQuantity", {})
                    value = value_quantity.get("value")

                    if value is not None:
                        range_info = VITAL_RANGES[vital_type]
                        if value < range_info["min"] or value > range_info["max"]:
                            severity = _classify_severity(vital_type, value, range_info)
                            direction = "below" if value < range_info["min"] else "above"
                            limit = range_info["min"] if direction == "below" else range_info["max"]

                            risks.append({
                                "risk_id": _generate_risk_id(),
                                "severity": severity,
                                "category": "ABNORMAL_VITAL",
                                "description": (
                                    f"{range_info['display']} is {value} {range_info['unit']}, "
                                    f"which is {direction} the normal range "
                                    f"({range_info['min']}-{range_info['max']} {range_info['unit']})"
                                ),
                                "recommended_action": (
                                    f"Verify {range_info['display'].lower()} reading. "
                                    f"If confirmed, escalate to attending physician."
                                    if severity in ("CRITICAL", "HIGH")
                                    else f"Monitor {range_info['display'].lower()} and reassess."
                                ),
                                "fhir_reference": f"Observation/{resource_id}",
                            })
            continue

        # Handle simple observations (HR, SpO2, temp, RR)
        if loinc_code and loinc_code in LOINC_TO_VITAL:
            vital_type = LOINC_TO_VITAL[loinc_code]
            value_quantity = resource.get("valueQuantity", {})
            value = value_quantity.get("value")

            if value is not None:
                range_info = VITAL_RANGES[vital_type]
                if value < range_info["min"] or value > range_info["max"]:
                    severity = _classify_severity(vital_type, value, range_info)
                    direction = "below" if value < range_info["min"] else "above"

                    risks.append({
                        "risk_id": _generate_risk_id(),
                        "severity": severity,
                        "category": "ABNORMAL_VITAL",
                        "description": (
                            f"{range_info['display']} is {value} {range_info['unit']}, "
                            f"which is {direction} the normal range "
                            f"({range_info['min']}-{range_info['max']} {range_info['unit']})"
                        ),
                        "recommended_action": (
                            f"Verify {range_info['display'].lower()} reading. "
                            f"If confirmed, escalate to attending physician."
                            if severity in ("CRITICAL", "HIGH")
                            else f"Monitor {range_info['display'].lower()} and reassess."
                        ),
                        "fhir_reference": f"Observation/{resource_id}",
                    })

    logger.info(f"Vital range check: {len(risks)} abnormal vitals flagged")
    return risks


def check_missing_critical_info(fhir_bundle: dict) -> list[dict]:
    """
    Check for missing critical information in a FHIR Bundle.

    Flags if essential clinical data is absent:
    - No vital signs recorded
    - No patient identifier
    - No medications listed
    - No allergy information

    Args:
        fhir_bundle: A FHIR R4 Bundle dict.

    Returns:
        List of risk dicts for missing critical information.
    """
    risks = []
    entries = fhir_bundle.get("entry", [])

    # Categorize resources
    has_patient = False
    has_patient_id = False
    has_observations = False
    has_medications = False
    has_allergies = False

    for entry in entries:
        resource = entry.get("resource", {})
        rt = resource.get("resourceType")

        if rt == "Patient":
            has_patient = True
            identifiers = resource.get("identifier", [])
            if identifiers and any(i.get("value") for i in identifiers):
                has_patient_id = True

        elif rt == "Observation":
            has_observations = True

        elif rt == "MedicationRequest":
            has_medications = True

        elif rt == "AllergyIntolerance":
            has_allergies = True

    # Flag missing items
    if not has_patient:
        risks.append({
            "risk_id": _generate_risk_id(),
            "severity": "HIGH",
            "category": "MISSING_INFO",
            "description": "No patient information found in the handoff note.",
            "recommended_action": "Confirm patient identity before proceeding with care.",
            "fhir_reference": "Bundle",
        })

    if has_patient and not has_patient_id:
        risks.append({
            "risk_id": _generate_risk_id(),
            "severity": "MEDIUM",
            "category": "MISSING_INFO",
            "description": "Patient has no medical record number (MRN) or identifier.",
            "recommended_action": "Verify patient identity using two identifiers per hospital policy.",
            "fhir_reference": "Bundle",
        })

    if not has_observations:
        risks.append({
            "risk_id": _generate_risk_id(),
            "severity": "HIGH",
            "category": "MISSING_INFO",
            "description": "No vital signs recorded in the handoff note.",
            "recommended_action": "Obtain a fresh set of vital signs immediately upon assuming care.",
            "fhir_reference": "Bundle",
        })

    if not has_medications:
        risks.append({
            "risk_id": _generate_risk_id(),
            "severity": "MEDIUM",
            "category": "MISSING_INFO",
            "description": "No medications listed in the handoff note.",
            "recommended_action": (
                "Review medication administration record (MAR) and confirm current orders."
            ),
            "fhir_reference": "Bundle",
        })

    if not has_allergies:
        risks.append({
            "risk_id": _generate_risk_id(),
            "severity": "MEDIUM",
            "category": "MISSING_INFO",
            "description": "No allergy information in the handoff note.",
            "recommended_action": (
                "Verify allergy status with patient/chart before administering any medications."
            ),
            "fhir_reference": "Bundle",
        })

    logger.info(f"Missing info check: {len(risks)} items flagged")
    return risks


def check_allergy_medication_conflicts(fhir_bundle: dict) -> list[dict]:
    """
    Check for allergy-medication conflicts in a FHIR Bundle.

    Specifically catches penicillin allergy + penicillin-class antibiotic
    prescriptions, which is one of the most dangerous and common conflicts.

    Args:
        fhir_bundle: A FHIR R4 Bundle dict.

    Returns:
        List of risk dicts for allergy-medication conflicts.
    """
    risks = []
    entries = fhir_bundle.get("entry", [])

    # Collect allergies
    allergies = []
    for entry in entries:
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "AllergyIntolerance":
            allergy_text = resource.get("code", {}).get("text", "").lower()
            allergy_codings = resource.get("code", {}).get("coding", [])
            for coding in allergy_codings:
                allergy_text += " " + coding.get("display", "").lower()
            allergies.append({
                "text": allergy_text.strip(),
                "id": resource.get("id", "unknown"),
            })

    # Collect medications
    medications = []
    for entry in entries:
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "MedicationRequest":
            med_text = resource.get("medicationCodeableConcept", {}).get("text", "").lower()
            med_codings = resource.get("medicationCodeableConcept", {}).get("coding", [])
            for coding in med_codings:
                med_text += " " + coding.get("display", "").lower()
            medications.append({
                "text": med_text.strip(),
                "id": resource.get("id", "unknown"),
            })

    # Check for penicillin allergy + penicillin-class drug
    has_penicillin_allergy = any(
        "penicillin" in a["text"] for a in allergies
    )

    if has_penicillin_allergy:
        allergy_ref = next(
            (a["id"] for a in allergies if "penicillin" in a["text"]),
            "unknown"
        )
        for med in medications:
            med_name_lower = med["text"].lower()
            # Check if the medication is in the penicillin class
            is_penicillin_class = any(
                drug in med_name_lower for drug in PENICILLIN_CLASS_DRUGS
            )
            if is_penicillin_class:
                risks.append({
                    "risk_id": _generate_risk_id(),
                    "severity": "CRITICAL",
                    "category": "ALLERGY_CONFLICT",
                    "description": (
                        f"CRITICAL ALLERGY CONFLICT: Patient has a documented penicillin allergy "
                        f"but has been prescribed {med['text'].title()}, which is a penicillin-class "
                        f"antibiotic. This could cause a severe allergic reaction including anaphylaxis."
                    ),
                    "recommended_action": (
                        "STOP administration of this medication immediately. "
                        "Contact prescribing physician (Dr.) to change to a non-penicillin antibiotic "
                        "(e.g., azithromycin, fluoroquinolone, or doxycycline for pneumonia). "
                        "Document the near-miss event per hospital incident reporting policy."
                    ),
                    "fhir_reference": f"AllergyIntolerance/{allergy_ref}, MedicationRequest/{med['id']}",
                })

    logger.info(f"Allergy-medication conflict check: {len(risks)} conflicts flagged")
    return risks
