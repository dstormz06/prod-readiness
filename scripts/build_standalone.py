#!/usr/bin/env python3
"""
build_standalone.py - derive the standalone deliverables from the plugin sources.

The standalone framework must never drift from the plugin it was distilled
from, so nothing in `standalone/` is hand-written where it can be generated:

  standalone/readiness_engine.py          <- the seven scripts/, merged
  standalone/PRODUCTION-READINESS-AUDITOR.md
                                          <- AUDITOR.template.md with the lens
                                             mandates, the control catalogue,
                                             the engine, and its digest injected

Merge rules, applied per source module:

  * the shebang, the module docstring, the top-level imports, the
    `sys.path.insert` shim, and the `if __name__` block are removed
  * the module docstring is preserved as `_DOC_<MODULE>` so `--help` still
    prints what it printed before
  * `def main(` becomes `def _cli_<module>(`, and a dispatcher rewrites
    `sys.argv` before calling it, so every sub-parser sees exactly the argument
    vector it was written for

Every other byte of logic is carried across unchanged. `tests/` proves that by
running both implementations over the same fixture and diffing the artefacts.

Usage:
    python3 scripts/build_standalone.py [--check]

`--check` regenerates into memory and fails if the tracked files differ, which
is what CI runs to catch a hand-edit of a generated file.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
AGENTS = REPO / "agents"
OUT_DIR = REPO / "standalone"
ENGINE_PATH = OUT_DIR / "readiness_engine.py"
TEMPLATE_PATH = OUT_DIR / "AUDITOR.template.md"
DOC_PATH = OUT_DIR / "PRODUCTION-READINESS-AUDITOR.md"

# Dependency order: every cross-module reference happens inside a function
# body, so this order is for readability rather than for correctness.
MODULES = [
    "audit_state",
    "evidence_scan",
    "absence_probe",
    "finding_store",
    "validate_findings",
    "assemble_report",
    "readiness_dashboard",
]

LENSES = ["security", "backend", "database", "devops", "qa", "frontend", "ai-security"]

ENGINE_HEADER = '''#!/usr/bin/env python3
"""
readiness_engine.py - the deterministic engine of the production readiness audit.

One dependency-free file, Python 3.9+, standard library only. It replaces the
seven scripts the audit was first written as, and it is generated from them, so
the behaviour is identical rather than merely similar.

    state       init | status | set-stage | set-lenses | archive
    evidence    scan | probe
    findings    validate | render
    report      report | assemble
    surface     serve
    proof       selftest

Commands
--------
    readiness_engine.py init <root> [--execution-mode parallel|sequential]
    readiness_engine.py status <root>
    readiness_engine.py set-stage <root> <stage> <status> [--note TEXT]
    readiness_engine.py set-lenses <root> [--run a,b] [--skip lens=reason]
    readiness_engine.py archive <root>
    readiness_engine.py scan <root> [--out DIR]
    readiness_engine.py probe <root> [--out DIR] [--json-only]
    readiness_engine.py validate <root> [--json]
    readiness_engine.py render <root>
    readiness_engine.py report <root>
    readiness_engine.py assemble <root> [--force]
    readiness_engine.py serve <root> [--port N]
    readiness_engine.py selftest

Exit codes are the ones the audit's gates depend on: `validate` returns 1 when
the report is blocked, `assemble` refuses to run on findings that do not pass,
and `selftest` returns 1 if this file does not behave the way the audit's
invariants require.

GENERATED FILE - do not hand-edit. Regenerate with:
    python3 scripts/build_standalone.py
"""
# PEP 563 keeps every annotation a string, so the `str | None` annotations
# carried over from the plugin scripts do not need Python 3.10 at import time.
# Nothing here reads an annotation at runtime, so this changes no behaviour.
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ENGINE_VERSION = "1.0.0"
'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_module(text: str, module: str) -> tuple[str, str]:
    """Return (docstring, body) for one source module, ready to concatenate."""
    lines = text.splitlines()

    if lines and lines[0].startswith("#!"):
        lines = lines[1:]

    # Module docstring: the first triple-quoted block at column 0.
    doc = ""
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start < len(lines) and lines[start].startswith('"""'):
        end = start
        if lines[start].count('"""') >= 2:
            doc = lines[start].strip('"')
        else:
            end = start + 1
            while end < len(lines) and '"""' not in lines[end]:
                end += 1
            doc = "\n".join(lines[start + 1:end])
        lines = lines[end + 1:]

    body = []
    for line in lines:
        if re.match(r"^(import |from )\S", line):
            continue
        if line.startswith("sys.path.insert("):
            continue
        if line.startswith('if __name__ == "__main__":'):
            break
        body.append(line)

    text = "\n".join(body)
    text = re.sub(r"^def main\(", f"def _cli_{module}(", text, flags=re.MULTILINE)
    text = text.replace("description=__doc__", f"description=_DOC_{module.upper()}")
    return doc.strip("\n"), text.strip("\n")


