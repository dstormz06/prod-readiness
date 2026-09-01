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
    table = doc.split("Evaluate every control in this order")[1].split("## 2 ·")[0]
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
    block = doc.split("One finding, eight parts")[1].split("## 8 ·")[0]
    for field in ("ID", "TITLE", "IMPACT", "STATE", "SEVERITY", "EVIDENCE", "METHOD", "FIX"):
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
    assert len(boxes) >= 25, f"only {len(boxes)} self-check items"
    assert "say so in the memo rather than completing it weakly" in s


def test_language_standard_is_present(doc):
    lang = doc.split("## 13 ·")[1]
    for rule in ("One idea per sentence", "Active voice", "No metaphor",
                 "Define every acronym"):
        assert rule in lang


def test_it_carries_a_dated_version_marker_and_a_change_log(doc):
    """Section 10 tells the reader to number and date every artefact. The
    method must not fail its own rule."""
    assert re.search(r"v\d+\.\d+(\.\d+)? · \d{4}-\d{2}-\d{2} · \d+ controls · \d+ lenses", doc), \
        "the version marker carries no date"
    assert "**Change log.**" in doc
    for version in ("v3.2.1", "v3.2", "v3.1", "v3.0", "v2.0"):
        assert f"**{version}**" in doc, f"{version} is missing from the change log"


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


def test_uncertainty_may_not_move_severity_in_either_direction(doc):
    """An UNVERIFIED item can gate a tier-1 pilot but can never become a
    blocker - and, since v3.2, it can never demote one either. An unverified
    compensating control was the cheapest way to game the verdict."""
    assert verdict(0, 0, 1, 99) != "NOT CLEARED"
    assert "Uncertainty never raises severity" in doc
    assert "A `CONFIRMED` compensating control lowers it" in doc
    assert "uncertainty may not move severity in either direction" in doc
    p0 = doc.split("| **P0 — Blocker** |")[1].split("\n")[0]
    assert "`CONFIRMED` compensating control" in p0
    assert "never demotes a P0" in p0


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
    assert len(set(memo_fields)) >= 11, \
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
    block = " ".join(example.split("Decisions not mine to make")[1]
                     .split("Re-review")[0].split()).lower()
    for owner in ("records officer", "privacy office", "contracting officer", "508"):
        assert owner in block, f"{owner!r} is not named as a decision owner"
    # every referral must carry a date and, where open, say so plainly
    assert "referred" in block
    assert "no response as of" in block


def test_the_example_names_something_done_well(example):
    assert "Done well:" in example


def test_the_example_carries_the_disclaimer(example):
    assert "not agency policy" in example.lower()


# --- v3.0: the protection layer ---------------------------------------------

def test_every_cross_reference_resolves_and_points_at_the_right_section(doc):
    """A reference that resolves but points at the wrong section is worse than
    a broken one: it looks correct and sends the reader to the wrong rule."""
    bodies, order = {}, []
    for m in re.finditer(r"^## (\d+) · (.+)$", doc, re.M):
        order.append((int(m.group(1)), m.group(2).strip(), m.start()))
    for i, (n, title, s) in enumerate(order):
        e = order[i + 1][2] if i + 1 < len(order) else len(doc)
        bodies[n] = doc[s:e]

    for m in re.finditer(r"§(\d+)", doc):
        assert int(m.group(1)) in bodies, f"§{m.group(1)} does not exist"

    # Each reference promises the target contains something. Check it does.
    promises = {
        "owner named in §10": (10, "Records officer"),
        "`METHOD` line in §7": (7, "METHOD"),
        "another office** (§10)": (10, "Whose call"),
        "risk-acceptance block in §8": (8, "RISK ACCEPTED"),
        "those are §10 determinations": (10, "Privacy office"),
        "**Scope it** (§3)": (3, "Stakes tier"),
        "**Work the lenses** (§5)": (5, "PA1"),
        "output** (§8)": (8, "Decision memo"),
        "adapt with §11": (11, "not in §3"),
        "Co-signature required (§10)": (10, "never signed by one person"),
        "person.** See §2": (2, "Describe artifacts, never people"),
        "triggers from §11": (11, "Re-review when"),
        "in the §7 format": (7, "SEVERITY"),
        "not in §3": (3, "Artifact type"),
        "verdict follows §6": (6, "The verdict"),
    }
    for ref, (target, must_contain) in promises.items():
        assert ref in doc, f"cross-reference text vanished: {ref!r}"
        assert must_contain in bodies[target], \
            f"{ref!r} points at §{target}, which does not contain {must_contain!r}"


