"""
ShiftGuard — FHIR R4 Resource Builder

Constructs valid FHIR R4 resources (Patient, Observation, AllergyIntolerance,
MedicationRequest, Condition) and bundles them into a FHIR Bundle.

All resources follow the HL7 FHIR R4 specification:
https://www.hl7.org/fhir/R4/

Author: Meet Desai
"""

import uuid
from datetime import datetime, timezone


# ──────────────────────────────────────────────────────────────
# LOINC codes for vital sign observations
# ──────────────────────────────────────────────────────────────

LOINC_CODES = {
    "blood_pressure": {
        "code": "55284-4",
        "display": "Blood pressure systolic and diastolic",
        "system": "http://loinc.org",
    },
    "blood_pressure_systolic": {
        "code": "8480-6",
        "display": "Systolic blood pressure",
        "system": "http://loinc.org",
    },
    "blood_pressure_diastolic": {
        "code": "8462-4",
        "display": "Diastolic blood pressure",
        "system": "http://loinc.org",
    },
    "heart_rate": {
        "code": "8867-4",
        "display": "Heart rate",
        "system": "http://loinc.org",
    },
    "spo2": {
        "code": "59408-5",
        "display": "Oxygen saturation in Arterial blood by Pulse oximetry",
        "system": "http://loinc.org",
    },
    "temperature": {
        "code": "8310-5",
        "display": "Body temperature",
        "system": "http://loinc.org",
    },
    "respiratory_rate": {
        "code": "9279-1",
        "display": "Respiratory rate",
        "system": "http://loinc.org",
    },
}

# Maps common vital type strings to their LOINC key
VITAL_TYPE_MAP = {
    "bp": "blood_pressure",
    "blood_pressure": "blood_pressure",
    "blood pressure": "blood_pressure",
    "systolic": "blood_pressure_systolic",
    "diastolic": "blood_pressure_diastolic",
    "hr": "heart_rate",
    "heart_rate": "heart_rate",
    "heart rate": "heart_rate",
    "pulse": "heart_rate",
    "spo2": "spo2",
    "sats": "spo2",
    "oxygen_saturation": "spo2",
    "oxygen saturation": "spo2",
    "temp": "temperature",
    "temperature": "temperature",
    "body_temperature": "temperature",
    "rr": "respiratory_rate",
    "respiratory_rate": "respiratory_rate",
    "respiratory rate": "respiratory_rate",
    "resp_rate": "respiratory_rate",
}


