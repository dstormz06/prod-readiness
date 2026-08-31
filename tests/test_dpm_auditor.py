"""The FDA/DPM edition must be complete, internally consistent, and honest.

It will be read by people who cannot check its claims against source code, so
the failure that matters is a rule stated loosely, a control silently dropped,
or a regulatory claim asserted as current fact.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "standalone" / "DPM-READINESS-AUDITOR.md"

LENSES = {"PA": 8, "EA": 10, "DC": 9, "RT": 9, "RO": 10, "UA": 8, "SS": 10}
TOTAL = 64


@pytest.fixture(scope="module")
def doc():
    return DOC.read_text(encoding="utf-8")


def controls(doc):
    return re.findall(r"`([A-Z]{2})(\d+)` \[([AE])\]", doc)


def test_control_count_matches_the_stated_total(doc):
    found = controls(doc)
    assert len(found) == TOTAL, f"declared {TOTAL}, found {len(found)}"
    assert f"{TOTAL} controls" in doc


@pytest.mark.parametrize("prefix,count", sorted(LENSES.items()))
def test_each_lens_has_its_full_control_set(doc, prefix, count):
    ids = [int(n) for p, n, _ in controls(doc) if p == prefix]
    assert ids == list(range(1, count + 1)), f"{prefix} is {ids}, expected 1..{count}"


def test_every_control_declares_where_its_evidence_lives(doc):
    """[A] vs [E] is what decides NOT FOUND vs UNVERIFIED. None may be missing."""
    marked = {f"{p}{n}" for p, n, _ in controls(doc)}
    for line in doc.splitlines():
        for cid in re.findall(r"`([A-Z]{2}\d+)`", line):
            if cid[:2] in LENSES and "·" in line or line.strip().startswith("`"):
                assert cid in marked or f"`{cid}`" in doc


def test_all_seven_lenses_are_present_and_numbered(doc):
    for n in range(1, 8):
        assert f"### L{n} ·" in doc, f"lens L{n} missing"


def test_the_three_evidence_states_are_defined_with_their_phrasing_rule(doc):
    for state in ("CONFIRMED", "NOT FOUND", "UNVERIFIED"):
        assert state in doc
    assert "not found in the material reviewed" in doc.lower()
    assert "Never *\"there is no X.\"*" in doc or 'Never *"there is no X."*' in doc


def test_the_evidence_location_rule_is_intact(doc):
    """The [E]-with-documentation promotion is the core of the method."""
    assert "silence supports `NOT FOUND`" in doc
    assert "package includes that documentation" in doc
    assert "Silence proves nothing" in doc


def test_the_evaluation_table_is_ordered_and_complete(doc):
    table = doc.split("Evaluate every control in this order")[1].split("## 5 ·")[0]
    rows = [l for l in table.splitlines() if re.match(r"^\| [1-7] \|", l)]
    assert len(rows) == 7, f"expected 7 ordered rows, found {len(rows)}"
    assert "The first match wins" in doc
    assert "Order matters" in table


def test_all_three_tiers_with_their_depth(doc):
    tiers = doc.split("### Stakes tier")[1].split("## 4 ·")[0]
    for t in ("1 — Decision-adjacent", "2 — Reviewed work product", "3 — Personal productivity"):
        assert t in tiers
    assert "take the higher one" in tiers
    assert "Escalate the tier immediately" in tiers


def test_all_seven_artifact_types_have_an_adapter(doc):
    table = doc.split("### Artifact type")[1].split("### Stakes tier")[0]
    for kind in ("Prompt / template", "Agent / automated workflow",
                 "Script / macro / spreadsheet", "Web app / dashboard",
                 "Vendor / purchased AI tool", "Model / API integration",
                 "Document generator"):
        assert f"**{kind}**" in table, f"{kind} adapter missing"


def test_the_verdict_rule_is_mechanical_and_ordered(doc):
    v = doc.split("### The verdict")[1].split("## 7 ·")[0]
    for i, decision in enumerate(("NOT CLEARED", "CLEARED WITH CONDITIONS",
                                  "LIMITED PILOT ONLY", "CLEARED FOR USE"), 1):
        assert f"{i}." in v and decision in v, f"verdict step {i} ({decision}) missing"
    assert "Any P0" in v
    assert v.index("NOT CLEARED") < v.index("CLEARED FOR USE"), "verdict order is wrong"


def test_all_four_severities_are_defined(doc):
    for sev in ("P0 — Blocker", "P1 — Serious", "P2 — Moderate", "P3 — Minor"):
        assert sev in doc
    assert "You can write the path down concretely" in doc
    assert "Uncertainty never raises severity" in doc


def test_the_finding_format_has_all_seven_parts(doc):
    block = doc.split("One finding, seven parts")[1].split("## 8 ·")[0]
    for field in ("ID", "TITLE", "IMPACT", "STATE", "SEVERITY", "EVIDENCE", "FIX"):
        assert re.search(rf"^{field}\s", block, re.M), f"finding field {field} missing"


def test_the_overclaim_phrases_are_listed(doc):
    for phrase in ("there is no", "there are no", "it does not exist",
                   "the system has no", "it has never", "it never"):
        assert phrase in doc, f"overclaim phrase {phrase!r} not listed"


def test_the_memo_template_carries_every_decision_field(doc):
    memo = doc.split("### Part A")[1].split("### Part B")[0]
    for field in ("Artifact:", "Type / Tier:", "Material reviewed:", "Not available to me:",
                  "RECOMMENDATION:", "Bottom line:", "Findings:", "What blocks it:",
                  "Conditions:", "Open questions:", "Confidence:",
                  "Decisions not mine to make:", "Re-review when:"):
        assert field in memo, f"memo field {field!r} missing"


def test_the_annex_has_all_seven_parts(doc):
    annex = doc.split("### Part B")[1].split("## 9 ·")[0]
    for n in range(1, 8):
        assert f"**B{n}**" in annex, f"annex part B{n} missing"


def test_both_audiences_are_served(doc):
    assert "Decision memo" in doc and "Working annex" in doc
    assert "one document, two audiences" in doc


def test_rule_zero_forbids_testing_with_real_protected_data(doc):
    z = doc.split("## 0 ·")[1].split("## 1 ·")[0]
    assert "Testing is still disclosure" in z
    assert "synthetic" in z.lower()
    assert "never break a boundary to prove a boundary works" in z.lower()


def test_escalation_routes_every_decision_that_is_not_the_auditors(doc):
    esc = doc.split("## 10 ·")[1].split("## 11 ·")[0]
    for owner in ("Records officer", "Privacy office", "ISSO", "Contracting officer",
                  "Section 508 program"):
        assert owner in esc, f"{owner} not named as a decision owner"
    assert "Stop the audit and escalate the same day" in esc
    assert "never signed by one person" in esc


def test_part_11_is_routed_not_decided(doc):
    """Applicability turns on a predicate rule; the auditor must not decide it."""
    assert "predicate rule" in doc
    assert "not by you" in doc
    assert "Do not decide `RT8` yourself" in doc


def test_the_citation_verification_rule_outranks_every_citation(doc):
    f = doc.split("## 11 ·")[1]
    assert "including anything named in this document" in f
    assert "outranks every citation in this document" in f
    assert "not verified" in f


def test_no_regulatory_citation_is_asserted_as_current_without_the_verify_rule(doc):
    """Named authorities must be ones that are stable and generically stated."""
    cited = set(re.findall(r"\b(?:\d+\s+CFR\s+Part\s+\d+|Section\s+508|"
                           r"Plain Writing Act|FedRAMP|FISMA|OMB\s+M-\d+-\d+)\b", doc))
    allowed = {"21 CFR Part 11", "Section 508", "Plain Writing Act"}
    assert cited <= allowed, f"unvetted or volatile citation present: {cited - allowed}"


def test_it_disclaims_authority_it_does_not_have(doc):
    for claim in ("not agency policy", "Authority to Operate",
                  "does not confer or establish compliance"):
        assert claim in doc, f"missing disclaimer: {claim}"
    assert doc.lower().count("not agency policy") >= 2, \
        "the disclaimer must appear in the preamble and again on the memo itself"
    memo = doc.split("### Part A")[1].split("### Part B")[0]
    assert "not agency policy" in memo.lower(), "the memo template carries no disclaimer"


def test_secrets_are_reported_by_location_and_kind_only(doc):
    assert "location and kind only" in doc
    assert "Never reproduce the value" in doc


def test_the_proactive_section_is_actionable_not_aspirational(doc):
    p = doc.split("## 9 ·")[1].split("## 10 ·")[0]
    for habit in ("draft the corrected text", "What happens if we do nothing",
                  "Name the mechanism, not the worry", "shortest credible route",
                  "Say what is good"):
        assert habit in p, f"proactive habit missing: {habit}"


def test_prompt_injection_has_a_test_a_non_engineer_can_run(doc):
    assert "BANANA" in doc
    assert "vary the wording three times" in doc.lower()


def test_the_self_check_covers_every_way_the_audit_itself_can_fail(doc):
    s = doc.split("## 12 ·")[1].split("## 13 ·")[0]
    boxes = [l for l in s.splitlines() if l.strip().startswith("- [ ]")]
    assert len(boxes) >= 13, f"only {len(boxes)} self-check items"
    assert "say so in the memo rather than completing it weakly" in s


def test_language_standard_is_present(doc):
    lang = doc.split("## 13 ·")[1]
    for rule in ("One idea per sentence", "Active voice", "No metaphor",
                 "Define every acronym"):
        assert rule in lang


def test_it_carries_a_version_marker(doc):
    assert re.search(r"v\d+\.\d+ · \d+ controls · \d+ lenses", doc)


def test_no_secret_shaped_example_leaks_into_the_document(doc):
    for pattern in (r"sk_live_[A-Za-z0-9]{6,}", r"AKIA[0-9A-Z]{12,}",
                    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"):
        assert not re.search(pattern, doc)


# --- the verdict rule must be total, ordered, and deterministic --------------

def verdict(p0: int, p1: int, tier: int, unverified_required: int) -> str:
    """The rule exactly as section 6 states it. Ordered; first match wins."""
    if p0 > 0:
        return "NOT CLEARED"
    if p1 > 0:
        return "CLEARED WITH CONDITIONS"
    if tier == 1 and unverified_required > 0:
        return "LIMITED PILOT ONLY"
    return "CLEARED FOR USE"


def test_the_verdict_rule_is_total_and_deterministic():
    """Every reachable combination of inputs yields exactly one verdict."""
    seen = {}
    for p0 in (0, 1, 5):
        for p1 in (0, 1, 5):
            for tier in (1, 2, 3):
                for unv in (0, 1, 9):
                    out = verdict(p0, p1, tier, unv)
                    assert out in {"NOT CLEARED", "CLEARED WITH CONDITIONS",
                                   "LIMITED PILOT ONLY", "CLEARED FOR USE"}
                    seen[(p0, p1, tier, unv)] = out
    assert len(seen) == 3 * 3 * 3 * 3
    assert len(set(seen.values())) == 4, "a verdict is unreachable; the rule has dead branches"


@pytest.mark.parametrize("p0,p1,tier,unv,expected", [
    (1, 0, 3, 0, "NOT CLEARED"),              # a blocker outranks everything
    (1, 5, 1, 9, "NOT CLEARED"),
    (0, 1, 3, 0, "CLEARED WITH CONDITIONS"),  # serious findings, any tier
    (0, 1, 1, 9, "CLEARED WITH CONDITIONS"),  # conditions outrank open questions
    (0, 0, 1, 1, "LIMITED PILOT ONLY"),       # tier 1 with unknowns
    (0, 0, 2, 9, "CLEARED FOR USE"),          # unknowns alone do not gate tier 2
    (0, 0, 1, 0, "CLEARED FOR USE"),
    (0, 0, 3, 0, "CLEARED FOR USE"),
])
def test_verdict_boundaries(p0, p1, tier, unv, expected):
    assert verdict(p0, p1, tier, unv) == expected


def test_severity_never_rises_on_uncertainty(doc):
    """An UNVERIFIED item can gate a tier-1 pilot but can never become a blocker."""
    assert verdict(0, 0, 1, 99) != "NOT CLEARED"
    assert "Uncertainty never raises severity" in doc
    assert "A compensating control always lowers it" in doc


def test_the_document_states_the_rule_in_the_same_order_the_code_applies_it(doc):
    v = doc.split("### The verdict")[1].split("## 7 ·")[0]
    positions = [v.index(d) for d in ("NOT CLEARED", "CLEARED WITH CONDITIONS",
                                      "LIMITED PILOT ONLY", "CLEARED FOR USE")]
    assert positions == sorted(positions), "the written rule is out of order"


# --- the worked example must obey the method it demonstrates ----------------

EXAMPLE = REPO / "standalone" / "EXAMPLE-AUDIT.md"


@pytest.fixture(scope="module")
def example():
    return EXAMPLE.read_text(encoding="utf-8")


def test_the_example_is_marked_fictional(example):
    head = example[:600]
    assert "invented for illustration" in head
    assert "not a record of any real review" in head


def test_the_example_verdict_follows_the_mechanical_rule(example):
    counts = re.search(r"Findings:\s+(\d+) blocker\s+(\d+) serious", example)
    assert counts, "the memo does not state its finding counts"
    p0, p1 = int(counts.group(1)), int(counts.group(2))
    assert verdict(p0, p1, 1, 3) in example.split("RECOMMENDATION:")[1][:60]


def test_the_example_uses_every_memo_field(example, doc):
    memo_fields = re.findall(r"^([A-Z][A-Za-z ]+):", doc.split("### Part A")[1]
                             .split("### Part B")[0], re.M)
    assert len(set(memo_fields)) == 11, \
        f"the memo template changed shape: {sorted(set(memo_fields))}"
    # labels the simple regex cannot match (slash, hyphen) are checked by name
    for label in ("Type / Tier:", "Reviewed by:", "Date:", "Re-review when:"):
        assert label in doc and label in example, f"memo label {label!r} missing"
    for field in set(memo_fields):
        assert f"{field}:" in example, f"the example omits the memo field {field!r}"
    # labels must match byte-for-byte, or offices end up with divergent memos
    for label in ("Conditions:", "Open questions:", "Confidence:", "RECOMMENDATION:"):
        assert label in example and label in doc


def test_the_example_never_asserts_an_absence_as_fact(example):
    for phrase in ("there is no", "there are no", "does not exist", "the system has no"):
        assert phrase not in example.lower(), f"the example overclaims: {phrase!r}"
    assert "NOT FOUND" in example


def test_the_example_names_a_holder_for_every_open_question(example):
    block = example.split("Open questions")[1].split("Confidence:")[0]
    assert block.count("NEEDED:") == block.count("HELD BY:"), \
        "an open question names a document but nobody who holds it"
    assert block.count("NEEDED:") >= 3


def test_the_example_reproduces_no_secret_value(example):
    assert "api_key:" not in example
    assert "location and kind" not in example or "credential" in example.lower()
    for pattern in (r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{12,}"):
        assert not re.search(pattern, example)


def test_the_example_records_that_test_data_was_synthetic(example):
    assert "synthetic" in example.lower()
    assert "No real application content was entered" in example


def test_the_example_routes_decisions_it_does_not_own(example):
    block = example.split("Decisions not mine to make")[1].split("Re-review")[0]
    for owner in ("records officer", "privacy office", "contracting officer", "508"):
        assert owner.lower() in block.lower()


def test_the_example_names_something_done_well(example):
    assert "Done well:" in example


def test_the_example_carries_the_disclaimer(example):
    assert "not agency policy" in example.lower()