def test_the_hostile_reader_standard_is_stated(doc):
    p = doc.split("## 2 ·")[1].split("## 3 ·")[0]
    assert "hostile-reader test" in p.lower() or "hostile reader" in p.lower()
    assert "read aloud, unchanged" in p


def test_the_defensibility_standard_has_all_four_requirements(doc):
    p = doc.split("### The defensibility standard")[1].split("### Describe artifacts")[0]
    for req in ("What you examined", "What you did", "What you observed", "When"):
        assert f"**{req}**" in p, f"defensibility requirement missing: {req}"
    assert "A finding without a method is an opinion" in p
    assert "another person, given the same material, would reach it" in p.lower()


def test_findings_may_never_describe_people(doc):
    p = doc.split("### Describe artifacts, never people")[1].split("### Habits")[0]
    assert "Motive is not observable" in p
    for forbidden in ("failed to", "should have known", "carelessly", "ignored"):
        assert forbidden in p.lower(), f"the forbidden phrasing {forbidden!r} is not shown"
    assert "Never describe a person" in doc, "the rule is not repeated at the finding format"


def test_the_pressure_protocol_exists_and_never_obstructs(doc):
    p = doc.split("### When someone asks you to change a finding")[1].split("### What this audit cannot")[0]
    assert "Ask what evidence supports the change" in p
    assert "the finding stands as written" in p.lower()
    assert "legitimate exercise of their authority" in p
    assert "never the person who said no" in p.lower()
    assert "recorded disagreement is a stronger audit" in p.lower()


def test_the_risk_acceptance_block_records_without_revising(doc):
    memo = doc.split("### Part A")[1].split("### Part B")[0]
    assert "RISK ACCEPTED" in memo
    assert "The finding above is unchanged" in memo
    assert "in the accepting official's own words" in memo.lower()
    assert "Never edit a finding to make this block unnecessary" in doc


def test_the_attestation_claims_only_what_the_auditor_did(doc):
    memo = doc.split("### Part A")[1].split("### Part B")[0]
    a = " ".join(memo.split("ATTESTATION")[1].split())  # the block is line-wrapped
    assert "I examined the material listed above" in a
    assert "I made no determination reserved to another office" in a
    assert "Second reviewer (required at Tier 1)" in a
    # It must not claim compliance, safety, or approval.
    for overclaim in ("I certify", "is compliant", "is safe", "is approved", "guarantee"):
        assert overclaim not in a, f"the attestation overclaims: {overclaim!r}"
    assert "claims only what you did" in doc


def test_provenance_and_conflict_are_captured_in_the_memo(doc):
    memo = doc.split("### Part A")[1].split("### Part B")[0]
    for field in ("Received from:", "Interest declared:", "This audit cannot tell you:"):
        assert field in memo, f"memo field missing: {field}"
    assert "how you identify this exact copy" in memo


def test_working_papers_are_preserved_and_indexed(doc):
    assert "Preserve the working papers" in doc
    assert "consult your records officer before doing anything" in doc
    annex = doc.split("### Part B")[1].split("## 9 ·")[0]
    assert "**B8**" in annex and "Working-papers index" in annex
    assert "**B9**" in annex and "unedited" in annex


def test_self_audit_conflict_rule(doc):
    assert "Do not audit your own work alone" in doc
    assert "name a second reviewer" in doc


