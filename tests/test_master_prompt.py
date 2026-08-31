"""The framework document must stay true to the plugin it was distilled from.

The document is the deliverable people copy into another host, so the failure
mode that matters is silent drift: a mandate edited in `agents/` but not in the
document, a control added to the probe registry but missing from the catalogue,
or an engine listing that no longer matches the file it claims to be.
"""
import hashlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "standalone" / "PRODUCTION-READINESS-AUDITOR.md"
ENGINE = REPO / "standalone" / "readiness_engine.py"
AGENTS = REPO / "agents"

sys.path.insert(0, str(REPO / "scripts"))
import absence_probe  # noqa: E402

LENSES = ["security", "backend", "database", "devops", "qa", "frontend", "ai-security"]


@pytest.fixture(scope="module")
def doc():
    return DOC.read_text(encoding="utf-8")


def test_every_injection_was_resolved(doc):
    assert "<!-- INJECT:" not in doc


def test_code_fences_are_balanced(doc):
    assert len([l for l in doc.splitlines() if l.startswith("```")]) % 2 == 0


def test_the_outline_is_flat_and_complete(doc):
    headings = [l for l in doc.splitlines() if l.startswith("## ")]
    expected = [f"## {n}." for n in range(0, 17)] + ["## Appendix A", "## Appendix B",
                                                    "## Appendix C", "## Appendix D"]
    for prefix in expected:
        assert any(h.startswith(prefix) for h in headings), f"missing section {prefix}"


def test_mandate_headings_do_not_compete_with_document_sections(doc):
    """A mandate's own headings are demoted, so the outline stays readable."""
    for heading in ("## Evidence discipline", "## Read before you look at any source",
                    "## Output", "## Language - write in ASD-STE100"):
        assert f"\n{heading}\n" not in doc, f"{heading!r} was not demoted"


def test_the_embedded_engine_is_the_engine(doc):
    """Appendix B must be the file it says it is, byte for byte."""
    match = re.search(r"## Appendix B.*?```python\n(.*?)\n```", doc, re.DOTALL)
    assert match, "Appendix B has no python listing"
    assert match.group(1) + "\n" == ENGINE.read_text(encoding="utf-8")


def test_the_published_digest_matches_the_engine(doc):
    digest = hashlib.sha256(ENGINE.read_bytes()).hexdigest()
    claimed = set(re.findall(r"expect ([0-9a-f]{64})", doc))
    assert claimed, "the document publishes no digest to verify a transferred engine against"
    assert claimed == {digest}


def test_every_control_reaches_the_catalogue(doc):
    """A probe the engine runs but the document omits is invisible in tier 3."""
    catalogue = doc.split("## Appendix A")[1].split("## Appendix B")[0]
    listed = set(re.findall(r"^\| `([a-z0-9_]+)` \|", catalogue, re.MULTILINE))
    registry = {c["id"] for c in absence_probe.CONTROLS}
    assert listed == registry, (
        f"missing from the catalogue: {sorted(registry - listed)}; "
        f"not in the registry: {sorted(listed - registry)}")


def test_the_catalogue_states_the_control_count(doc):
    assert str(len(absence_probe.CONTROLS)) in doc.split("## Appendix A")[1][:400]


def test_catalogue_rows_carry_polarity_scope_and_patterns(doc):
    catalogue = doc.split("## Appendix A")[1].split("## Appendix B")[0]
    by_id = {c["id"]: c for c in absence_probe.CONTROLS}
    rows = re.findall(r"^\| `([a-z0-9_]+)` \| ([^|]*)\| ([^|]*)\| ([^|]*)\| ([^|]*)\| (.*) \|$",
                      catalogue, re.MULTILINE)
    assert len(rows) == len(by_id)
    for control_id, _label, kind, scope, _needs, patterns in rows:
        control = by_id[control_id]
        assert scope.strip() == control["scope"]
        if control_id in absence_probe.SIGNAL_ONLY:
            assert kind.strip() == "branch selector"
        else:
            assert kind.strip() == control["polarity"]
        expected = len(control["content"]) + len(control["paths"])
        assert len(patterns.split("<br>")) == expected, f"{control_id} lost a search pattern"


def test_dependency_requirements_are_published(doc):
    catalogue = doc.split("## Appendix A")[1].split("## Appendix B")[0]
    for control_id, dependency in absence_probe.REQUIRES.items():
        row = re.search(rf"^\| `{control_id}` \|.*$", catalogue, re.MULTILINE)
        assert row, f"{control_id} is missing from the catalogue"
        assert f"`{dependency}`" in row.group(0), f"{control_id} does not declare it needs {dependency}"


