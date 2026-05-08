# 🛡️ ShiftGuard — Clinical Handoff Intelligence

> **Transforming chaotic shift handoffs into structured, safe, FHIR-compliant patient summaries — powered by AI.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FHIR R4](https://img.shields.io/badge/FHIR-R4-orange.svg)](https://www.hl7.org/fhir/R4/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-green.svg)](https://gofastmcp.com/)

---

## The Problem

Every day in hospitals around the world, nurses and doctors switch shifts. During this transition — called a **clinical handoff** — they pass patient status to the incoming clinician through verbal notes, paper forms, or basic templates.

**80% of serious medical errors involve miscommunication during handoffs.**

Information gets lost. Allergies are forgotten. Overdue medications slip through. A penicillin allergy goes unnoticed while amoxicillin drips into the IV line.

**ShiftGuard stops this.**

---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                    SHIFTGUARD PIPELINE                          │
│                                                                 │
│  📝 Raw Handoff Note                                           │
│  "pt bed 7B, john doe, 67yo,                                  │
│   bp 158/94, sats 91%,                                        │
│   allergic to penicillin,                                      │
│   started on amoxicillin..."                                   │
│         │                                                       │
│         ▼                                                       │
│  ┌─────────────────────┐                                       │
│  │  Tool 1: PARSE      │──── Gemini AI ────► FHIR R4 Bundle   │
│  │  parse_handoff_note  │                    (Patient, Vitals,  │
│  └──────────┬──────────┘                     Allergies, Meds,  │
│             │                                 Conditions)       │
│             ▼                                                   │
│  ┌─────────────────────┐     ┌─────────┐                      │
│  │  Tool 2: ANALYZE    │────►│ HAPI    │ Push/Pull FHIR Data  │
│  │  flag_critical_risks │     │ FHIR R4 │ (Interoperability)   │
│  └──────────┬──────────┘     └─────────┘                      │
│             │                                                   │
│             │  Rules Engine + Gemini AI                         │
│             │  ⚠️ CRITICAL: Penicillin allergy + Amoxicillin   │
│             │  ⚠️ CRITICAL: SpO2 91% (below 95%)              │
│             │  🔶 HIGH: BP 158/94, Overdue insulin             │
│             ▼                                                   │
│  ┌─────────────────────┐                                       │
│  │  Tool 3: BRIEF      │──── Gemini AI ────► SBAR Brief       │
│  │  generate_handoff    │                    (Situation,        │
│  │  _brief              │                     Background,       │
│  └─────────────────────┘                     Assessment,       │
│                                               Recommendation)   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/meetdesai/shiftguard.git
cd clinical-handoff-intelligence
pip install -r requirements.txt
```

### 2. Configure API Key

```bash
cp .env.example .env
# Edit .env and add your Gemini API key:
# GEMINI_API_KEY=your_key_here
```

Get a free Gemini API key at [aistudio.google.com](https://aistudio.google.com/).

### 3. Run the MCP Server

```bash
# Run as MCP server (HTTP transport)
python server.py

# Or use FastMCP CLI
fastmcp run server.py:mcp --transport streamable-http --port 8000
```

### 4. Run the Test

```bash
python test/test_server.py
```

---

## MCP Tools

### Tool 1: `parse_handoff_note`

**Input:** Raw unstructured handoff note (string)

**Output:** FHIR R4 Bundle containing:
- `Patient` — name, age, gender, bed number
- `Observation` — vitals (BP, HR, SpO2, Temp, RR)
- `AllergyIntolerance` — documented allergies
- `MedicationRequest` — current medications with dosage/timing
- `Condition` — diagnoses and complaints

```python
result = parse_handoff_note(
    note="pt bed 7B, john doe, 67yo male. bp 158/94, sats 91%..."
)
```

### Tool 2: `flag_critical_risks`

**Input:** FHIR Bundle (from Tool 1)

**Output:** Prioritized list of safety risks with severity, category, description, and recommended actions.

```python
risks = flag_critical_risks(fhir_bundle=result)
# Returns: [{"severity": "CRITICAL", "category": "ALLERGY_CONFLICT", ...}, ...]
```

### Tool 3: `generate_handoff_brief`

**Input:** FHIR Bundle + Risk list

**Output:** Professional SBAR clinical brief (under 250 words)

```python
brief = generate_handoff_brief(fhir_bundle=result, flagged_risks=risks)
```

---

## FHIR R4 Interoperability

All generated FHIR bundles are pushed to the **HAPI FHIR public test server** at:

```
https://hapi.fhir.org/baseR4
```

This is a free, publicly accessible FHIR R4 server — no authentication required. It demonstrates real healthcare interoperability: our AI-generated clinical data can be read by any FHIR-compliant EHR system.

### FHIR Resources Generated

| Resource | FHIR Profile | Coding System |
|----------|-------------|---------------|
| Patient | US Core Patient | — |
| Observation | Vital Signs | LOINC |
| AllergyIntolerance | US Core AllergyIntolerance | SNOMED CT |
| MedicationRequest | US Core MedicationRequest | RxNorm |
| Condition | US Core Condition | SNOMED CT |

### LOINC Codes Used

| Vital Sign | LOINC Code |
|-----------|------------|
| Blood Pressure | 55284-4 |
| Heart Rate | 8867-4 |
| SpO2 | 59408-5 |
| Temperature | 8310-5 |
| Respiratory Rate | 9279-1 |

---

## SHARP Context Integration

ShiftGuard supports **SHARP context propagation** — Prompt Opinion's extension to MCP for healthcare contexts. Pass a `sharp_context` parameter to any tool:

```python
result = parse_handoff_note(
    note="...",
    sharp_context={
        "patient_id": "12345",           # Fetch existing FHIR data
        "fhir_base_url": "https://...",  # Custom FHIR server
        "auth_token": "Bearer ..."       # Auth for private servers
    }
)
```

When `patient_id` is provided, ShiftGuard automatically fetches existing patient data (allergies, observations) from the FHIR server and merges it — making risk detection even more powerful.

---

## Deployment

### Railway

```bash
# Procfile is already included
railway up
```

ShiftGuard binds to `0.0.0.0` on the `PORT` environment variable (Railway sets this automatically).

### MCP Server Discovery

The server exposes metadata for MCP client discovery:

```json
{
  "name": "ShiftGuard — Clinical Handoff Intelligence",
  "version": "1.0.0",
  "description": "Transforms unstructured clinical handoff notes into FHIR-compliant patient summaries with AI-powered risk detection",
  "author": "Meet Desai",
  "tools": ["parse_handoff_note", "flag_critical_risks", "generate_handoff_brief"],
  "fhir_version": "R4",
  "sharp_compatible": true
}
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| MCP Framework | FastMCP 3.x |
| AI Model | Google Gemini 2.5 Flash |
| AI SDK | google-genai |
| FHIR Standard | HL7 FHIR R4 |
| FHIR Server | HAPI FHIR (public test server) |
| Data Validation | Pydantic 2.x |
| HTTP Client | Requests |
| Deployment | Railway.app |

---

## Project Structure

```
clinical-handoff-intelligence/
├── server.py                  # Main MCP server — all 3 tools
├── prompts/
│   ├── parse_prompt.txt       # LLM prompt: note → structured data
│   ├── risk_prompt.txt        # LLM prompt: clinical risk reasoning
│   └── brief_prompt.txt       # LLM prompt: SBAR brief generation
├── fhir/
│   ├── builder.py             # FHIR R4 resource constructors
│   └── client.py              # HAPI FHIR server push/pull client
├── rules/
│   └── risk_rules.py          # Programmatic risk detection rules
├── test/
│   └── test_server.py         # End-to-end test with sample note
├── .env.example               # Environment variable template
├── requirements.txt           # Python dependencies
├── Procfile                   # Railway deployment
└── README.md                  # This file
```

---


## Author

**Meet Desai** — Built for Prompt Opinion Hackathon

---

## License

MIT
