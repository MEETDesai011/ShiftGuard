"""
ShiftGuard — End-to-End Test Script

Tests the complete pipeline:
1. Parse a realistic messy handoff note → FHIR Bundle
2. Flag critical risks → Risk list
3. Generate SBAR brief → Clinical handoff brief

The sample note contains multiple deliberate risks:
- Penicillin allergy + Amoxicillin prescription (CRITICAL allergy conflict)
- SpO2 91% (CRITICAL — below 95%)
- Elevated BP 158/94 (HIGH)
- Elevated HR 102 (MEDIUM)
- Overdue insulin (HIGH)
- Pending chest X-ray (MEDIUM)

Author: Meet Desai
"""

import sys
import json
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from server import parse_handoff_note, flag_critical_risks, generate_handoff_brief


# ──────────────────────────────────────────────────────────────
# Sample handoff note — realistic, messy, contains multiple risks
# ──────────────────────────────────────────────────────────────

SAMPLE_NOTE = """pt in bed 7B, john doe, 67yo male. came in yesterday with chest pain and sob. 
hx of T2DM and HTN. bp was 158/94 last check, hr around 102, sats 91% on 2L. 
temp 38.1. allergic to penicillin - severe reaction last time. 
on metformin 500mg BD, lisinopril 10mg OD - last dose was this morning. 
needs his 8pm insulin - glargine 20 units - NOT given yet. 
also started on amoxicillin by dr patel this afternoon for suspected pneumonia. 
chest xray pending. family has been asking for updates. 
handoff to night shift at 8pm."""


def print_header(title: str):
    """Print a formatted section header."""
    width = 70
    print("\n" + "═" * width)
    print(f"  {title}")
    print("═" * width)


def print_divider():
    print("─" * 70)


def main():
    print_header("SHIFTGUARD — END-TO-END TEST")
    print(f"\nSample Note:\n{SAMPLE_NOTE}")
    print_divider()

    # ─── Tool 1: Parse Handoff Note ──────────────────────────
    print_header("TOOL 1: parse_handoff_note")
    print("Parsing handoff note into FHIR R4 Bundle...")

    try:
        fhir_bundle = parse_handoff_note(note=SAMPLE_NOTE)

        # Count resources by type
        resource_counts = {}
        for entry in fhir_bundle.get("entry", []):
            rt = entry.get("resource", {}).get("resourceType", "Unknown")
            resource_counts[rt] = resource_counts.get(rt, 0) + 1

        print(f"\n✅ FHIR Bundle created successfully!")
        print(f"   Total resources: {len(fhir_bundle.get('entry', []))}")
        print(f"   Resource breakdown:")
        for rt, count in sorted(resource_counts.items()):
            print(f"     • {rt}: {count}")

        # Show metadata
        meta = fhir_bundle.get("_shiftguard_meta", {})
        print(f"\n   FHIR Push Status: {meta.get('fhir_push_status', 'unknown')}")
        if meta.get("fhir_server_url"):
            print(f"   FHIR Server: {meta['fhir_server_url']}")
        if meta.get("fhir_server_urls"):
            print(f"   Created resources on server:")
            for url in meta["fhir_server_urls"][:5]:
                print(f"     → {url}")
            if len(meta["fhir_server_urls"]) > 5:
                print(f"     ... and {len(meta['fhir_server_urls']) - 5} more")

        if meta.get("errors"):
            print(f"\n   ⚠️  Warnings: {len(meta['errors'])}")
            for err in meta["errors"]:
                print(f"     - {err}")

        # Print formatted bundle (abbreviated)
        print(f"\n   FHIR Bundle JSON (first 2 entries):")
        preview = dict(fhir_bundle)
        preview_entries = preview.get("entry", [])[:2]
        preview["entry"] = preview_entries
        preview.pop("_shiftguard_meta", None)
        preview.pop("_shiftguard_extracted", None)
        print(json.dumps(preview, indent=2, default=str)[:2000])
        if len(fhir_bundle.get("entry", [])) > 2:
            print(f"   ... ({len(fhir_bundle['entry']) - 2} more entries)")

    except Exception as e:
        print(f"\n❌ Tool 1 failed: {e}")
        import traceback
        traceback.print_exc()
        return

    print_divider()

    # ─── Tool 2: Flag Critical Risks ─────────────────────────
    print_header("TOOL 2: flag_critical_risks")
    print("Analyzing FHIR Bundle for patient safety risks...")

    try:
        risks = flag_critical_risks(fhir_bundle=fhir_bundle)

        print(f"\n✅ Risk analysis complete!")
        print(f"   Total risks flagged: {len(risks)}")

        # Severity breakdown
        severity_counts = {}
        for r in risks:
            sev = r.get("severity", "UNKNOWN")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        print(f"   Severity breakdown:")
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            count = severity_counts.get(sev, 0)
            if count > 0:
                icon = "🔴" if sev == "CRITICAL" else "🟠" if sev == "HIGH" else "🟡" if sev == "MEDIUM" else "🟢"
                print(f"     {icon} {sev}: {count}")

        # Print each risk
        print(f"\n   Detailed risks:")
        for i, risk in enumerate(risks, 1):
            severity = risk.get("severity", "?")
            category = risk.get("category", "?")
            description = risk.get("description", "?")
            action = risk.get("recommended_action", "?")

            print(f"\n   [{i}] [{severity}] {category}")
            print(f"       Description: {description}")
            print(f"       Action: {action}")
            print(f"       FHIR Ref: {risk.get('fhir_reference', 'N/A')}")

    except Exception as e:
        print(f"\n❌ Tool 2 failed: {e}")
        import traceback
        traceback.print_exc()
        risks = []

    print_divider()

    # ─── Tool 3: Generate Handoff Brief ──────────────────────
    print_header("TOOL 3: generate_handoff_brief")
    print("Generating SBAR clinical handoff brief...")

    try:
        brief = generate_handoff_brief(
            fhir_bundle=fhir_bundle,
            flagged_risks=risks,
        )

        print(f"\n✅ SBAR Brief generated!")
        print(f"   Word count: {len(brief.split())}")
        print(f"\n{'─' * 50}")
        print(brief)
        print(f"{'─' * 50}")

    except Exception as e:
        print(f"\n❌ Tool 3 failed: {e}")
        import traceback
        traceback.print_exc()

    # ─── Summary ─────────────────────────────────────────────
    print_header("TEST SUMMARY")
    print(f"  Resources extracted: {len(fhir_bundle.get('entry', []))}")
    print(f"  Risks flagged:       {len(risks)}")
    if severity_counts:
        for sev, count in sorted(severity_counts.items()):
            print(f"    • {sev}: {count}")
    print(f"  FHIR push:           {meta.get('fhir_push_status', 'unknown')}")
    print(f"  Brief generated:     {'Yes' if brief else 'No'}")

    # Check for the hero demo moment
    penicillin_flagged = any(
        "penicillin" in r.get("description", "").lower()
        and r.get("severity") == "CRITICAL"
        for r in risks
    )
    print(f"\n  🎯 Hero check — Penicillin + Amoxicillin conflict flagged: {'✅ YES' if penicillin_flagged else '❌ NO'}")

    spo2_flagged = any(
        "spo2" in r.get("description", "").lower() or "oxygen" in r.get("description", "").lower()
        for r in risks
    )
    print(f"  🎯 Hero check — SpO2 91% flagged as abnormal: {'✅ YES' if spo2_flagged else '❌ NO'}")

    print("\n" + "═" * 70)
    print("  TEST COMPLETE")
    print("═" * 70 + "\n")


if __name__ == "__main__":
    main()