@pytest.mark.parametrize("lens", LENSES)
def test_each_mandate_is_carried_over_verbatim(doc, lens):
    """Front matter off, headings demoted, every other line unchanged."""
    source = (AGENTS / f"lens-{lens}.md").read_text(encoding="utf-8")
    body = re.sub(r"^---\n.*?\n---\n", "", source, count=1, flags=re.DOTALL).strip()
    for line in body.splitlines():
        if not line.strip():
            continue
        expected = re.sub(r"^(#{1,5}) ", r"\1# ", line)
        assert expected in doc, f"lens-{lens}.md line missing from the document: {line[:70]!r}"


@pytest.mark.parametrize("lens", LENSES)
def test_each_mandate_names_its_own_findings_file(doc, lens):
    assert f".readiness-audit/findings/{lens}.json" in doc


def test_the_validation_law_states_every_message_the_gate_emits(doc):
    """A rule the gate enforces but the document omits cannot be met in tier 3."""
    gate = (REPO / "scripts" / "validate_findings.py").read_text(encoding="utf-8")
    for message in ("no fix given", "no owner lens declared", "no impact given",
                    "CONFIRMED requires evidence", "must cite file:line",
                    "NOT_FOUND requires a probe id", "is not in the absence ledger",
                    "this control is present", "cannot support a finding",
                    "absence here proves nothing", "absence is phrased as established fact",
                    "UNVERIFIED requires resolve", "written in confirmed language",
                    "P0 requires failure-path", "P0 requires compensating",
                    "duplicate finding id", "same underlying issue",
                    "impact repeats failure-path"):
        assert message in gate, f"{message!r} is no longer a rule the gate enforces"
        assert message in doc, f"{message!r} is enforced by the gate but absent from section 12"


def test_the_overclaim_phrases_are_published(doc):
    for phrase in ("there is no", "there are no", "does not exist", "do not exist",
                   "the system has no", "has never been", "is never"):
        assert f"`{phrase}`" in doc, f"the overclaim detector's {phrase!r} is undocumented"


def test_the_ledger_verdicts_are_all_explained(doc):
    for verdict in ("SIGNAL_PRESENT", "NO_SIGNAL_IN_SCOPE", "OUT_OF_SCOPE_UNSEEN",
                    "SINK_PRESENT", "NO_SINK_FOUND", "NOT_APPLICABLE"):
        assert verdict in doc, f"{verdict} is emitted by the probe but never explained"


def test_the_invariants_survived(doc):
    for invariant in ("Read-only", "One evidence pass, seven evaluations",
                      "Uncertainty never escalates severity",
                      "Secrets are reported by location and kind only",
                      "Each stage persists before the next begins"):
        assert invariant in doc


def test_the_verdict_rule_is_stated_and_mechanical(doc):
    verdict = doc.split("### The verdict law")[1].split("###")[0]
    assert "Any P0" in verdict and "HOLD" in verdict
    assert "FIX_THEN_SHIP" in verdict and "SHIP" in verdict


def test_every_engine_command_is_documented(doc):
    for command in ("init", "status", "set-stage", "set-lenses", "archive", "scan",
                    "probe", "validate", "render", "report", "assemble", "serve", "selftest"):
        assert re.search(rf"\b{re.escape(command)}\b", doc), f"{command} is undocumented"


def test_the_degraded_tier_has_an_honest_contract(doc):
    tiers = doc.split("## 3. Capability tiers")[1].split("## 4.")[0]
    assert "T3" in tiers
    assert "It is never `NOT_FOUND`" in tiers
    assert "Degrading is legitimate. Concealing the degrade is not." in tiers


def test_known_limits_are_declared(doc):
    limits = doc.split("### Known limits")[1]
    assert "lexical, not semantic" in limits
    assert "is not proof of safety" in limits
    assert "no authentication" in limits
    assert "matches its own probes" in limits


def test_the_document_never_leaks_a_secret_shaped_example(doc):
    """The framework forbids quoting secret values; it must not model the habit."""
    for pattern in (r"sk_live_[A-Za-z0-9]{6,}", r"AKIA[0-9A-Z]{12,}",
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"):
        assert not re.search(pattern, doc), f"the document contains a {pattern!r} shaped string"