SELFTEST = '''

# ---------------------------------------------------------------------------
# selftest - proof that this file still enforces what the audit depends on
# ---------------------------------------------------------------------------

_SELFTEST_SOURCES = {
    "package.json": '{"name":"selftest","dependencies":{"express":"^4.18.0"}}',
    "src/app.ts": (
        'import express from "express";\\n'
        'const app = express();\\n'
        'app.get("/orders", async (req, res) => {\\n'
        '  const rows = await db.query(`SELECT * FROM orders WHERE t = \\'${req.query.t}\\'`);\\n'
        '  res.json(rows);\\n'
        '});\\n'
    ),
    "prisma/migrations/001_init/migration.sql": "CREATE TABLE orders (id TEXT PRIMARY KEY);\\n",
    "tests/app.test.ts": 'test("adds", () => { expect(1).toBe(1); });\\n',
}

_SELFTEST_GOOD = {
    "schema": 1,
    "lens": "security",
    "findings": [
        {
            "id": "PRA-SEC-001",
            "title": "The order query puts a request value directly into SQL",
            "impact": "An attacker can read every customer order with one web request.",
            "state": "CONFIRMED", "severity": "P0", "owner": "security",
            "cross_lens": ["database"], "evidence": ["src/app.ts:4"], "probe": None,
            "failure_path": "The handler builds the SQL text with a value from the query string. An attacker adds a quote and a second statement.",
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

# Each case pairs one deliberately broken finding with the substring the gate
# must produce for it. A gate that stops firing is a gate nobody notices.
_SELFTEST_BAD_CASES = [
    ({"id": "PRA-SEC-101", "title": "Absence overclaimed", "impact": "Customers lose orders.",
      "state": "NOT_FOUND", "severity": "P1", "owner": "security", "cross_lens": [],
      "evidence": [], "probe": "backup_config", "failure_path": "There is no backup at all.",
      "compensating": None, "fix": "Add backups.", "resolve": None, "see": None},
     "absence here proves nothing"),
    ({"id": "PRA-SEC-102", "title": "Confirmed without a location", "impact": "Customers lose money.",
      "state": "CONFIRMED", "severity": "P1", "owner": "security", "cross_lens": [],
      "evidence": ["src/app"], "probe": None, "failure_path": None, "compensating": None,
      "fix": "Fix it.", "resolve": None, "see": None},
     "must cite file:line"),
    ({"id": "PRA-SEC-103", "title": "Unverified without a resolve", "impact": "Customers lose data.",
      "state": "UNVERIFIED", "severity": "P2", "owner": "security", "cross_lens": [],
      "evidence": [], "probe": None, "failure_path": None, "compensating": None,
      "fix": "Ask for it.", "resolve": None, "see": None},
     "UNVERIFIED requires resolve"),
    ({"id": "PRA-SEC-104", "title": "P0 without a failure path", "impact": "Customers lose money.",
      "state": "CONFIRMED", "severity": "P0", "owner": "security", "cross_lens": [],
      "evidence": ["src/app.ts:4"], "probe": None, "failure_path": None,
      "compensating": None, "fix": "Fix it.", "resolve": None, "see": None},
     "P0 requires failure-path"),
    ({"id": "PRA-SEC-105", "title": "Branch selector cited as a control", "impact": "Customers see nothing.",
      "state": "NOT_FOUND", "severity": "P2", "owner": "security", "cross_lens": [],
      "evidence": [], "probe": "frontend_present", "failure_path": None, "compensating": None,
      "fix": "Add a user interface.", "resolve": None, "see": None},
     "cannot support a finding"),
    ({"id": "PRA-SEC-106", "title": "Present control claimed absent", "impact": "Customers lose data.",
      "state": "NOT_FOUND", "severity": "P2", "owner": "security", "cross_lens": [],
      "evidence": [], "probe": "migrations", "failure_path": None, "compensating": None,
      "fix": "Add migrations.", "resolve": None, "see": None},
     "this control is present"),
    ({"id": "PRA-SEC-107", "title": "No impact line", "impact": None,
      "state": "CONFIRMED", "severity": "P2", "owner": "security", "cross_lens": [],
      "evidence": ["src/app.ts:4"], "probe": None, "failure_path": None,
      "compensating": None, "fix": "Fix it.", "resolve": None, "see": None},
     "no impact given"),
]


def _quiet(call):
    """Run one sub-command for its effect on disk, not for its console output."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        call()
    return buffer.getvalue()


def _selftest_write(root: Path):
    for rel, text in _SELFTEST_SOURCES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _selftest_findings(root: Path, payload: dict):
    directory = root / DIRNAME / "findings"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "security.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cli_selftest():
    """Run the whole audit machine over a throwaway repository and check it."""
    parser = argparse.ArgumentParser(
        description="Prove this engine still enforces the audit's invariants.")
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary project instead of deleting it")
    args = parser.parse_args()

    checks, failures = [], []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}" if detail else name)

    workdir = Path(tempfile.mkdtemp(prefix="readiness-selftest-"))
    try:
        root = workdir / "project"
        root.mkdir()
        _selftest_write(root)

        # --- stage 0: the trail exists and records its own execution mode ----
        _quiet(lambda: cmd_init(root, "parallel"))
        state = _load(root)
        check("init writes state.json", state is not None)
        check("init records the execution mode",
              (state or {}).get("execution_mode") == "parallel")
        check("init creates the evidence directory", (_dir(root) / "evidence").is_dir())
        check("init creates the findings directory", (_dir(root) / "findings").is_dir())

        # --- stage 2: one evidence pass, and a ledger with real verdicts -----
        files, truncated = collect(root)
        results = verdicts(evaluate(compile_controls(), files), False)
        check("the corpus is collected", len(files) >= len(_SELFTEST_SOURCES),
              f"{len(files)} files")
        check("the catalogue is complete", len(results) == len(CONTROLS),
              f"{len(results)} of {len(CONTROLS)}")
        check("an absent repo-scoped control supports NOT_FOUND",
              results["rate_limiting"]["supports_state"] == "NOT_FOUND",
              results["rate_limiting"]["supports_state"])
        check("an absent infra-scoped control supports UNVERIFIED without IaC",
              results["backup_config"]["supports_state"] == "UNVERIFIED",
              results["backup_config"]["supports_state"])
        promoted = verdicts(evaluate(compile_controls(), files), True)
        check("the same control supports NOT_FOUND once IaC ships in the repo",
              promoted["backup_config"]["supports_state"] == "NOT_FOUND",
              promoted["backup_config"]["supports_state"])
        check("a branch selector supports no finding at all",
              results["frontend_present"]["supports_state"] == "none",
              results["frontend_present"]["supports_state"])
        check("a control with no dependency present is not applicable",
              results["dead_letter_queue"]["verdict"] == "NOT_APPLICABLE",
              results["dead_letter_queue"]["verdict"])
        check("a present control cannot support an absence",
              results["migrations"]["hit_count"] > 0 and
              results["migrations"]["supports_state"] == "none")
        check("a sink with hits becomes a reading list",
              results["raw_sql_concat"]["verdict"] == "SINK_PRESENT",
              results["raw_sql_concat"]["verdict"])

        sys.argv = ["selftest scan", str(root)]
        _quiet(_cli_evidence_scan)
        sys.argv = ["selftest probe", str(root)]
        _quiet(_cli_absence_probe)
        check("the inventory is written",
              (_dir(root) / "evidence" / "inventory.json").is_file())
        check("the ledger is written as data and as prose",
              (_dir(root) / "evidence" / "absence-ledger.json").is_file() and
              (_dir(root) / "evidence" / "absence-ledger.md").is_file())
        inventory = json.loads((_dir(root) / "evidence" / "inventory.json").read_text())
        check("the inventory never reads a credential-shaped file",
              "contents are never read" in inventory.get("_note", ""))

        # --- stage 4: the gate rejects each way a finding can be wrong -------
        for bad, expected in _SELFTEST_BAD_CASES:
            _selftest_findings(root, {"schema": 1, "lens": "security", "findings": [bad]})
            errors, _, _ = validate(root)
            blob = " ".join(message for _, _, message in errors)
            check(f"the gate rejects {bad['id']} ({expected})",
                  expected in blob, blob[:220] or "no error raised")

        # --- and passes a finding that is actually evidence-backed -----------
        _selftest_findings(root, _SELFTEST_GOOD)
        errors, warnings, stats = validate(root)
        check("an evidence-backed findings file passes the gate",
              not errors, "; ".join(m for _, _, m in errors)[:220])
        check("the gate counts what it read", stats.get("total") == 2, str(stats.get("total")))

        # --- stage 4/5: rendering and assembly stay in step ------------------
        written, render_errors = render_all(root)
        check("the markdown trail is generated from the JSON",
              written and not render_errors, "; ".join(render_errors)[:220])
        markdown = (_dir(root) / "findings" / "security.md").read_text()
        check("the generated markdown carries every field",
              all(k in markdown for k in ("state:", "severity:", "impact:", "fix:")))

        (_dir(root) / "verdict.json").write_text(json.dumps({
            "decision": "HOLD",
            "headline": "One confirmed blocker makes this unsafe to deploy.",
            "summary": "An attacker can read every order with one request.",
        }), encoding="utf-8")
        sys.argv = ["selftest assemble", str(root)]
        _quiet(_cli_assemble_report)
        report = (_dir(root) / "report.md").read_text()
        for section in ("Section A", "Section B", "Section C", "Section D", "Section E",
                        "Section F", "Section G", "Section H", "Section I", "Section J",
                        "Section K"):
            check(f"the report contains {section}", section in report)
        check("the verdict reaches Section B", "HOLD - DO NOT DEPLOY" in report)
        check("judgement is still marked as owed", "<!-- FILL" in report)
        structured = json.loads((_dir(root) / "report.json").read_text())
        check("the structured report agrees with the findings",
              structured["counts"]["total"] == 2 and structured["counts"]["p0"] == 1,
              json.dumps(structured["counts"]))
        check("the structured report carries the verdict",
              structured["verdict"]["decision"] == "HOLD")

        # --- refusal: a blocked report is not assembled ----------------------
        _selftest_findings(root, {"schema": 1, "lens": "security",
                                  "findings": [_SELFTEST_BAD_CASES[1][0]]})
        blocked_errors, _, _ = validate(root)
        check("a blocked audit still reports its errors", bool(blocked_errors))

        # --- the audit trail survives a restart ------------------------------
        _quiet(lambda: cmd_archive(root))
        check("archive preserves the old run rather than deleting it",
              any((_dir(root) / "archive").iterdir()))
        check("archive clears the way for a new run",
              not (_dir(root) / "state.json").exists())
    finally:
        if args.keep:
            print(f"kept: {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    passed = sum(1 for c in checks if c["ok"])
    print(json.dumps({
        "engine_version": ENGINE_VERSION,
        "controls": len(CONTROLS),
        "checks": len(checks),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }, indent=2))
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------

# Every sub-parser below was written as its own program, so the dispatcher
# rewrites sys.argv to the vector that program expects instead of nesting
# parsers and changing the argument shapes the audit's docs already state.
COMMANDS = {
    "init": (lambda: _cli_audit_state(), ["init"]),
    "status": (lambda: _cli_audit_state(), ["status"]),
    "set-stage": (lambda: _cli_audit_state(), ["set-stage"]),
    "set-lenses": (lambda: _cli_audit_state(), ["set-lenses"]),
    "archive": (lambda: _cli_audit_state(), ["archive"]),
    "scan": (lambda: _cli_evidence_scan(), []),
    "probe": (lambda: _cli_absence_probe(), []),
    "validate": (lambda: _cli_validate_findings(), []),
    "render": (lambda: _cli_finding_store(), ["render"]),
    "report": (lambda: _cli_finding_store(), ["report"]),
    "assemble": (lambda: _cli_assemble_report(), []),
    "serve": (lambda: _cli_readiness_dashboard(), []),
    "selftest": (lambda: _cli_selftest(), []),
}

USAGE = """readiness_engine.py <command> [options]

  init <root> [--execution-mode parallel|sequential]   start or resume the trail
  status <root>                                        where the audit is up to
  set-stage <root> <stage> <status> [--note TEXT]      move the stage pointer
  set-lenses <root> [--run a,b] [--skip lens=reason]   record lens decisions
  archive <root>                                       keep the old run, start clean
  scan <root> [--out DIR]                              stage 2: what exists
  probe <root> [--out DIR] [--json-only]               stage 2: what was searched for
  validate <root> [--json]                             stage 4: the gate
  render <root>                                        findings/*.json -> findings/*.md
  report <root>                                        -> report.json
  assemble <root> [--force]                            stage 5: -> report.md
  serve <root> [--port N]                              read-only dashboard on 127.0.0.1
  selftest                                             prove this engine is intact
"""


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if len(sys.argv) > 1 else 2
    command = sys.argv[1]
    if command in ("-V", "--version", "version"):
        print(ENGINE_VERSION)
        return 0
    if command not in COMMANDS:
        print(f"unknown command {command!r}\\n\\n{USAGE}", file=sys.stderr)
        return 2
    handler, prefix = COMMANDS[command]
    sys.argv = [f"readiness_engine.py {command}"] + prefix + sys.argv[2:]
    return handler() or 0


if __name__ == "__main__":
    sys.exit(main())
'''


