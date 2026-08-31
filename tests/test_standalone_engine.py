"""The standalone engine must behave exactly like the scripts it was built from.

A distilled copy that drifts is worse than no copy: the audit's whole claim is
that its absence ledger and its gate are deterministic, so a second
implementation that disagrees with the first quietly breaks the claim. These
tests run both implementations over the same fixture and diff every artefact.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
ENGINE = REPO / "standalone" / "readiness_engine.py"
DOC = REPO / "standalone" / "PRODUCTION-READINESS-AUDITOR.md"

# The audit trail records when it ran and against which commit. Those differ
# between two runs by design, so they are normalised before the diff.
VOLATILE = (
    (re.compile(r'"(created_at|updated_at|updatedAt|generatedAt)":\s*"[^"]*"'), r'"\1": "<TS>"'),
    (re.compile(r'"(git_ref|gitRef)":\s*"[0-9a-f]{40}"'), r'"\1": "<SHA>"'),
    (re.compile(r"`[0-9a-f]{40}`"), "`<SHA>`"),
)

ARTEFACTS = [
    "state.json",
    "evidence/inventory.json",
    "evidence/absence-ledger.json",
    "evidence/absence-ledger.md",
    "findings/security.md",
    "report.md",
    "report.json",
]

FIXTURE_SOURCES = {
    "package.json": '{"name":"fixture","dependencies":{"express":"^4.18.0","openai":"^4.20.0"}}\n',
    "src/orders.ts": (
        'import express from "express";\n'
        "const app = express();\n"
        'app.get("/orders", async (req, res) => {\n'
        "  const rows = await db.query(`SELECT * FROM orders WHERE t = '${req.query.t}'`);\n"
        "  res.json(rows);\n"
        "});\n"
    ),
    "src/ai.ts": (
        'import OpenAI from "openai";\n'
        "const client = new OpenAI();\n"
        'export const ask = (q) => client.chat.completions.create({ model: "gpt-4o",\n'
        '  messages: [{ role: "system", content: "hi" }], tools: [{ type: "function" }] });\n'
    ),
    "prisma/migrations/001_init/migration.sql": "CREATE TABLE orders (id TEXT PRIMARY KEY);\n",
    "tests/orders.test.ts": 'test("adds", () => { expect(1).toBe(1); });\n',
    ".github/workflows/ci.yml": "name: ci\non: [push]\njobs:\n  build:\n    steps:\n      - run: npm ci\n",
    "Dockerfile": "FROM node:20\nCOPY . /app\n",
}

FINDINGS = {
    "schema": 1,
    "lens": "security",
    "findings": [
        {
            "id": "PRA-SEC-001",
            "title": "The order query puts a request value directly into SQL",
            "impact": "An attacker can read every customer order with one web request.",
            "state": "CONFIRMED", "severity": "P0", "owner": "security",
            "cross_lens": ["database"], "evidence": ["src/orders.ts:4"], "probe": None,
            "failure_path": "The handler builds the SQL text from the query string. An attacker adds a quote and a second statement.",
            "compensating": "none found",
            "fix": "Use a parameterised query and bind the value.",
            "resolve": None, "see": None,
        },
        {
            "id": "PRA-SEC-002",
            "title": "No rate limit found on the public API",
            "impact": "One person can flood the service and stop other customers from buying.",
            "state": "NOT_FOUND", "severity": "P1", "owner": "security",
            "cross_lens": [], "evidence": ["searched, not found in scope"],
            "probe": "rate_limiting", "failure_path": None, "compensating": None,
            "fix": "Add a request rate limit at the edge and on the login route.",
            "resolve": None, "see": None,
        },
    ],
}

VERDICT = {
    "decision": "HOLD",
    "headline": "One confirmed blocker makes this unsafe to deploy.",
    "summary": "An attacker can read every order with one request.",
}


def run(*argv, cwd=None):
    return subprocess.run([sys.executable, *[str(a) for a in argv]],
                          cwd=cwd, capture_output=True, text=True)


def build_project(root: Path) -> Path:
    for rel, text in FIXTURE_SOURCES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def author_inputs(root: Path):
    audit = root / ".readiness-audit"
    (audit / "findings").mkdir(parents=True, exist_ok=True)
    (audit / "context.md").write_text(
        "# Operating context\nCriticality: public checkout. RTO 1 hour, RPO 15 minutes (assumed).\n",
        encoding="utf-8")
    (audit / "scope.md").write_text(
        "# Scope\nReviewed: application source, CI, container build.\nNot reviewed: cloud console.\n",
        encoding="utf-8")
    (audit / "verdict.json").write_text(json.dumps(VERDICT), encoding="utf-8")
    (audit / "findings" / "security.json").write_text(json.dumps(FINDINGS, indent=2),
                                                      encoding="utf-8")


def normalise(text: str, root: Path) -> str:
    text = text.replace(str(root), "<ROOT>")
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    """Run the original scripts and the merged engine over identical inputs."""
    base = tmp_path_factory.mktemp("parity")
    original = build_project(base / "original")
    engine = build_project(base / "engine")

    steps = [
        (["audit_state.py", "init", original, "--execution-mode", "parallel"],
         ["init", engine, "--execution-mode", "parallel"]),
        (["evidence_scan.py", original], ["scan", engine]),
        (["absence_probe.py", original], ["probe", engine]),
    ]
    for script_args, engine_args in steps:
        a = run(SCRIPTS / script_args[0], *script_args[1:])
        b = run(ENGINE, *engine_args)
        assert a.returncode == 0, a.stderr
        assert b.returncode == 0, b.stderr

    author_inputs(original)
    author_inputs(engine)

    run(SCRIPTS / "audit_state.py", "set-lenses", original,
        "--run", "security,backend", "--skip", "frontend=no frontend code found")
    run(ENGINE, "set-lenses", engine,
        "--run", "security,backend", "--skip", "frontend=no frontend code found")

    results = {
        "validate": (run(SCRIPTS / "validate_findings.py", original), run(ENGINE, "validate", engine)),
        "render": (run(SCRIPTS / "finding_store.py", "render", original), run(ENGINE, "render", engine)),
        "assemble": (run(SCRIPTS / "assemble_report.py", original), run(ENGINE, "assemble", engine)),
    }
    return {"original": original, "engine": engine, "results": results}


@pytest.mark.parametrize("relative", ARTEFACTS)
def test_artefacts_are_identical(both, relative):
    original = both["original"] / ".readiness-audit" / relative
    engine = both["engine"] / ".readiness-audit" / relative
    assert original.is_file(), f"the plugin scripts did not write {relative}"
    assert engine.is_file(), f"the engine did not write {relative}"
    assert normalise(original.read_text(), both["original"]) == \
        normalise(engine.read_text(), both["engine"])


@pytest.mark.parametrize("step", ["validate", "render", "assemble"])
def test_console_output_and_exit_codes_match(both, step):
    original, engine = both["results"][step]
    assert original.returncode == engine.returncode
    assert normalise(original.stdout, both["original"]) == normalise(engine.stdout, both["engine"])


def test_the_gate_still_blocks_and_returns_one(tmp_path):
    """A finding that overclaims an absence must block both implementations."""
    root = build_project(tmp_path / "blocked")
    run(ENGINE, "init", root)
    run(ENGINE, "probe", root)
    (root / ".readiness-audit" / "findings").mkdir(parents=True, exist_ok=True)
    (root / ".readiness-audit" / "findings" / "security.json").write_text(json.dumps({
        "schema": 1, "lens": "security", "findings": [{
            "id": "PRA-SEC-001", "title": "No backups", "impact": "Customers lose their orders.",
            "state": "NOT_FOUND", "severity": "P1", "owner": "security", "cross_lens": [],
            "evidence": [], "probe": "backup_config", "failure_path": "There is no backup at all.",
            "compensating": None, "fix": "Add backups.", "resolve": None, "see": None,
        }]}), encoding="utf-8")

    original = run(SCRIPTS / "validate_findings.py", root)
    engine = run(ENGINE, "validate", root)
    assert original.returncode == 1 and engine.returncode == 1
    assert original.stdout == engine.stdout
    assert "absence here proves nothing" in engine.stdout

    refused = run(ENGINE, "assemble", root)
    assert refused.returncode == 1
    assert "refusing to assemble" in refused.stderr


def test_engine_selftest_passes():
    result = run(ENGINE, "selftest")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["result"] == "PASS"
    assert payload["failed"] == 0
    assert payload["checks"] >= 40
    assert payload["controls"] == 91


def test_engine_exposes_every_documented_command():
    listed = run(ENGINE, "--help").stdout
    for command in ("init", "status", "set-stage", "set-lenses", "archive", "scan",
                    "probe", "validate", "render", "report", "assemble", "serve", "selftest"):
        assert f"  {command} " in listed, f"{command} is missing from the engine usage text"


def test_unknown_command_fails_loudly():
    result = run(ENGINE, "definitely-not-a-command")
    assert result.returncode == 2
    assert "unknown command" in result.stderr


def test_generated_files_are_in_sync_with_their_sources():
    """A hand-edit of standalone/ is a drift bug; the generator is the source."""
    result = run(SCRIPTS / "build_standalone.py", "--check")
    assert result.returncode == 0, result.stdout + result.stderr