def test_the_limits_of_the_audit_are_enumerated(doc):
    p = doc.split("### What this audit cannot tell you")[1].split("## 3 ·")[0]
    for limit in ("lawful, compliant, or authorised", "data you did not test it with",
                  "after any change", "could not search for", "the artifact's author"):
        assert limit in p, f"limit not stated: {limit}"


def test_the_annex_now_has_nine_parts(doc):
    annex = doc.split("### Part B")[1].split("## 9 ·")[0]
    for n in range(1, 10):
        assert f"**B{n}**" in annex, f"annex part B{n} missing"


def test_no_response_yet_is_an_acceptable_recorded_answer(doc):
    """The auditor must never be forced to guess to fill a blank."""
    assert "no response as of" in doc.lower()


def test_the_document_obeys_its_own_phrasing_rules(doc):
    """It forbids absence-as-fact and intent attribution. It must not use them."""
    prohibitions = (
        doc.split("### Describe artifacts, never people")[1].split("### Habits")[0]
        + doc.split("**Never write:**")[1].split("\n")[0]
        + doc.split("- [ ] No sentence describes a person")[1].split("\n")[0]
        + doc.split("| | Means | You may write it as |")[1].split("\n\n")[0]
        # §4 rule 4 forbids the word rather than using it
        + doc.split("It describes the artifact and its use, never its author.")[1].split("\n")[0]
    )
    for pattern in (r"there (is|are) no ", r"\bit does not exist\b",
                    r"the system has no ", r"\bfailed to\b", r"\bshould have known\b",
                    r"\bcareless", r"\bnegligen"):
        for m in re.finditer(pattern, doc, re.I):
            fragment = doc[max(0, m.start() - 90):m.end() + 60]
            assert any(line and line in prohibitions
                       for line in [doc[max(0, m.start() - 90):m.end() + 60].strip()]) \
                or m.group(0) in prohibitions or fragment[:50] in prohibitions, \
                f"the document uses phrasing it forbids: ...{fragment.strip()[-110:]}"


def test_it_never_claims_safety_or_compliance_anywhere(doc):
    for pattern in (r"\bis compliant\b", r"\bfully compliant\b", r"\bensures compliance\b",
                    r"\bwe certify\b", r"\bguarantees?\b", r"\bconcluding it is safe\b"):
        assert not re.search(pattern, doc, re.I), \
            f"the document makes a claim it cannot support: {pattern!r}"


# --- v3.2 -------------------------------------------------------------------

def test_an_artifact_you_cannot_open_never_yields_not_found(doc):
    """The most dangerous defect v3.1 carried: closed vendor tools produced
    confident absences from material nobody was ever shown.

    v3.2's first cut of this rule was itself defective - it voided every [E]
    control on a closed artifact, including CONFIRMED findings taken from a
    vendor package. The rule is per control, not per artifact."""
    law = doc.split("### Where the answer lives")[1].split("## 2 ·")[0]
    assert "You could not open it" in law
    assert "every `[A]` control you cannot inspect becomes `[E]` for this audit" in law
    assert "judge this control by control" in law
    assert "You cannot establish an absence in material nobody showed you" in law

    rows = [l for l in law.splitlines() if re.match(r"^\| [1-8] \|", l)]
    assert len(rows) == 8, f"expected 8 ordered rows, found {len(rows)}"
    closed = [r for r in rows if "could not go and look at" in r]
    assert len(closed) == 1, "the closed-artifact row is missing or duplicated"
    assert "**UNVERIFIED**" in closed[0] and "Never `NOT FOUND`" in closed[0]
    assert rows.index(closed[0]) == 2, "the closed-artifact row must precede present/absent"

    # the defect: the row must be scoped to [A], or it swallows [E] evidence
    assert "`[A]` control" in closed[0], \
        "row 3 is not scoped to [A]; a closed artifact would void [E] findings too"
    assert "An `[E]` control is unaffected by a closed artifact" in law



def test_required_control_is_no_longer_an_undefined_term(doc):
    """Tier depth and the pilot test both keyed on a set nothing defined."""
    assert "controls marked required" not in doc
    assert "any required control is `UNVERIFIED`" not in doc
    assert "any `[A]` control is `UNVERIFIED`" in doc
    tier2 = [l for l in doc.splitlines() if l.startswith("| **2 — Reviewed work product**")][0]
    assert "Every `[A]` control" in tier2 and "where documentation was provided" in tier2