def build_engine() -> str:
    parts = [ENGINE_HEADER]
    for module in MODULES:
        doc, body = strip_module(read(SCRIPTS / f"{module}.py"), module)
        parts.append(
            f'\n\n# {"=" * 74}\n'
            f'# {module}.py\n'
            f'# {"=" * 74}\n\n'
            f'_DOC_{module.upper()} = """{doc}\n"""\n\n{body}\n'
        )
    parts.append(SELFTEST)
    return "".join(parts)


def build_catalogue() -> str:
    """The control catalogue, rendered from the registry the engine executes."""
    sys.path.insert(0, str(SCRIPTS))
    import absence_probe as ap  # noqa: E402

    signal_only = ap.SIGNAL_ONLY
    requires = ap.REQUIRES
    lens_order = ["security", "backend", "database", "devops", "qa", "frontend", "ai-security"]
    lines = []
    for lens in lens_order:
        rows = [c for c in ap.CONTROLS if c["lens"] == lens]
        if not rows:
            continue
        lines.append(f"#### `{lens}` - {len(rows)} controls\n")
        lines.append("| id | what it looks for | polarity | scope | needs | search patterns |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for c in rows:
            pats = list(c["content"]) + [f"path:{p}" for p in c["paths"]]
            rendered = " <br> ".join("`" + p.replace("|", "\\|") + "`" for p in pats) or "-"
            need = f"`{requires[c['id']]}`" if c["id"] in requires else "-"
            kind = "branch selector" if c["id"] in signal_only else c["polarity"]
            lines.append(
                f"| `{c['id']}` | {c['label'].replace('|', '/')} | {kind} | {c['scope']} "
                f"| {need} | {rendered} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_lens(lens: str) -> str:
    """One lens mandate, ready to inline or to dispatch as a sub-agent prompt.

    The front matter is stripped and every heading is demoted one level, so a
    mandate nests under section 11 instead of competing with it. No other byte
    of the mandate changes - it has to stay the brief the agent was given.
    """
    text = read(AGENTS / f"lens-{lens}.md")
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    if "```" in body:
        raise SystemExit(f"lens-{lens}.md now has a fenced block; demotion needs a real parser")
    body = re.sub(r"^(#{1,5}) ", r"\1# ", body, flags=re.MULTILINE)
    return body.strip() + "\n"


def build_doc(engine: str) -> str:
    doc = read(TEMPLATE_PATH)
    digest = hashlib.sha256(engine.encode("utf-8")).hexdigest()
    injections = {
        "CONTROL_CATALOGUE": build_catalogue(),
        "ENGINE": engine.rstrip("\n"),
        "ENGINE_SHA256": digest,
        "ENGINE_LINES": str(len(engine.splitlines())),
        "CONTROL_COUNT": str(len(build_catalogue().splitlines())),
    }
    sys.path.insert(0, str(SCRIPTS))
    import absence_probe as ap  # noqa: E402
    injections["CONTROL_COUNT"] = str(len(ap.CONTROLS))
    for lens in LENSES:
        injections[f"LENS_{lens.upper().replace('-', '_')}"] = build_lens(lens)

    def replace(match):
        key = match.group(1)
        if key not in injections:
            raise SystemExit(f"template asks for unknown injection {key!r}")
        return injections[key]

    out = re.sub(r"<!-- INJECT:([A-Z0-9_]+) -->", replace, doc)
    leftover = re.findall(r"<!-- INJECT:([A-Z0-9_]+) -->", out)
    if leftover:
        raise SystemExit(f"unresolved injections: {leftover}")
    return out


def main() -> int:
    ap_ = argparse.ArgumentParser(description=__doc__)
    ap_.add_argument("--check", action="store_true",
                     help="fail if the tracked files differ from a fresh build")
    args = ap_.parse_args()

    engine = build_engine()
    doc = build_doc(engine)
    targets = {ENGINE_PATH: engine, DOC_PATH: doc}

    if args.check:
        stale = [str(p.relative_to(REPO)) for p, text in targets.items()
                 if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print("stale generated files: " + ", ".join(stale), file=sys.stderr)
            print("run: python3 scripts/build_standalone.py", file=sys.stderr)
            return 1
        print("standalone/ is in sync with scripts/ and agents/")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
    ENGINE_PATH.chmod(0o755)
    print(f"wrote {ENGINE_PATH.relative_to(REPO)} "
          f"({len(engine.splitlines())} lines, sha256 "
          f"{hashlib.sha256(engine.encode()).hexdigest()[:16]}...)")
    print(f"wrote {DOC_PATH.relative_to(REPO)} ({len(doc.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