def _generate_id() -> str:
    """Generate a unique resource ID."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────
# Resource builders
# ──────────────────────────────────────────────────────────────


def build_patient(
    patient_id: str | None = None,
    name: str = "Unknown",
    age: int | None = None,
    gender: str | None = None,
    bed_number: str | None = None,
) -> dict:
    """
    Build a FHIR R4 Patient resource.

    Args:
        patient_id: Optional identifier for the patient (MRN or assigned ID).
        name: Full name of the patient.
        age: Age in years (stored as extension since FHIR uses birthDate).
        gender: 'male', 'female', 'other', or 'unknown'.
        bed_number: Hospital bed number (stored as extension).

    Returns:
        A valid FHIR R4 Patient resource dict.
    """
    resource_id = _generate_id()

    # Parse name into family / given
    name_parts = name.strip().split() if name else ["Unknown"]
    family_name = name_parts[-1] if len(name_parts) > 1 else name_parts[0]
    given_names = name_parts[:-1] if len(name_parts) > 1 else []

    patient = {
        "resourceType": "Patient",
        "id": resource_id,
        "meta": {
            "profile": ["http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"],
            "lastUpdated": _now_iso(),
        },
        "identifier": [],
        "name": [
            {
                "use": "official",
                "family": family_name,
                "given": given_names if given_names else [family_name],
                "text": name,
            }
        ],
        "active": True,
    }

    # Patient ID / MRN
    if patient_id:
        patient["identifier"].append(
            {
                "use": "usual",
                "type": {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                            "code": "MR",
                            "display": "Medical Record Number",
                        }
                    ]
                },
                "value": patient_id,
            }
        )

    # Gender
    if gender:
        gender_lower = gender.lower().strip()
        if gender_lower in ("male", "female", "other", "unknown"):
            patient["gender"] = gender_lower
        elif gender_lower in ("m",):
            patient["gender"] = "male"
        elif gender_lower in ("f",):
            patient["gender"] = "female"

    # Extensions for age and bed number (not standard FHIR fields)
    extensions = []
    if age is not None:
        extensions.append(
            {
                "url": "http://shiftguard.dev/fhir/StructureDefinition/patient-age",
                "valueInteger": int(age),
            }
        )
    if bed_number:
        extensions.append(
            {
                "url": "http://shiftguard.dev/fhir/StructureDefinition/bed-number",
                "valueString": str(bed_number),
            }
        )
    if extensions:
        patient["extension"] = extensions

    return patient


def build_observation(
    patient_id: str,
    vital_type: str,
    value: float | str,
    unit: str,
    status: str = "final",
) -> dict:
    """
    Build a FHIR R4 Observation resource for a vital sign.

    Args:
        patient_id: The FHIR Patient resource ID to reference.
        vital_type: Type of vital sign (e.g., 'heart_rate', 'spo2', 'bp').
        value: Numeric value or string (for BP like '158/94').
        unit: Unit of measurement (e.g., 'bpm', '%', 'mmHg').
        status: Observation status (default: 'final').

    Returns:
        A valid FHIR R4 Observation resource dict, or a list of dicts for BP.
    """
    # Normalize vital type
    vital_key = VITAL_TYPE_MAP.get(vital_type.lower().strip(), vital_type.lower().strip())

    # Handle blood pressure specially — it has components
    if vital_key == "blood_pressure" and isinstance(value, str) and "/" in str(value):
        return _build_bp_observation(patient_id, value, unit, status)

    # Look up LOINC code
    loinc = LOINC_CODES.get(vital_key)
    if not loinc:
        # Fallback: use the vital_type as display text
        loinc = {
            "code": "unknown",
            "display": vital_type,
            "system": "http://loinc.org",
        }

    resource_id = _generate_id()

    # Parse numeric value
    numeric_value = None
    if isinstance(value, (int, float)):
        numeric_value = float(value)
    else:
        try:
            numeric_value = float(str(value).strip().rstrip("%"))
        except (ValueError, TypeError):
            numeric_value = None

    observation = {
        "resourceType": "Observation",
        "id": resource_id,
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/vitalsigns"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": loinc["system"],
                    "code": loinc["code"],
                    "display": loinc["display"],
                }
            ],
            "text": loinc["display"],
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _now_iso(),
    }

    if numeric_value is not None:
        observation["valueQuantity"] = {
            "value": numeric_value,
            "unit": unit,
            "system": "http://unitsofmeasure.org",
            "code": unit,
        }
    else:
        observation["valueString"] = str(value)

    return observation


def _build_bp_observation(
    patient_id: str, bp_value: str, unit: str, status: str
) -> dict:
    """Build a FHIR R4 Observation for blood pressure with systolic/diastolic components."""
    parts = bp_value.split("/")
    systolic = float(parts[0].strip())
    diastolic = float(parts[1].strip()) if len(parts) > 1 else None

    resource_id = _generate_id()
    loinc = LOINC_CODES["blood_pressure"]

    observation = {
        "resourceType": "Observation",
        "id": resource_id,
        "meta": {
            "profile": ["http://hl7.org/fhir/StructureDefinition/vitalsigns"],
            "lastUpdated": _now_iso(),
        },
        "status": status,
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital Signs",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": loinc["system"],
                    "code": loinc["code"],
                    "display": loinc["display"],
                }
            ],
            "text": "Blood Pressure",
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _now_iso(),
        "component": [
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": LOINC_CODES["blood_pressure_systolic"]["code"],
                            "display": LOINC_CODES["blood_pressure_systolic"]["display"],
                        }
                    ]
                },
                "valueQuantity": {
                    "value": systolic,
                    "unit": unit or "mmHg",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            }
        ],
    }

    if diastolic is not None:
        observation["component"].append(
            {
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": LOINC_CODES["blood_pressure_diastolic"]["code"],
                            "display": LOINC_CODES["blood_pressure_diastolic"]["display"],
                        }
                    ]
                },
                "valueQuantity": {
                    "value": diastolic,
                    "unit": unit or "mmHg",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm[Hg]",
                },
            }
        )

    return observation


def build_allergy(
    patient_id: str,
    substance: str,
    severity: str = "moderate",
    criticality: str = "high",
    reaction_description: str | None = None,
) -> dict:
    """
    Build a FHIR R4 AllergyIntolerance resource.

    Args:
        patient_id: The FHIR Patient resource ID to reference.
        substance: Name of the allergen (e.g., 'Penicillin').
        severity: 'mild', 'moderate', or 'severe'.
        criticality: 'low', 'high', or 'unable-to-assess'.
        reaction_description: Optional description of the reaction.

    Returns:
        A valid FHIR R4 AllergyIntolerance resource dict.
    """
    resource_id = _generate_id()

    # Normalize severity
    severity_lower = severity.lower().strip() if severity else "moderate"
    if severity_lower not in ("mild", "moderate", "severe"):
        severity_lower = "moderate"

    # Normalize criticality
    criticality_lower = criticality.lower().strip() if criticality else "high"
    if criticality_lower not in ("low", "high", "unable-to-assess"):
        criticality_lower = "high"

    allergy = {
        "resourceType": "AllergyIntolerance",
        "id": resource_id,
        "meta": {
            "profile": [
                "http://hl7.org/fhir/us/core/StructureDefinition/us-core-allergyintolerance"
            ],
            "lastUpdated": _now_iso(),
        },
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active",
                    "display": "Active",
                }
            ]
        },
        "verificationStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": "confirmed",
                    "display": "Confirmed",
                }
            ]
        },
        "type": "allergy",
        "category": ["medication"],
        "criticality": criticality_lower,
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "display": substance,
                }
            ],
            "text": substance,
        },
        "patient": {"reference": f"Patient/{patient_id}"},
        "recordedDate": _now_iso(),
    }

    # Add reaction if description provided
    if reaction_description:
        allergy["reaction"] = [
            {
                "description": reaction_description,
                "severity": severity_lower,
            }
        ]

    return allergy


def build_medication_request(
    patient_id: str,
    medication_name: str,
    dosage: str | None = None,
    timing: str | None = None,
    status: str = "active",
    notes: str | None = None,
) -> dict:
    """
    Build a FHIR R4 MedicationRequest resource.

    Args:
        patient_id: The FHIR Patient resource ID to reference.
        medication_name: Name of the medication (e.g., 'Metformin').
        dosage: Dosage string (e.g., '500mg').
        timing: Timing/frequency (e.g., 'BD', 'OD', 'PRN').
        status: 'active', 'completed', 'stopped', etc.
        notes: Additional notes about the medication.

    Returns:
        A valid FHIR R4 MedicationRequest resource dict.
    """
    resource_id = _generate_id()

    # Normalize status
    valid_statuses = (
        "active", "on-hold", "cancelled", "completed",
        "entered-in-error", "stopped", "draft", "unknown",
    )
    status_lower = status.lower().strip() if status else "active"
    if status_lower not in valid_statuses:
        status_lower = "active"

    # Expand common timing abbreviations
    timing_map = {
        "OD": "Once daily",
        "BD": "Twice daily",
        "TDS": "Three times daily",
        "QDS": "Four times daily",
        "PRN": "As needed",
        "STAT": "Immediately",
        "NOC": "At night",
        "MANE": "In the morning",
    }
    timing_display = timing_map.get(timing.upper(), timing) if timing else None

    med_request = {
        "resourceType": "MedicationRequest",
        "id": resource_id,
        "meta": {
            "profile": [
                "http://hl7.org/fhir/us/core/StructureDefinition/us-core-medicationrequest"
            ],
            "lastUpdated": _now_iso(),
        },
        "status": status_lower,
        "intent": "order",
        "medicationCodeableConcept": {
            "coding": [
                {
                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "display": medication_name,
                }
            ],
            "text": medication_name,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "authoredOn": _now_iso(),
    }

    # Dosage instruction
    dosage_instruction = {}
    if dosage:
        dosage_instruction["text"] = dosage
        # Try to extract numeric dose
        dose_parts = dosage.lower().replace("mg", " mg").replace("units", " units").split()
        for i, part in enumerate(dose_parts):
            try:
                dose_val = float(part)
                dose_unit = dose_parts[i + 1] if i + 1 < len(dose_parts) else "mg"
                dosage_instruction["doseAndRate"] = [
                    {
                        "doseQuantity": {
                            "value": dose_val,
                            "unit": dose_unit,
                            "system": "http://unitsofmeasure.org",
                        }
                    }
                ]
                break
            except (ValueError, IndexError):
                continue

    if timing_display:
        dosage_instruction["timing"] = {
            "code": {
                "text": timing_display,
            }
        }
        if timing:
            dosage_instruction.setdefault("text", "")
            if dosage_instruction["text"]:
                dosage_instruction["text"] += f" {timing_display}"
            else:
                dosage_instruction["text"] = timing_display

    if dosage_instruction:
        med_request["dosageInstruction"] = [dosage_instruction]

    # Notes
    if notes:
        med_request["note"] = [{"text": notes}]

    return med_request


def build_condition(
    patient_id: str,
    description: str,
    clinical_status: str = "active",
    severity: str | None = None,
) -> dict:
    """
    Build a FHIR R4 Condition resource.

    Args:
        patient_id: The FHIR Patient resource ID to reference.
        description: Clinical description of the condition.
        clinical_status: 'active', 'recurrence', 'relapse', 'inactive', 'remission', 'resolved'.
        severity: 'mild', 'moderate', or 'severe'.

    Returns:
        A valid FHIR R4 Condition resource dict.
    """
    resource_id = _generate_id()

    # Normalize clinical status
    valid_statuses = ("active", "recurrence", "relapse", "inactive", "remission", "resolved")
    cs_lower = clinical_status.lower().strip() if clinical_status else "active"
    if cs_lower not in valid_statuses:
        cs_lower = "active"

    condition = {
        "resourceType": "Condition",
        "id": resource_id,
        "meta": {
            "profile": [
                "http://hl7.org/fhir/us/core/StructureDefinition/us-core-condition"
            ],
            "lastUpdated": _now_iso(),
        },
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": cs_lower,
                    "display": cs_lower.capitalize(),
                }
            ]
        },
        "verificationStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-ver-status",
                    "code": "confirmed",
                    "display": "Confirmed",
                }
            ]
        },
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                        "code": "encounter-diagnosis",
                        "display": "Encounter Diagnosis",
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://snomed.info/sct",
                    "display": description,
                }
            ],
            "text": description,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "recordedDate": _now_iso(),
    }

    # Severity
    if severity:
        sev_lower = severity.lower().strip()
        if sev_lower in ("mild", "moderate", "severe"):
            snomed_severity = {
                "mild": {"code": "255604002", "display": "Mild"},
                "moderate": {"code": "6736007", "display": "Moderate"},
                "severe": {"code": "24484000", "display": "Severe"},
            }
            condition["severity"] = {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        **snomed_severity[sev_lower],
                    }
                ]
            }

    return condition


def build_fhir_bundle(resources: list, bundle_type: str = "collection") -> dict:
    """
    Wrap a list of FHIR resources into a FHIR R4 Bundle.

    Args:
        resources: List of FHIR resource dicts.
        bundle_type: Bundle type — 'collection' for local use, 'transaction' for HAPI push.

    Returns:
        A valid FHIR R4 Bundle dict.
    """
    bundle_id = _generate_id()

    entries = []
    for resource in resources:
        entry = {
            "fullUrl": f"urn:uuid:{resource.get('id', _generate_id())}",
            "resource": resource,
        }

        # Add request entry for transaction bundles
        if bundle_type == "transaction":
            resource_type = resource.get("resourceType", "Resource")
            entry["request"] = {
                "method": "POST",
                "url": resource_type,
            }

        entries.append(entry)

    return {
        "resourceType": "Bundle",
        "id": bundle_id,
        "meta": {
            "lastUpdated": _now_iso(),
        },
        "type": bundle_type,
        "total": len(resources),
        "entry": entries,
        "timestamp": _now_iso(),
    }
