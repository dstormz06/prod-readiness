"""The distilled prompt must lose length, not rules.

It is the version people actually paste, so anything the gate enforces or the
ledger decides has to survive the compression. These tests compare it against
the same sources the long framework is generated from.
"""
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
COMPACT = REPO / "standalone" / "AUDITOR-COMPACT.md"

sys.path.insert(0, str(REPO / "scripts"))
import absence_probe  # noqa: E402


@pytest.fixture(scope="module")
def doc():
    return COMPACT.read_text(encoding="utf-8")


def test_it_is_actually_compact(doc):
    assert len(doc.splitlines()) < 200, "the distilled prompt has grown back"
    assert len(doc) < 30_000


def test_every_control_survived(doc):
    listed = set(re.findall(r"\b([a-z][a-z0-9_]{2,})\b(?=[,\s·^*→])", doc))
    registry = {c["id"] for c in absence_probe.CONTROLS}
    assert registry <= listed, f"controls dropped: {sorted(registry - listed)}"


@pytest.mark.parametrize("control", sorted(c["id"] for c in absence_probe.CONTROLS))
def test_each_control_carries_its_scope_and_dependency(doc, control):
    by_id = {c["id"]: c for c in absence_probe.CONTROLS}
    row = re.search(rf"\b{control}(\^?)(\*?)(→[a-z_]+)?", doc)
    assert row, f"{control} is missing"
    infra, signal, dependency = row.group(1), row.group(2), row.group(3)
    assert bool(infra) == (by_id[control]["scope"] == "infra"), \
        f"{control} infra marker is wrong"
    assert bool(signal) == (control in absence_probe.SIGNAL_ONLY), \
        f"{control} branch-selector marker is wrong"
    expected = absence_probe.REQUIRES.get(control)
    assert (dependency[1:] if dependency else None) == expected, \
        f"{control} dependency marker is wrong"


def test_sinks_are_marked_as_sinks(doc):
    sinks = {c["id"] for c in absence_probe.CONTROLS if c["polarity"] == "sink"}
    sink_text = " ".join(re.findall(r"\*\*SINKS\*\*([^\n·]*)", doc))
    for sink in sinks:
        assert sink in sink_text, f"{sink} is not listed as a sink"


def test_the_promotion_rule_is_intact(doc):
    """The one rule that prevents most over-claiming."""
    assert "infra-scoped" in doc and "ships IaC" in doc
    assert "Absence here proves nothing" in doc
    assert "Never a `NOT_FOUND`" in doc, "the sink-silence rule is missing"


def test_all_three_evidence_states_and_their_conditions(doc):
    for state in ("CONFIRMED", "NOT_FOUND", "UNVERIFIED"):
        assert state in doc
    assert "No X found in reviewed scope" in doc


def test_every_gate_rule_survived(doc):
    """Each numbered rule the validator enforces must be recognisable here."""
    required = [
        "Valid JSON", "duplicate", "Prefix is one of", "Prefix matches",
        "`state` is exactly", "`severity` is P0", "`fix` present", "`owner` present",
        "`title` present", "`impact` present", "not a copy of `failure_path`",
        "CONFIRMED has evidence", "path.ext:line", "NOT_FOUND cites a probe",
        "is in the ledger", "zero** hits", "branch selector or an inapplicable",
        "restate as UNVERIFIED", "Absence is not phrased as fact",
        "UNVERIFIED has `resolve`", "not written in confirmed language",
        "P0 has `failure_path`", "P0 has `compensating`", "without a `see:`",
        "ledger exists",
    ]
    for phrase in required:
        assert phrase in doc, f"gate rule missing: {phrase!r}"


def test_the_overclaim_phrases_are_all_listed(doc):
    source = (REPO / "scripts" / "validate_findings.py").read_text()
    pattern = re.search(r"OVERCLAIM = re\.compile\(\s*r\"(.*?)\", re\.IGNORECASE\)",
                        source, re.DOTALL).group(1)
    literals = [p for p in re.split(r"\|", pattern.replace('"\n    r"', ""))
                if p and "{" not in p and "\\b" not in p.strip("\\b")]
    for phrase in ("there is no", "there are no", "does not exist", "do not exist",
                   "the system has no", "has never been", "is never"):
        assert phrase in doc, f"overclaim phrase missing: {phrase!r}"
        assert phrase in pattern, f"{phrase!r} is no longer in the validator"


def test_required_field_conditions_survived(doc):
    for rule in ("required for NOT_FOUND", "required for P0", "required for UNVERIFIED"):
        assert rule in doc


def test_the_verdict_rule_is_mechanical(doc):
    verdict = doc.split("Verdict rule")[1][:400]
    assert "any P0" in verdict and "HOLD" in verdict
    assert "FIX THEN SHIP" in verdict and "SHIP" in verdict
    assert "could not see" in doc


def test_all_seven_lenses_have_a_brief(doc):
    section = doc.split("## 9 · The seven lenses")[1]
    for lens in ("security", "backend", "database", "devops", "qa", "frontend", "ai-security"):
        assert f"**{lens}**" in section, f"{lens} has no brief"


def test_lens_signal_table_survived(doc):
    for signal in ("frontend_present", "llm_sdk", "message_broker", "test_files", "iac"):
        assert signal in doc.split("**Lens signals:**")[1].split("\n\n")[0]


def test_the_invariants_survived(doc):
    for invariant in ("Read-only", "One evidence pass", "Uncertainty never raises severity",
                      "Secrets by location and kind only", "no artefact on disk is not done"):
        assert invariant in doc


def test_ownership_table_survived(doc):
    for issue in ("sequencing", "backups", "cache", "replay", "DLQ", "smoke",
                  "validation", "PII", "secrets in CI"):
        assert issue.lower() in doc.lower().split("**ownership**")[1][:1200]


def test_the_degraded_mode_contract_survived(doc):
    assert "cannot execute code" in doc
    assert "Concealing the degrade is not" in doc
    assert "hit count you did not earn" in doc


def test_asd_ste100_rules_survived(doc):
    section = doc.split("ASD-STE100")[-1]
    for rule in ("One idea per sentence", "Active voice", "hedging", "verbatim"):
        assert rule in section
