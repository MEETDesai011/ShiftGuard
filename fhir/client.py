"""
ShiftGuard — HAPI FHIR R4 Server Client

Handles push/pull operations against the HAPI FHIR public test server
(https://hapi.fhir.org/baseR4). This server is free, requires no auth,
and uses synthetic data only.

Author: Meet Desai
"""

import logging
import os
import copy
import requests

logger = logging.getLogger("shiftguard.fhir.client")

# Default to HAPI FHIR public test server
FHIR_BASE_URL = os.getenv("FHIR_BASE_URL", "https://hapi.fhir.org/baseR4")

# Standard FHIR headers
FHIR_HEADERS_POST = {
    "Content-Type": "application/fhir+json",
    "Accept": "application/fhir+json",
}

FHIR_HEADERS_GET = {
    "Accept": "application/fhir+json",
}

# Request timeout in seconds (HAPI public server can be slow)
REQUEST_TIMEOUT = 30


def _get_base_url(sharp_context: dict | None = None) -> str:
    """Get the FHIR base URL, preferring SHARP context if provided."""
    if sharp_context and sharp_context.get("fhir_base_url"):
        return sharp_context["fhir_base_url"].rstrip("/")
    return FHIR_BASE_URL.rstrip("/")


def _fix_intra_bundle_references(tx_bundle: dict) -> dict:
    """
    Convert direct resource references (e.g. Patient/uuid) to urn:uuid:uuid format
    so HAPI FHIR can resolve them within a transaction bundle.
    """
    # Build a map: resource_id -> urn:uuid:resource_id
    id_to_urn = {}
    for entry in tx_bundle.get("entry", []):
        resource = entry.get("resource", {})
        res_id = resource.get("id")
        if res_id:
            id_to_urn[res_id] = f"urn:uuid:{res_id}"

    # Walk through each entry and fix references
    for entry in tx_bundle.get("entry", []):
        resource = entry.get("resource", {})
        _fix_references_in_resource(resource, id_to_urn)

    return tx_bundle


def _fix_references_in_resource(obj, id_to_urn: dict):
    """Recursively find 'reference' fields and rewrite them to urn:uuid format."""
    if isinstance(obj, dict):
        if "reference" in obj:
            ref = obj["reference"]
            # Check if it matches "ResourceType/uuid" pattern
            if "/" in ref:
                parts = ref.split("/", 1)
                if len(parts) == 2 and parts[1] in id_to_urn:
                    obj["reference"] = id_to_urn[parts[1]]
        for value in obj.values():
            _fix_references_in_resource(value, id_to_urn)
    elif isinstance(obj, list):
        for item in obj:
            _fix_references_in_resource(item, id_to_urn)


def push_bundle_to_fhir(
    bundle: dict, sharp_context: dict | None = None
) -> dict:
    """
    POST a FHIR Bundle to the HAPI FHIR test server.

    Converts the bundle to a transaction type so all resources are created
    atomically. Returns the server response with assigned resource IDs.

    Args:
        bundle: A FHIR R4 Bundle dict (collection or transaction type).
        sharp_context: Optional SHARP context with custom FHIR server URL.

    Returns:
        The FHIR server response dict with assigned IDs, or an error dict.
    """
    base_url = _get_base_url(sharp_context)
    logger.info(f"Pushing FHIR bundle to: {base_url}")

    # Convert to transaction bundle for HAPI
    tx_bundle = copy.deepcopy(bundle)
    tx_bundle["type"] = "transaction"

    # Remove internal ShiftGuard metadata (not valid FHIR)
    tx_bundle.pop("_shiftguard_meta", None)
    tx_bundle.pop("_shiftguard_extracted", None)
    tx_bundle.pop("total", None)
    tx_bundle.pop("timestamp", None)

    # Ensure each entry has a request element and fix fullUrl
    for entry in tx_bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_type = resource.get("resourceType", "Resource")
        res_id = resource.get("id", "")

        # Ensure fullUrl is urn:uuid format
        entry["fullUrl"] = f"urn:uuid:{res_id}"

        if "request" not in entry:
            entry["request"] = {
                "method": "POST",
                "url": resource_type,
            }

    # Fix intra-bundle references (Patient/uuid -> urn:uuid:uuid)
    _fix_intra_bundle_references(tx_bundle)

    try:
        response = requests.post(
            base_url,
            json=tx_bundle,
            headers=FHIR_HEADERS_POST,
            timeout=REQUEST_TIMEOUT,
        )
        logger.info(f"FHIR server response status: {response.status_code}")

        if response.status_code in (200, 201):
            result = response.json()
            logger.info(
                f"Bundle pushed successfully. "
                f"Entries processed: {len(result.get('entry', []))}"
            )
            return result
        else:
            error_msg = response.text[:500] if response.text else "Unknown error"
            logger.error(
                f"FHIR push failed with status {response.status_code}: {error_msg}"
            )
            return {
                "error": True,
                "status_code": response.status_code,
                "message": error_msg,
            }

    except requests.exceptions.Timeout:
        logger.error(f"FHIR push timed out after {REQUEST_TIMEOUT}s")
        return {"error": True, "message": "Request timed out"}
    except requests.exceptions.ConnectionError as e:
        logger.error(f"FHIR server connection error: {e}")
        return {"error": True, "message": f"Connection error: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error pushing to FHIR: {e}")
        return {"error": True, "message": f"Unexpected error: {str(e)}"}