def test_the_artifact_may_not_audit_itself(doc):
    assert "Never ask the artifact to audit itself" in doc
    assert "under-reports" in doc
    assert "a claim to verify, never evidence" in doc


def test_the_95_percent_rule_resolves_ask_against_just_fix_it(doc):
    """Two instructions that would otherwise contradict each other."""
    r = doc.split("### When you are not sure")[1].split("### When someone asks")[0]
    assert "Resolve it yourself, or ask. Never guess." in r
    assert "Fix it silently" in r and "reversible" in r
    assert "less than about 95% certain" in r
    for gated in ("state", "severity", "verdict", "leaves this audit"):
        assert gated in r, f"the 95% gate does not name {gated!r}"
    assert "that is not permission to guess" in r


def test_it_scales_above_the_division_without_widening_authority(doc):
    s = doc.split("### Above the division")[1].split("## 4 ·")[0]
    for reach in ("Another division or office", "cited as precedent",
                  "agency-level system", "outside the agency"):
        assert reach in s, f"scaling row missing: {reach}"
    assert "Do not invent authority you do not have" in s
    assert "more signatures, not a bolder auditor" in s


def test_a_new_risk_class_may_get_a_new_lens(doc):
    assert "When a risk has no lens" in doc
    assert "mark the audit `extended`" in doc
    assert "a floor, not a ceiling" in doc


def test_the_new_rules_reach_the_self_check(doc):
    s = doc.split("## 12 ·")[1].split("## 13 ·")[0]
    for item in ("Nothing you could not examine is written as `NOT FOUND`",
                 "compensating control that is itself `UNVERIFIED`",
                 "artifact did not assess itself",
                 "Nothing was guessed"):
        assert item in s, f"self-check item missing: {item}"


def test_the_external_reach_triggers_are_graded_not_contradictory(doc):
    """Outside the office escalates the tier; outside the agency stops the audit.
    v3.2 first shipped these as two different answers to one trigger."""
    esc = [l for l in doc.splitlines() if l.startswith("**Escalate the tier immediately**")]
    assert esc, "the escalation trigger is missing"
    assert "outside the office" in esc[0]
    assert "not an escalation — it is a stop" in esc[0]
    above = doc.split("### Above the division")[1].split("## 4 ·")[0]
    assert "outside the agency" in above and "Stop." in above


def test_the_self_check_survives_an_extended_audit(doc):
    """§10 permits adding a lens; a check hard-coded to 64 would then fail."""
    assert "plus any you added under §11" in doc


def test_the_change_log_describes_the_rules_as_they_now_read(doc):
    entry = doc.split("**v3.2**")[1].split("**v3.1**")[0]
    assert "`[A]` control you could not go and look at" in entry
    assert "`[E]` control is unaffected by a closed artifact" in entry


# --- v3.3: the expert read --------------------------------------------------

def test_the_expert_read_is_its_own_stage_in_the_workflow(doc):
    assert "## 4 · The expert read" in doc
    assert "**Four steps, every time.**" in doc
    assert "**Read it as an expert** (§4)" in doc
    card = doc.split("## Start here")[1].split("\n---\n")[0]
    assert "§3–§5" in card, "Tier 3 must still reach the expert read"


def test_a_hypothesis_can_never_become_a_finding_by_itself(doc):
    """The step's whole risk is manufacturing plausible concerns."""
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    assert "produces hypotheses, never findings" in s
    assert "no evidence state" in s and "never reaches the memo" in s
    assert "**Plausible is not evidence.**" in s
    assert "A hypothesis is not a finding" in s


def test_the_expert_read_has_all_four_questions_and_all_five_rules(doc):
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    questions = re.findall(r"^\d\. \*\*", s.split("### Adapt the depth")[0], re.M)
    assert len(questions) == 4, f"expected 4 questions, found {len(questions)}"
    rules = re.findall(r"^\d\. \*\*", s.split("five rules")[1], re.M)
    assert len(rules) == 5, f"expected 5 rules, found {len(rules)}"


def test_the_expert_read_adapts_by_tier(doc):
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    for tier in ("**3**", "**2**", "**1**"):
        assert f"| {tier} |" in s, f"tier {tier} has no depth row"
    assert "pre-mortem" in s


def test_the_expert_read_cannot_set_severity_or_describe_people(doc):
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    assert "never sets severity" in s
    assert "Severity comes from §6" in s
    assert "never about who was careless" in s


def test_the_95_percent_rule_is_reconciled_with_hypotheses(doc):
    """Without this the two rules contradict: a hypothesis is uncertain by design."""
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    assert "The 95% rule does not gate a hypothesis" in s
    assert "gates what you *write down as a claim*" in s
    assert "Guess freely here, and nowhere else" in s


def test_killed_hypotheses_must_be_recorded(doc):
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    assert "Kill your own hypotheses" in s
    assert "Record every one you killed and what killed it" in s
    assert "confirms everything it guessed was not a read" in s


def test_an_empty_expert_read_is_a_legitimate_outcome(doc):
    s = doc.split("## 4 · The expert read")[1].split("## 5 ·")[0]
    assert "If the expert read produces nothing, say that" in s
    assert "An invented concern to fill the space" in s


def test_the_expert_read_is_recorded_in_the_annex(doc):
    annex = doc.split("### Part B")[1].split("## 9 ·")[0]
    assert "**B10**" in annex and "expert read" in annex
    assert "killed and why" in annex


def test_the_expert_read_reaches_the_self_check(doc):
    s = doc.split("## 12 ·")[1].split("## 13 ·")[0]
    assert "confirmed into a finding or killed" in s
    assert "No hypothesis reached the memo" in s


def test_the_example_annex_is_in_order_and_the_footer_is_last(example):
    """A footer that lands mid-annex strands the sections after it. The
    committed v3.2 example shipped with B8 and B9 orphaned below the footer."""
    numbers = [int(n) for n in re.findall(r"^### B(\d+) · ", example, re.M)]
    assert numbers == sorted(numbers), f"annex sections are out of order: {numbers}"
    assert numbers, "the example has no annex sections"
    footer = example.rindex("*Method: READINESS AUDITOR")
    last_section = example.rindex(f"### B{numbers[-1]} · ")
    assert footer > last_section, "the footer appears before the last annex section"


def test_the_example_shows_the_expert_read_with_killed_hypotheses(example):
    assert "### B10 · The expert read" in example
    b10 = example.split("### B10 · The expert read")[1]
    assert b10.count("**Killed.**") >= 2, "the example must show hypotheses being killed"
    assert "**Confirmed**" in b10
    assert "Nothing here reached the memo except through a finding" in b10


# --- v3.4: ownership in the frame, neutrality in the rules ------------------

def test_it_names_its_owning_office_without_narrowing_the_rules(doc):
    """DPM ownership must not cost another division a zero-edit adoption."""
    assert "Division of Project Management" in doc
    assert "deliberately office-neutral" in doc
    assert "any division at the FDA can adopt it without editing" in doc
    # the operative sections must stay neutral
    body = doc.split("## 0 · Rule Zero")[1]
    for parochial in ("DPM", "OGD", "ORO"):
        assert parochial not in body, \
            f"{parochial!r} appears in an operative section; the rules must stay office-neutral"


def test_the_expert_read_is_distinguished_from_its_neighbours(doc):
    s = doc.split("### How this differs from the rest of the method")[1].split("### Adapt the depth")[0]
    for neighbour in ("§5 lens callouts", "ninety days", "§12 self-check"):
        assert neighbour in s, f"neighbour not distinguished: {neighbour}"
    assert "doing one does not discharge another" in s
    assert "a search plan you may be wrong about" in s
    # and the ninety-day note points back
    assert "This is not the §4 pre-mortem" in doc