def get_patient_from_fhir(
    patient_id: str, sharp_context: dict | None = None
) -> dict | None:
    """
    GET a Patient resource from HAPI FHIR by ID.

    Args:
        patient_id: The FHIR server-assigned Patient resource ID.
        sharp_context: Optional SHARP context with custom FHIR server URL.

    Returns:
        The Patient resource dict, or None if not found.
    """
    base_url = _get_base_url(sharp_context)
    url = f"{base_url}/Patient/{patient_id}"
    logger.info(f"Fetching patient from: {url}")

    try:
        response = requests.get(
            url, headers=FHIR_HEADERS_GET, timeout=REQUEST_TIMEOUT
        )
        logger.info(f"GET Patient response status: {response.status_code}")

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            logger.warning(f"Patient {patient_id} not found on FHIR server")
            return None
        else:
            logger.error(f"Unexpected status {response.status_code} for Patient/{patient_id}")
            return None

    except requests.exceptions.Timeout:
        logger.error(f"GET Patient timed out after {REQUEST_TIMEOUT}s")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error fetching patient: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching patient: {e}")
        return None


def search_patient_by_name(
    name: str, sharp_context: dict | None = None
) -> list[dict]:
    """
    Search for patients by name on HAPI FHIR.

    Args:
        name: Patient name (or partial name) to search for.
        sharp_context: Optional SHARP context with custom FHIR server URL.

    Returns:
        List of matching Patient resource dicts.
    """
    base_url = _get_base_url(sharp_context)
    url = f"{base_url}/Patient"
    params = {"name": name, "_count": 10}
    logger.info(f"Searching patients by name '{name}' at: {url}")

    try:
        response = requests.get(
            url, params=params, headers=FHIR_HEADERS_GET, timeout=REQUEST_TIMEOUT
        )
        logger.info(f"Patient search response status: {response.status_code}")

        if response.status_code == 200:
            bundle = response.json()
            entries = bundle.get("entry", [])
            patients = [e["resource"] for e in entries if "resource" in e]
            logger.info(f"Found {len(patients)} patients matching '{name}'")
            return patients
        else:
            logger.error(f"Patient search failed: {response.status_code}")
            return []

    except requests.exceptions.Timeout:
        logger.error(f"Patient search timed out after {REQUEST_TIMEOUT}s")
        return []
    except Exception as e:
        logger.error(f"Unexpected error searching patients: {e}")
        return []


def get_patient_observations(
    patient_id: str, sharp_context: dict | None = None
) -> list[dict]:
    """
    GET all Observation resources for a patient from HAPI FHIR.

    Args:
        patient_id: The FHIR Patient resource ID.
        sharp_context: Optional SHARP context with custom FHIR server URL.

    Returns:
        List of Observation resource dicts.
    """
    base_url = _get_base_url(sharp_context)
    url = f"{base_url}/Observation"
    params = {"patient": patient_id, "_count": 50}
    logger.info(f"Fetching observations for Patient/{patient_id}")

    try:
        response = requests.get(
            url, params=params, headers=FHIR_HEADERS_GET, timeout=REQUEST_TIMEOUT
        )
        logger.info(f"GET Observations response status: {response.status_code}")

        if response.status_code == 200:
            bundle = response.json()
            entries = bundle.get("entry", [])
            observations = [e["resource"] for e in entries if "resource" in e]
            logger.info(f"Found {len(observations)} observations for Patient/{patient_id}")
            return observations
        else:
            logger.error(f"Observations fetch failed: {response.status_code}")
            return []

    except requests.exceptions.Timeout:
        logger.error(f"GET Observations timed out after {REQUEST_TIMEOUT}s")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching observations: {e}")
        return []


def get_patient_allergies(
    patient_id: str, sharp_context: dict | None = None
) -> list[dict]:
    """
    GET all AllergyIntolerance resources for a patient from HAPI FHIR.

    Args:
        patient_id: The FHIR Patient resource ID.
        sharp_context: Optional SHARP context with custom FHIR server URL.

    Returns:
        List of AllergyIntolerance resource dicts.
    """
    base_url = _get_base_url(sharp_context)
    url = f"{base_url}/AllergyIntolerance"
    params = {"patient": patient_id, "_count": 50}
    logger.info(f"Fetching allergies for Patient/{patient_id}")

    try:
        response = requests.get(
            url, params=params, headers=FHIR_HEADERS_GET, timeout=REQUEST_TIMEOUT
        )
        logger.info(f"GET Allergies response status: {response.status_code}")

        if response.status_code == 200:
            bundle = response.json()
            entries = bundle.get("entry", [])
            allergies = [e["resource"] for e in entries if "resource" in e]
            logger.info(f"Found {len(allergies)} allergies for Patient/{patient_id}")
            return allergies
        else:
            logger.error(f"Allergies fetch failed: {response.status_code}")
            return []

    except requests.exceptions.Timeout:
        logger.error(f"GET Allergies timed out after {REQUEST_TIMEOUT}s")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching allergies: {e}")
        return []
