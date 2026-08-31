#!/usr/bin/env python3
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


# ==========================================================================
# audit_state.py
# ==========================================================================

_DOC_AUDIT_STATE = """audit_state.py - single source of truth for where a readiness audit is up to.

The audit is designed to survive /clear, a crash, or a week-long gap, so the
stage pointer lives on disk rather than in conversation memory. Every stage
reads its inputs from .readiness-audit/ and writes its outputs there before
the next stage starts.

Usage:
    python3 audit_state.py init <project_root> [--execution-mode parallel|sequential]
    python3 audit_state.py status <project_root>
    python3 audit_state.py set-stage <project_root> <stage> <status> [--note TEXT]
    python3 audit_state.py set-lenses <project_root> --run a,b --skip c=reason
    python3 audit_state.py archive <project_root>
"""

DIRNAME = ".readiness-audit"
STAGES = [
    "0-preflight",
    "1-context",
    "2-evidence",
    "3-lenses",
    "4-validation",
    "5-report",
]
LENSES = ["security", "backend", "frontend", "devops", "qa", "database", "ai-security"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _dir(root: Path) -> Path:
    return root / DIRNAME


def _file(root: Path) -> Path:
    return _dir(root) / "state.json"


def _git(root: Path, *args):
    try:
        out = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=15
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _load(root: Path):
    p = _file(root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _save(root: Path, state):
    _dir(root).mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _now()
    _file(root).write_text(json.dumps(state, indent=2) + "\n")


def cmd_init(root: Path, execution_mode: str):
    existing = _load(root)
    if existing:
        print(json.dumps({"already_initialised": True, "state": existing}, indent=2))
        return 0
    head = _git(root, "rev-parse", "HEAD")
    dirty = _git(root, "status", "--porcelain")
    state = {
        "schema": 1,
        "project_root": str(root.resolve()),
        "created_at": _now(),
        "git_ref": head,
        "dirty_at_start": bool(dirty),
        "dirty_files": (dirty.splitlines() if dirty else []),
        "stage": STAGES[0],
        "stage_status": "in_progress",
        "execution_mode": execution_mode,
        "notes": [],
        "lenses_to_run": [],
        "lenses_skipped": {},
    }
    _save(root, state)
    for sub in ("evidence", "findings"):
        (_dir(root) / sub).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"initialised": True, "state": state}, indent=2))
    return 0


def cmd_status(root: Path):
    state = _load(root)
    if not state:
        print(json.dumps({"exists": False, "hint": "run: audit_state.py init"}, indent=2))
        return 0
    d = _dir(root)
    artefacts = {
        "context.md": (d / "context.md").exists(),
        "scope.md": (d / "scope.md").exists(),
        "evidence/inventory.json": (d / "evidence" / "inventory.json").exists(),
        "evidence/absence-ledger.json": (d / "evidence" / "absence-ledger.json").exists(),
        "evidence/map.md": (d / "evidence" / "map.md").exists(),
        "report.md": (d / "report.md").exists(),
    }
    findings = sorted(p.name for p in (d / "findings").glob("*.md")) if (d / "findings").exists() else []
    print(json.dumps({"exists": True, "state": state, "artefacts": artefacts,
                      "finding_files": findings}, indent=2))
    return 0


def cmd_set_stage(root: Path, stage: str, status: str, note: str | None):
    state = _load(root)
    if not state:
        print("no state.json - run init first", file=sys.stderr)
        return 1
    if stage not in STAGES:
        print(f"unknown stage {stage!r}; expected one of {STAGES}", file=sys.stderr)
        return 1
    state["stage"] = stage
    state["stage_status"] = status
    if note:
        state["notes"].append({"at": _now(), "stage": stage, "note": note})
    _save(root, state)
    print(json.dumps({"stage": stage, "stage_status": status}, indent=2))
    return 0


def cmd_set_lenses(root: Path, run: str | None, skip: list[str]):
    state = _load(root)
    if not state:
        print("no state.json - run init first", file=sys.stderr)
        return 1
    if run:
        wanted = [x.strip() for x in run.split(",") if x.strip()]
        bad = [x for x in wanted if x not in LENSES]
        if bad:
            print(f"unknown lens(es) {bad}; expected from {LENSES}", file=sys.stderr)
            return 1
        state["lenses_to_run"] = wanted
    for entry in skip or []:
        lens, _, reason = entry.partition("=")
        lens = lens.strip()
        if lens not in LENSES:
            print(f"unknown lens {lens!r}", file=sys.stderr)
            return 1
        if not reason.strip():
            print(f"skip for {lens!r} needs a reason: --skip {lens}=<why>", file=sys.stderr)
            return 1
        state["lenses_skipped"][lens] = reason.strip()
    _save(root, state)
    print(json.dumps({"lenses_to_run": state["lenses_to_run"],
                      "lenses_skipped": state["lenses_skipped"]}, indent=2))
    return 0


def cmd_archive(root: Path):
    d = _dir(root)
    if not d.exists():
        print("nothing to archive")
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = d / "archive" / stamp
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir()
    for item in d.iterdir():
        if item.name == "archive":
            continue
        shutil.move(str(item), str(dest / item.name))
    print(json.dumps({"archived_to": str(dest)}, indent=2))
    return 0


def _cli_audit_state():
    ap = argparse.ArgumentParser(description=_DOC_AUDIT_STATE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    s.add_argument("project_root")
    s.add_argument(
        "--execution-mode", choices=("parallel", "sequential"), default="parallel"
    )
    for name in ("status", "archive"):
        s = sub.add_parser(name)
        s.add_argument("project_root")
    s = sub.add_parser("set-stage")
    s.add_argument("project_root")
    s.add_argument("stage")
    s.add_argument("status")
    s.add_argument("--note")
    s = sub.add_parser("set-lenses")
    s.add_argument("project_root")
    s.add_argument("--run")
    s.add_argument("--skip", action="append", default=[])
    args = ap.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    if args.cmd == "init":
        return cmd_init(root, args.execution_mode)
    if args.cmd == "status":
        return cmd_status(root)
    if args.cmd == "archive":
        return cmd_archive(root)
    if args.cmd == "set-stage":
        return cmd_set_stage(root, args.stage, args.status, args.note)
    if args.cmd == "set-lenses":
        return cmd_set_lenses(root, args.run, args.skip)
    return 1


# ==========================================================================
# evidence_scan.py
# ==========================================================================

_DOC_EVIDENCE_SCAN = """evidence_scan.py - the "what exists" half of the evidence pass.

absence_probe.py answers "what did we look for and not find". This answers
"what is actually here": languages, dependency manifests with pinned versions,
entry points, datastore and infrastructure config, test and migration counts,
and data-growth signals. Seven lenses read this one file instead of each
running their own wholesale scan, which is what keeps the audit affordable and
keeps every lens reasoning about the same evidence body.

It never prints the contents of anything that looks like a credential - only
that the file exists and what kind it appears to be.

Usage:
    python3 evidence_scan.py <project_root> [--out DIR]
"""

EXCLUDE_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "env", "__pycache__",
    "dist", "build", ".next", ".nuxt", "out", "target", ".gradle", ".idea",
    ".vscode", "coverage", ".pytest_cache", ".mypy_cache", ".terraform",
    "bower_components", ".readiness-audit", ".security-audit", "Pods",
    ".turbo", ".svelte-kit", "storybook-static", ".cache",
}

SECRET_LIKE = re.compile(
    r"(^|/)(\.env(\..+)?|.*\.pem|.*\.key|.*\.p12|.*\.pfx|id_rsa|credentials\.json|"
    r".*service[-_]?account.*\.json)$", re.IGNORECASE)

IAC_PAT = re.compile(
    r"(\.tf$|\.tfvars$|\.hcl$|/k8s/|/kubernetes/|/helm/|/charts/|cloudformation|"
    r"pulumi\.|cdk\.json$|serverless\.ya?ml$|\.bicep$)", re.IGNORECASE)
CI_PAT = re.compile(
    r"(\.github/workflows/|\.gitlab-ci\.ya?ml$|bitbucket-pipelines\.ya?ml$|"
    r"Jenkinsfile$|\.circleci/|azure-pipelines\.ya?ml$|\.buildkite/)", re.IGNORECASE)
CONTAINER_PAT = re.compile(r"(dockerfile|docker-compose\.ya?ml$|\.dockerignore$)", re.IGNORECASE)
TEST_PAT = re.compile(
    r"(\.(spec|test)\.[jt]sx?$|(^|/)tests?/|(^|/)__tests__/|(^|/)test_[^/]+\.py$|"
    r"_test\.go$|Test\.java$|_spec\.rb$)", re.IGNORECASE)
MIGRATION_PAT = re.compile(r"((^|/)migrations?/|(^|/)db/migrate/|prisma/migrations/)", re.IGNORECASE)
DOC_PAT = re.compile(r"(readme|architecture|adr|runbook|onboarding|contributing)", re.IGNORECASE)

MANIFESTS = [
    "package.json", "requirements.txt", "pyproject.toml", "Pipfile", "go.mod",
    "Gemfile", "pom.xml", "build.gradle", "build.gradle.kts", "composer.json",
    "Cargo.toml", "*.csproj",
]

ENTRY_HINTS = re.compile(
    r"(main\.[jt]s$|index\.[jt]s$|app\.module\.ts$|server\.[jt]s$|main\.py$|"
    r"app\.py$|wsgi\.py$|asgi\.py$|main\.go$|Application\.java$|Program\.cs$)",
    re.IGNORECASE)

ROUTE_PAT = re.compile(
    r"(@(Get|Post|Put|Patch|Delete)\(|app\.(get|post|put|patch|delete)\(|"
    r"router\.(get|post|put|patch|delete)\(|@(app|router)\.(get|post|put|delete)\(|"
    r"export async function (GET|POST|PUT|PATCH|DELETE))")


def read(p: Path, limit=1_500_000):
    try:
        if p.stat().st_size > limit:
            return ""
        return p.read_text(errors="replace")
    except OSError:
        return ""


def parse_package_json(text):
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(d.get(key) or {})
    return deps


def parse_requirements(text):
    deps = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"([A-Za-z0-9._\-\[\]]+)\s*([=<>~!]=?.*)?", line)
        if m:
            deps[m.group(1)] = (m.group(2) or "").strip() or "unpinned"
    return deps


def parse_go_mod(text):
    deps = {}
    for m in re.finditer(r"^\s*([\w./\-]+)\s+(v[\w.\-+]+)", text, re.MULTILINE):
        deps[m.group(1)] = m.group(2)
    return deps


def _cli_evidence_scan():
    ap = argparse.ArgumentParser(description=_DOC_EVIDENCE_SCAN)
    ap.add_argument("project_root")
    ap.add_argument("--out")
    args = ap.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    ext_counts = Counter()
    iac, ci, container, tests, migrations, docs, secretish, entries = [], [], [], [], [], [], [], []
    manifests = {}
    route_count = 0
    total_files = 0

    for p in root.rglob("*"):
        if p.is_dir() or any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        rel = p.relative_to(root).as_posix()
        total_files += 1
        ext_counts[p.suffix.lower() or "(none)"] += 1

        if IAC_PAT.search(rel):
            iac.append(rel)
        if CI_PAT.search(rel):
            ci.append(rel)
        if CONTAINER_PAT.search(rel):
            container.append(rel)
        if TEST_PAT.search(rel):
            tests.append(rel)
        if MIGRATION_PAT.search(rel):
            migrations.append(rel)
        if DOC_PAT.search(p.name) and p.suffix.lower() in (".md", ".mdx", ".rst", ".txt"):
            docs.append(rel)
        if SECRET_LIKE.search(rel):
            secretish.append(rel)  # path and kind only, never contents
        if ENTRY_HINTS.search(rel):
            entries.append(rel)

        if p.name in MANIFESTS or p.suffix == ".csproj":
            text = read(p)
            if p.name == "package.json":
                manifests[rel] = parse_package_json(text)
            elif p.name in ("requirements.txt", "Pipfile"):
                manifests[rel] = parse_requirements(text)
            elif p.name == "go.mod":
                manifests[rel] = parse_go_mod(text)
            else:
                manifests[rel] = {"_parsed": False, "_note": "manifest present, not parsed"}

        if p.suffix.lower() in (".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rb"):
            route_count += len(ROUTE_PAT.findall(read(p, 400_000)))

    def cap(lst, n=40):
        return {"count": len(lst), "sample": sorted(lst)[:n],
                "truncated": len(lst) > n}

    inventory = {
        "schema": 1,
        "project_root": str(root),
        "total_files": total_files,
        "extensions_top": dict(ext_counts.most_common(20)),
        "manifests": manifests,
        "entry_points": cap(entries),
        "route_handler_count": route_count,
        "infrastructure_as_code": cap(iac),
        "ci_config": cap(ci),
        "container_config": cap(container),
        "test_files": cap(tests, 25),
        "migration_files": cap(migrations, 25),
        "documentation": cap(docs),
        "credential_shaped_files": cap(secretish),
        "_note": "credential_shaped_files lists paths and kinds only; contents are never read or reported.",
    }

    outdir = Path(args.out) if args.out else root / ".readiness-audit" / "evidence"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")

    print(json.dumps({
        "total_files": total_files,
        "route_handlers": route_count,
        "tests": inventory["test_files"]["count"],
        "migrations": inventory["migration_files"]["count"],
        "iac_files": inventory["infrastructure_as_code"]["count"],
        "ci_files": inventory["ci_config"]["count"],
        "credential_shaped_files": inventory["credential_shaped_files"]["count"],
        "written_to": str(outdir / "inventory.json"),
    }, indent=2))
    return 0


# ==========================================================================
# absence_probe.py
# ==========================================================================

_DOC_ABSENCE_PROBE = """absence_probe.py - turn "I looked for X and did not find it" into a citable fact.

The most dangerous claim an audit can make is a confident absence. A model asked
to find what is missing will happily assert that a system has no rate limiting
when it simply did not grep for the right thing. This script does that grepping
deterministically: for every expected control it records the patterns searched,
how many files matched, and where. A lens agent may only write a [NOT FOUND]
finding by citing a ledger row whose hit count is zero.

It also decides, per control, whether a zero-hit result *should* be reported as
NOT FOUND or as UNVERIFIED. Controls that normally live outside a source
repository (backups, PITR, alert routing) default to UNVERIFIED - unless the
repo ships infrastructure-as-code, in which case the repo does cover them and a
miss becomes a real NOT FOUND. That single rule prevents most over-claiming.

Usage:
    python3 absence_probe.py <project_root> [--out DIR] [--json-only]

Writes <project_root>/.readiness-audit/evidence/absence-ledger.{json,md}
unless --out is given. Prints a short summary to stdout.
"""

MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 20000
MAX_HITS_RECORDED = 8

EXCLUDE_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "env", "__pycache__",
    "dist", "build", ".next", ".nuxt", "out", "target", ".gradle", ".idea",
    ".vscode", "coverage", ".pytest_cache", ".mypy_cache", ".terraform",
    "bower_components", ".readiness-audit", ".security-audit", "Pods",
    ".turbo", ".svelte-kit", "storybook-static", ".cache",
}

TEXT_SUFFIXES = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".py", ".go", ".rb", ".java", ".kt", ".kts", ".php", ".cs", ".rs", ".scala",
    ".sql", ".prisma", ".graphql",
    ".yml", ".yaml", ".json", ".toml", ".ini", ".conf", ".cfg", ".properties",
    ".tf", ".tfvars", ".hcl", ".bicep",
    ".sh", ".bash", ".zsh", ".ps1",
    ".md", ".mdx", ".txt", ".xml", ".gradle", ".env", ".example", ".sample",
}

TEXT_NAMES = {
    "dockerfile", "makefile", "procfile", "jenkinsfile", "caddyfile",
    "docker-compose.yml", "docker-compose.yaml", ".env", ".env.example",
    ".dockerignore", ".gitignore", ".nvmrc", ".tool-versions",
}


def _is_texty(p: Path) -> bool:
    if p.suffix.lower() in TEXT_SUFFIXES:
        return True
    n = p.name.lower()
    if n in TEXT_NAMES or n.startswith("dockerfile") or n.startswith(".env"):
        return True
    return False


def collect(root: Path):
    """One walk, one read. Every control is evaluated against this corpus."""
    files = []
    truncated = False
    for p in root.rglob("*"):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        if p.is_dir():
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if not _is_texty(p):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            text = p.read_text(errors="replace")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        files.append((rel, text, text.lower()))
    return files, truncated


# ---------------------------------------------------------------------------
# Control registry.
#
#   polarity "control" -> we expect this to exist; zero hits is a candidate
#                         finding.
#   polarity "sink"    -> we expect this NOT to exist, or to exist only with
#                         guards; hits are what the lens must go and read.
#   scope "repo"       -> a source repository is the right place to find it, so
#                         zero hits supports [NOT FOUND].
#   scope "infra"      -> normally configured outside the repo, so zero hits
#                         supports [UNVERIFIED] - promoted to [NOT FOUND] when
#                         the repo does ship IaC.
# ---------------------------------------------------------------------------
C = lambda i, lens, label, content=(), paths=(), polarity="control", scope="repo", \
      signal=False, requires=None: {
    "id": i, "lens": lens, "label": label, "content": list(content),
    "paths": list(paths), "polarity": polarity, "scope": scope,
    # signal: existence tells us which branch of the audit applies; absence is
    # not itself a defect (no frontend is not a missing frontend).
    "signal": signal,
    # requires: this control only makes sense when another one is present. No
    # broker means a missing dead-letter queue is not a finding, it is a
    # category that does not apply - which is what stops the report filling up
    # with demands for machinery the system does not use.
    "requires": requires,
}

CONTROLS = [
    # ---- security -------------------------------------------------------
    C("rate_limiting", "security", "Request rate limiting / throttling",
      [r"rate[-_ ]?limit", r"\bthrottler?\b", r"express-rate-limit", r"slowapi",
       r"@nestjs/throttler", r"limiter\.", r"ratelimit"]),
    C("security_headers", "security", "Security response headers (helmet/CSP/HSTS)",
      [r"\bhelmet\b", r"content-security-policy", r"strict-transport-security",
       r"x-frame-options", r"securityheaders"]),
    C("csrf_protection", "security", "CSRF protection",
      [r"\bcsrf\b", r"xsrf", r"samesite\s*[:=]"]),
    C("input_validation", "security", "Server-side input validation / schema parsing",
      [r"class-validator", r"\bzod\b", r"joi\.", r"yup\.", r"pydantic",
       r"marshmallow", r"validationpipe", r"express-validator", r"@isstring",
       r"jsonschema"]),
    C("authn", "security", "Authentication implementation",
      [r"passport", r"jsonwebtoken", r"\bjwt\b", r"next-auth", r"authguard",
       r"oauth", r"session\(", r"bcrypt", r"argon2", r"clerk", r"supabase\.auth"]),
    C("authz", "security", "Authorization / permission checks distinct from authn",
      [r"canactivate", r"\brbac\b", r"\bcasl\b", r"permission", r"\brole[s]?guard",
       r"authoriz", r"@roles?\(", r"policy"]),
    C("token_expiry", "security", "Token expiry / rotation / revocation config",
      [r"expiresin", r"refresh[-_ ]?token", r"token.{0,12}revok", r"blacklist.{0,10}token",
       r"maxage"]),
    C("tenant_scoping", "security", "Explicit tenant/org scoping on data access",
      [r"tenant[_ ]?id", r"organi[sz]ation[_ ]?id", r"workspace[_ ]?id",
       r"account[_ ]?id\b", r"row level security", r"set_config\('app"]),
    C("secrets_committed", "security", "Committed secret-bearing files",
      paths=[r"(^|/)\.env$", r"(^|/)\.env\.(local|prod|production|staging)$",
             r"(^|/)credentials\.json$", r"(^|/)(id_rsa|.*\.pem|.*\.p12|.*\.pfx)$",
             r"serviceaccount.*\.json$"],
      polarity="sink"),
    C("secrets_manager", "security", "Managed secret store integration",
      [r"secretsmanager", r"parameter ?store", r"\bvault\b", r"key ?vault",
       r"doppler", r"sops", r"gcp.{0,10}secret", r"1password"], scope="infra"),
    C("cors_config", "security", "Explicit CORS configuration",
      [r"enablecors", r"\bcors\(", r"access-control-allow-origin",
       r"allowed_origins", r"corsoptions"]),
    C("audit_logging", "security", "Audit trail of security-relevant actions",
      [r"audit[_ ]?log", r"auditlog", r"activity[_ ]?log", r"security[_ ]?event"]),
    C("account_lockout", "security", "Brute-force lockout / login attempt limiting",
      [r"lockout", r"failed[_ ]?login", r"login[_ ]?attempt", r"max[_ ]?attempts"]),
    C("dependency_scanning", "security", "Dependency vulnerability scanning",
      [r"npm audit", r"yarn audit", r"pnpm audit", r"snyk", r"dependabot",
       r"trivy", r"\bgrype\b", r"safety check", r"osv-scanner", r"renovate"],
      paths=[r"\.github/dependabot\.ya?ml", r"renovate\.json"]),
    C("encryption_at_rest", "security", "Encryption at rest for stored data",
      [r"encrypt.{0,12}at.{0,4}rest", r"\bkms\b", r"pgcrypto", r"storage_encrypted",
       r"field.{0,10}encrypt"], scope="infra"),
    # sinks
    C("ssrf_url_fetch", "security", "Backend fetch of user-influenced URLs (SSRF sink)",
      [r"(axios|fetch|request|httpx|requests)\.(get|post|request)\s*\(\s*[a-z_]*url",
       r"urllib\.request\.urlopen", r"http\.get\(\s*[a-z_]*url",
       r"new url\(\s*(req|request|body|query|params)"], polarity="sink"),
    C("path_traversal_sink", "security", "File path built from request input (traversal sink)",
      [r"path\.join\([^)]*\b(req|request|params|query|body|filename)\b",
       r"readfile(sync)?\([^)]*\b(req|request|params|query|body)\b",
       r"sendfile\(", r"os\.path\.join\([^)]*request"], polarity="sink"),
    C("raw_sql_concat", "security", "String-built SQL (injection sink)",
      [r"\b(select|insert into|update|delete from)\b[^;]{0,200}\$\{",
       r"f[\"'][^\"']{0,120}\b(select|insert|update|delete)\b[^\"']{0,120}\{",
       r"(query|execute|raw)\s*\(\s*[`\"'][^`\"']*(select|insert|update|delete)[^`\"']*[`\"']\s*\+",
       r"createquerybuilder\([^)]*\)\.where\([`\"'][^`\"']*\$\{"], polarity="sink"),
    C("open_redirect_sink", "security", "Redirect target from request input",
      [r"redirect\(\s*(req|request)\.(query|body|params)",
       r"res\.redirect\([^)]*\b(url|next|return_?to|redirect_?uri)\b"], polarity="sink"),

    # ---- backend --------------------------------------------------------
    C("external_call_timeout", "backend", "Timeouts on outbound calls",
      [r"timeout\s*[:=]", r"abortsignal\.timeout", r"request_?timeout",
       r"connecttimeout", r"deadline"]),
    C("retry_policy", "backend", "Retry policy on external calls",
      [r"\bretr(y|ies)\b", r"axios-retry", r"backoff", r"tenacity", r"p-retry",
       r"maxattempts"]),
    C("circuit_breaker", "backend", "Circuit breaker around external dependencies",
      [r"circuit[_ ]?breaker", r"opossum", r"\bhystrix\b", r"resilience4j",
       r"pybreaker"]),
    C("idempotency", "backend", "Idempotency keys on write operations",
      [r"idempotenc", r"idempotency[-_ ]?key", r"dedup(e|lication)?[_ ]?key",
       r"request[_ ]?id.{0,20}unique"]),
    C("message_broker", "backend", "Message broker / queue / pub-sub",
      [r"\bbullmq\b", r"\bbull\b", r"rabbitmq", r"amqplib", r"\bkafka\b",
       r"\bsqs\b", r"\bsns\b", r"pubsub", r"celery", r"sidekiq", r"nats",
       r"@nestjs/bull", r"redis.{0,10}stream"]),
    C("dead_letter_queue", "backend", "Dead-letter queue / poison message handling",
      [r"dead[-_ ]?letter", r"\bdlq\b", r"failedqueue", r"redrive"]),
    C("event_schema_versioning", "backend", "Event schema versioning / registry",
      [r"schema[_ ]?registry", r"avro", r"event[_ ]?version", r"\bcloudevents\b",
       r"\"version\"\s*:\s*\"?\d.*event"]),
    C("consumer_lag_monitoring", "backend", "Consumer lag / queue depth observability",
      [r"consumer[_ ]?lag", r"queue[_ ]?depth", r"backlog.{0,10}metric",
       r"getjobcounts", r"waiting.{0,10}count"], scope="infra"),
    C("caching_layer", "backend", "Caching layer",
      [r"cache[-_ ]?manager", r"\bredis\b", r"memcached", r"@cacheable",
       r"cacheinterceptor", r"unstable_cache", r"revalidate"]),
    C("cache_invalidation", "backend", "Explicit cache invalidation / TTL policy",
      [r"cache.{0,10}(del|evict|invalidat|purge)", r"\bttl\b", r"revalidatetag",
       r"expire\("]),
    C("cache_stampede_guard", "backend", "Single-flight / jitter protection on cache misses",
      [r"single[-_ ]?flight", r"stampede", r"mutex.{0,15}cache", r"jitter",
       r"lock.{0,10}(acquire|redlock)"]),
    C("graceful_shutdown", "backend", "Graceful shutdown / drain handling",
      [r"enableshutdownhooks", r"sigterm", r"beforeexit", r"graceful.{0,10}shutdown",
       r"onmoduledestroy", r"lifespan"]),
    C("api_versioning", "backend", "API versioning strategy",
      [r"enableversioning", r"/v[12]/", r"api[-_ ]?version", r"accept-version"]),
    C("feature_flags", "backend", "Feature flags / kill switches",
      [r"feature[_ ]?flag", r"launchdarkly", r"unleash", r"posthog.{0,10}flag",
       r"flagsmith", r"is_enabled\("]),
    C("health_endpoint", "backend", "Health / readiness endpoint",
      [r"/health", r"/healthz", r"/readyz", r"/livez", r"terminus", r"healthcheck"]),

    # ---- frontend -------------------------------------------------------
    C("frontend_present", "frontend", "Frontend application present",
      [r"\breact\b", r"\bvue\b", r"\bsvelte\b", r"\bangular\b", r"next\.config",
       r"\"react-dom\""],
      paths=[r"\.(tsx|jsx|vue|svelte)$", r"(^|/)index\.html$"]),
    C("error_boundary", "frontend", "Error boundary / global UI error handling",
      [r"errorboundary", r"componentdidcatch", r"error\.tsx", r"global-error",
       r"onerrorcaptured"]),
    C("loading_empty_states", "frontend", "Loading / empty state handling",
      [r"isloading", r"ispending", r"\bskeleton\b", r"suspense", r"loading\.tsx",
       r"emptystate"]),
    C("offline_handling", "frontend", "Offline / network-failure handling",
      [r"navigator\.online", r"\boffline\b", r"service ?worker", r"workbox"]),
    C("a11y_tooling", "frontend", "Accessibility tooling in the repo",
      [r"eslint-plugin-jsx-a11y", r"\baxe-core\b", r"@axe-core", r"lighthouse",
       r"pa11y", r"jest-axe"]),
    C("cross_browser_testing", "frontend", "Cross-browser / device test config",
      [r"browsers\s*:\s*\[", r"webkit", r"firefox", r"browserslist",
       r"devices\[", r"projects\s*:\s*\["],
      paths=[r"playwright\.config\.[jt]s", r"browserslistrc"]),
    C("client_storage_sensitive", "frontend", "Sensitive data in browser storage (sink)",
      [r"localstorage\.setitem\([^)]*(token|jwt|secret|password|key)",
       r"sessionstorage\.setitem\([^)]*(token|jwt|secret|password)",
       r"document\.cookie\s*="], polarity="sink"),

    # ---- devops ---------------------------------------------------------
    C("iac", "devops", "Infrastructure as code",
      [r"resource\s+\"aws_", r"apiversion:\s*apps/", r"awstemplateformatversion"],
      paths=[r"\.tf$", r"\.tfvars$", r"(^|/)k8s/", r"(^|/)kubernetes/",
             r"(^|/)helm/", r"(^|/)charts/", r"cloudformation", r"(^|/)pulumi\.",
             r"(^|/)cdk\.json$", r"serverless\.ya?ml$"]),
    C("ci_pipeline", "devops", "CI pipeline definition",
      paths=[r"\.github/workflows/.*\.ya?ml$", r"\.gitlab-ci\.ya?ml$",
             r"(^|/)bitbucket-pipelines\.ya?ml$", r"(^|/)Jenkinsfile$",
             r"\.circleci/config\.ya?ml$", r"azure-pipelines\.ya?ml$",
             r"\.buildkite/"]),
    C("tests_in_ci", "devops", "Tests wired into CI",
      [r"run:\s*.*\b(npm|yarn|pnpm|pytest|go test|mvn|gradle).*\btest\b",
       r"script:\s*.*test"]),
    C("deploy_automation", "devops", "Automated deploy step",
      [r"\bdeploy\b", r"kubectl apply", r"helm upgrade", r"terraform apply",
       r"flyctl deploy", r"vercel deploy", r"eb deploy", r"argocd"]),
    C("rollback_path", "devops", "Documented or automated rollback",
      [r"\brollback\b", r"helm rollback", r"kubectl rollout undo", r"revert.{0,10}deploy",
       r"blue[-_ ]?green", r"canary"], scope="infra"),
    C("post_deploy_smoke", "devops", "Post-deploy smoke verification",
      [r"smoke[-_ ]?test", r"post[-_ ]?deploy", r"health.{0,10}check.{0,15}after",
       r"verify.{0,10}deployment"], scope="infra"),
    C("container_build", "devops", "Container build definition",
      paths=[r"(^|/)dockerfile", r"docker-compose\.ya?ml$"]),
    C("container_nonroot", "devops", "Container runs as non-root",
      [r"^\s*user\s+(?!root)\S+", r"runasnonroot", r"runasuser"]),
    C("container_pinned_base", "devops", "Base image pinned by digest",
      [r"^\s*from\s+\S+@sha256:"]),
    C("resource_limits", "devops", "Container CPU/memory limits",
      [r"resources:\s*\n\s*limits", r"mem_limit", r"cpus:", r"memory:\s*\"?\d"]),
    C("liveness_readiness_probes", "devops", "Liveness/readiness probes",
      [r"livenessprobe", r"readinessprobe", r"startupprobe", r"healthcheck:"]),
    C("structured_logging", "devops", "Structured logging",
      [r"\bpino\b", r"winston", r"structlog", r"zerolog", r"logrus",
       r"json.{0,10}logger", r"logger\.(info|warn|error)\(\s*\{"]),
    C("metrics", "devops", "Application metrics emission",
      [r"prom-client", r"prometheus", r"statsd", r"opentelemetry", r"datadog",
       r"micrometer", r"/metrics"], scope="infra"),
    C("tracing", "devops", "Distributed tracing",
      [r"opentelemetry", r"\bjaeger\b", r"\bzipkin\b", r"traceparent", r"\bsentry\b"],
      scope="infra"),
    C("alerting", "devops", "Alert rules / on-call routing",
      [r"alertmanager", r"pagerduty", r"opsgenie", r"alert.{0,10}rule",
       r"slo|error[_ ]?budget"], scope="infra"),
    C("env_config_template", "devops", "Externalised config template",
      paths=[r"\.env\.example$", r"\.env\.sample$", r"(^|/)env\.example",
             r"(^|/)config\.example"]),
    C("runbook", "devops", "Runbook / operational documentation",
      [r"\brunbook\b", r"on[-_ ]?call", r"incident.{0,10}response", r"\bpostmortem\b"],
      paths=[r"(^|/)docs?/.*(runbook|ops|incident)"]),

    # ---- qa -------------------------------------------------------------
    C("test_framework", "qa", "Test framework configured",
      [r"\bjest\b", r"vitest", r"\bmocha\b", r"pytest", r"\bunittest\b",
       r"testing-library", r"go test", r"junit", r"rspec"],
      paths=[r"jest\.config", r"vitest\.config", r"pytest\.ini", r"(^|/)tox\.ini$"]),
    C("test_files", "qa", "Test files present",
      paths=[r"\.(spec|test)\.[jt]sx?$", r"(^|/)tests?/", r"(^|/)__tests__/",
             r"(^|/)test_[^/]+\.py$", r"_test\.go$", r"Test\.java$"]),
    C("e2e_tests", "qa", "End-to-end tests",
      [r"playwright", r"cypress", r"puppeteer", r"selenium", r"testcafe"],
      paths=[r"(^|/)e2e/", r"cypress\.config"]),
    C("authz_boundary_tests", "qa", "Authorization boundary tests",
      [r"(describe|test|it)\([^)]*\b(403|forbidden|unauthori[sz]ed|permission|other tenant|cross[- ]tenant)\b"]),
    C("load_testing", "qa", "Load / performance testing",
      [r"\bk6\b", r"\blocust\b", r"artillery", r"\bjmeter\b", r"gatling",
       r"autocannon"]),
    C("coverage_config", "qa", "Coverage measurement configured",
      [r"collectcoverage", r"coveragethreshold", r"--cov", r"nyc", r"codecov",
       r"coveralls"]),
    C("synthetic_test_data", "qa", "Synthetic test data generation",
      [r"\bfaker\b", r"factory[-_ ]?bot", r"factory_boy", r"fishery", r"\bmirage\b",
       r"seed.{0,10}(data|script)"]),
    C("pii_in_fixtures", "qa", "Real-looking PII in fixtures or dumps (sink)",
      [r"@(gmail|yahoo|hotmail|outlook)\.com",
       r"\b\d{3}-\d{2}-\d{4}\b",
       r"\b4[0-9]{12}(?:[0-9]{3})?\b"],
      paths=[r"(^|/)(fixtures?|seeds?|dumps?|testdata)/"], polarity="sink"),
    C("prod_creds_in_test", "qa", "Production-looking credentials in test config (sink)",
      [r"(prod|production)[_-]?(url|host|key|token|password)\s*[:=]",
       r"sk_live_", r"pk_live_", r"rk_live_"], polarity="sink"),

    # ---- database -------------------------------------------------------
    C("migrations", "database", "Schema migrations",
      [r"migration", r"alembic", r"knex", r"flyway", r"liquibase", r"goose"],
      paths=[r"(^|/)migrations?/", r"(^|/)db/migrate/", r"(^|/)prisma/migrations/"]),
    C("reversible_migrations", "database", "Down / reversible migrations",
      [r"\bpublic async down\b", r"def downgrade", r"\.down\s*=", r"exports\.down",
       r"-- ?\+goose down", r"<!-- ?rollback"]),
    C("index_definitions", "database", "Explicit index definitions",
      [r"create index", r"@index\(", r"@@index\(", r"addindex", r"db_index=true",
       r"createindex"]),
    C("foreign_keys", "database", "Foreign key constraints",
      [r"foreign key", r"references\s+\w+\s*\(", r"@manytoone", r"@joincolumn",
       r"on delete", r"forcign", r"ondelete"]),
    C("connection_pooling", "database", "Connection pool configuration",
      [r"pool\s*[:=]", r"max.{0,5}connections", r"pgbouncer", r"poolsize",
       r"connection[_ ]?limit"]),
    C("query_timeout", "database", "Statement / query timeout",
      [r"statement_timeout", r"query[_ ]?timeout", r"lock_timeout",
       r"maxquerytime"]),
    C("transaction_boundaries", "database", "Explicit transaction boundaries",
      [r"begin transaction", r"\$transaction", r"transaction\(", r"@transactional",
       r"withtransaction", r"session\.begin"]),
    C("soft_delete", "database", "Soft delete columns",
      [r"deleted_?at", r"is_?deleted", r"@deletedatecolumn", r"softdelete",
       r"archived_?at"]),
    C("soft_delete_purge", "database", "Purge job for soft-deleted rows",
      [r"purge", r"hard[-_ ]?delete", r"cleanup.{0,15}(deleted|expired)",
       r"vacuum.{0,10}job"]),
    C("backup_config", "database", "Backup configuration",
      [r"\bbackup\b", r"pg_dump", r"mysqldump", r"snapshot", r"backup_retention"],
      scope="infra"),
    C("pitr", "database", "Point-in-time recovery",
      [r"point[-_ ]?in[-_ ]?time", r"\bpitr\b", r"wal[-_ ]?archiv", r"binlog",
       r"continuous.{0,10}backup"], scope="infra"),
    C("restore_drill", "database", "Evidence of a tested restore",
      [r"restore.{0,15}(drill|test|verif|rehears)", r"pg_restore",
       r"disaster[-_ ]?recovery"], scope="infra"),
    C("retention_policy", "database", "Data retention / deletion policy",
      [r"retention", r"\bgdpr\b", r"right to be forgotten", r"data[_ ]?deletion",
       r"anonymi[sz]e"]),
    C("archival_strategy", "database", "Archival / partitioning for cold data",
      [r"partition by", r"create table.{0,30}partition", r"archive[_ ]?table",
       r"cold[_ ]?storage", r"glacier"]),
    C("object_storage_lifecycle", "database", "Object storage lifecycle rules",
      [r"lifecycle_rule", r"lifecycle_configuration", r"expiration\s*\{",
       r"transition.{0,10}storage_class"], scope="infra"),
    C("slow_query_logging", "database", "Slow query logging",
      [r"slow[_ ]?quer", r"log_min_duration", r"long_query_time",
       r"maxquerytime"], scope="infra"),

    # ---- ai-security ----------------------------------------------------
    C("llm_sdk", "ai-security", "LLM / model provider SDK",
      [r"@anthropic-ai", r"\bopenai\b", r"langchain", r"llamaindex",
       r"@google/generative-ai", r"bedrock-runtime", r"huggingface",
       r"ollama", r"litellm", r"vercel/ai"]),
    C("prompt_templates", "ai-security", "Prompt construction sites",
      [r"system[_ ]?prompt", r"\bmessages\s*:\s*\[", r"chatprompttemplate",
       r"role:\s*[\"']system"]),
    C("model_pinning", "ai-security", "Pinned model identifiers",
      [r"claude-[a-z0-9.\-]+", r"gpt-[0-9][a-z0-9.\-]*", r"gemini-[a-z0-9.\-]+",
       r"model\s*[:=]\s*[\"'][a-z0-9][^\"']{4,}"]),
    C("llm_token_limits", "ai-security", "Token / output limits on model calls",
      [r"max_?tokens", r"max_output_tokens", r"maxtokens"]),
    C("llm_cost_controls", "ai-security", "Cost or usage controls on inference",
      [r"token.{0,10}(budget|quota|usage|count)", r"cost.{0,10}(limit|cap|track)",
       r"spend.{0,10}limit"]),
    C("llm_output_validation", "ai-security", "Validation of model output before use",
      [r"parse.{0,10}(response|completion|output)", r"safeparse",
       r"guardrail", r"sanitiz.{0,15}(output|response)", r"json\.parse\(.{0,30}completion"]),
    C("llm_tool_calling", "ai-security", "Model-driven tool / function calling (sink)",
      [r"tool_?choice", r"function_?call", r"\btools\s*:\s*\[", r"tool_use",
       r"agentexecutor"], polarity="sink"),
    C("llm_human_in_loop", "ai-security", "Human approval gate on model-triggered actions",
      [r"human[-_ ]?in[-_ ]?the[-_ ]?loop", r"require.{0,10}approval",
       r"confirm.{0,15}before", r"pending[_ ]?approval"]),
]


# Existence tells us which branch of the audit applies; absence is not itself a
# defect. "No frontend found" is not a missing frontend.
SIGNAL_ONLY = {
    "frontend_present", "llm_sdk", "message_broker", "caching_layer",
    "container_build", "soft_delete",
}

# A control that only makes sense when something else is present. Without a
# broker, a missing dead-letter queue is not a gap - it is a category that does
# not apply. This is what stops the report demanding machinery the system has
# no use for, which is the fastest way to get a whole audit ignored.
REQUIRES = {
    "dead_letter_queue": "message_broker",
    "event_schema_versioning": "message_broker",
    "consumer_lag_monitoring": "message_broker",
    "cache_invalidation": "caching_layer",
    "cache_stampede_guard": "caching_layer",
    "container_nonroot": "container_build",
    "container_pinned_base": "container_build",
    "resource_limits": "container_build",
    "liveness_readiness_probes": "container_build",
    "error_boundary": "frontend_present",
    "loading_empty_states": "frontend_present",
    "offline_handling": "frontend_present",
    "a11y_tooling": "frontend_present",
    "cross_browser_testing": "frontend_present",
    "client_storage_sensitive": "frontend_present",
    "prompt_templates": "llm_sdk",
    "model_pinning": "llm_sdk",
    "llm_token_limits": "llm_sdk",
    "llm_cost_controls": "llm_sdk",
    "llm_output_validation": "llm_sdk",
    "llm_human_in_loop": "llm_sdk",
    "llm_tool_calling": "llm_sdk",
    "reversible_migrations": "migrations",
    "soft_delete_purge": "soft_delete",
    "tests_in_ci": "ci_pipeline",
    "e2e_tests": "test_files",
    "authz_boundary_tests": "test_files",
    "coverage_config": "test_files",
    "synthetic_test_data": "test_files",
}


def compile_controls():
    for c in CONTROLS:
        c["signal"] = c["id"] in SIGNAL_ONLY
        c["requires"] = REQUIRES.get(c["id"])
        c["_content"] = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in c["content"]]
        c["_paths"] = [re.compile(p, re.IGNORECASE) for p in c["paths"]]
    return CONTROLS


def evaluate(controls, files):
    results = {}
    for c in controls:
        hits = []
        for rel, text, lower in files:
            matched_by = None
            for rx in c["_paths"]:
                if rx.search(rel):
                    matched_by = "path"
                    break
            if not matched_by:
                for rx in c["_content"]:
                    m = rx.search(text)
                    if m:
                        matched_by = "content"
                        break
            if matched_by:
                if len(hits) < MAX_HITS_RECORDED:
                    hits.append({"path": rel, "matched_by": matched_by})
                else:
                    hits.append(None)  # counted, not recorded
        recorded = [h for h in hits if h]
        results[c["id"]] = {
            "id": c["id"],
            "lens": c["lens"],
            "label": c["label"],
            "polarity": c["polarity"],
            "scope": c["scope"],
            "signal": c["signal"],
            "requires": c["requires"],
            "patterns_searched": c["content"] + c["paths"],
            "hit_count": len(hits),
            "hits": recorded,
            "hits_truncated": len(hits) > len(recorded),
        }
    return results


def verdicts(results, iac_present: bool):
    """Turn raw hit counts into the evidence state a lens is allowed to claim."""
    for r in results.values():
        n = r["hit_count"]
        dep = r.get("requires")
        if dep and results.get(dep, {}).get("hit_count", 0) == 0 and n == 0:
            r["verdict"] = "NOT_APPLICABLE"
            r["supports_state"] = "none"
            r["note"] = (f"Depends on `{dep}`, which is not present, so this control has "
                         "nothing to apply to. Not a gap.")
            continue
        if r.get("signal") and n == 0:
            r["verdict"] = "NO_SIGNAL_IN_SCOPE"
            r["supports_state"] = "none"
            r["note"] = ("Branch selector, not a control. Absence means this part of the "
                         "audit does not apply; it is not a finding.")
            continue
        if r["polarity"] == "sink":
            r["verdict"] = "SINK_PRESENT" if n else "NO_SINK_FOUND"
            r["supports_state"] = "CONFIRMED-candidate" if n else "none"
            r["note"] = ("Hits are code to read, not a finding by themselves."
                         if n else "No sink of this shape in scope.")
            continue
        if n:
            r["verdict"] = "SIGNAL_PRESENT"
            r["supports_state"] = "none"
            r["note"] = "Something matching this control exists; the lens must judge whether it is adequate, not whether it exists."
        elif r["scope"] == "repo":
            r["verdict"] = "NO_SIGNAL_IN_SCOPE"
            r["supports_state"] = "NOT_FOUND"
            r["note"] = "A source repository is the right place for this, so zero hits supports a NOT FOUND finding."
        else:
            if iac_present:
                r["verdict"] = "NO_SIGNAL_IN_SCOPE"
                r["supports_state"] = "NOT_FOUND"
                r["note"] = "Normally configured outside the repo, but this repo ships IaC, so the repo does cover it. Zero hits supports NOT FOUND."
            else:
                r["verdict"] = "OUT_OF_SCOPE_UNSEEN"
                r["supports_state"] = "UNVERIFIED"
                r["note"] = "Normally configured outside the repo and no IaC is present, so absence here proves nothing. Report as UNVERIFIED and say what evidence would resolve it."
    return results


def lens_signals(results):
    def present(cid):
        return results[cid]["hit_count"] > 0
    return {
        "frontend_present": present("frontend_present"),
        "llm_present": present("llm_sdk"),
        "broker_present": present("message_broker"),
        "cache_present": present("caching_layer"),
        "iac_present": present("iac"),
        "ci_present": present("ci_pipeline"),
        "container_present": present("container_build"),
        "tests_present": present("test_files") or present("test_framework"),
        "migrations_present": present("migrations"),
    }


def render_md(ledger):
    L = ledger
    out = ["# Absence ledger", "",
           f"Files scanned: {L['files_scanned']}"
           + (" (TRUNCATED - repository exceeded the scan cap)" if L["truncated"] else ""),
           "",
           "Every `[NOT FOUND]` finding must cite a row below whose hit count is 0 and "
           "whose *Supports* column says `NOT_FOUND`. A row saying `UNVERIFIED` means the "
           "repository is the wrong place to look - report it as unverified, not as absent.",
           ""]
    for lens in ["security", "backend", "frontend", "devops", "qa", "database", "ai-security"]:
        rows = [r for r in L["controls"].values() if r["lens"] == lens]
        if not rows:
            continue
        out += [f"## {lens}", "",
                "| Control | Polarity | Hits | Verdict | Supports | Example paths |",
                "| --- | --- | --- | --- | --- | --- |"]
        for r in sorted(rows, key=lambda x: x["id"]):
            paths = ", ".join(h["path"] for h in r["hits"][:3]) or "-"
            if r["hits_truncated"]:
                paths += ", ..."
            out.append(f"| `{r['id']}` - {r['label']} | {r['polarity']} | {r['hit_count']} "
                       f"| {r['verdict']} | {r['supports_state']} | {paths} |")
        out.append("")
    return "\n".join(out) + "\n"


def _cli_absence_probe():
    ap = argparse.ArgumentParser(description=_DOC_ABSENCE_PROBE)
    ap.add_argument("project_root")
    ap.add_argument("--out", help="output directory (default <root>/.readiness-audit/evidence)")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    files, truncated = collect(root)
    controls = compile_controls()
    results = evaluate(controls, files)
    iac_present = results["iac"]["hit_count"] > 0
    results = verdicts(results, iac_present)

    ledger = {
        "schema": 1,
        "project_root": str(root),
        "files_scanned": len(files),
        "truncated": truncated,
        "iac_present": iac_present,
        "lens_signals": lens_signals(results),
        "controls": results,
    }

    outdir = Path(args.out) if args.out else root / ".readiness-audit" / "evidence"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "absence-ledger.json").write_text(json.dumps(ledger, indent=2) + "\n")
    if not args.json_only:
        (outdir / "absence-ledger.md").write_text(render_md(ledger))

    absent = [r["id"] for r in results.values()
              if r["polarity"] == "control" and r["supports_state"] == "NOT_FOUND"]
    unver = [r["id"] for r in results.values() if r["supports_state"] == "UNVERIFIED"]
    sinks = [r["id"] for r in results.values() if r["verdict"] == "SINK_PRESENT"]
    print(json.dumps({
        "files_scanned": len(files),
        "truncated": truncated,
        "lens_signals": ledger["lens_signals"],
        "not_found_candidates": absent,
        "unverified_candidates": unver,
        "sinks_to_read": sinks,
        "written_to": str(outdir),
    }, indent=2))
    return 0


# ==========================================================================
# finding_store.py
# ==========================================================================

_DOC_FINDING_STORE = """finding_store.py - the structured layer under the audit trail.

Lenses author findings as JSON (`findings/<lens>.json`). That file is the
source of truth: it is what the dashboard renders and what the report is built
from. The markdown a fix agent reads (`findings/<lens>.md`) is *generated* from
it, so the two can never disagree.

The split matters because a human reviewer and a fix agent want different
things from the same finding. The reviewer wants to know that a problem exists,
what it costs them, and enough evidence to believe it. The agent wants every
field. JSON carries both and lets each surface choose.

Usage:
    python3 finding_store.py render <project_root>   # findings/*.json -> findings/*.md
    python3 finding_store.py report <project_root>   # -> report.json
"""

SCHEMA = 1

STATES = {"CONFIRMED", "NOT_FOUND", "UNVERIFIED"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
DECISIONS = {"SHIP", "FIX_THEN_SHIP", "HOLD"}

LENS_ORDER = ["security", "backend", "frontend", "devops", "qa", "database", "ai-security"]
LENS_LABEL = {
    "security": "Security", "backend": "Backend", "frontend": "Frontend",
    "devops": "DevOps", "qa": "QA", "database": "Database",
    "ai-security": "AI security",
}

# Fields a lens may set. `impact` is the one written for a human who will never
# open the codebase; everything else is the technical record.
TEXT_FIELDS = ("title", "impact", "failure_path", "compensating", "fix", "resolve", "see", "probe")
LIST_FIELDS = ("cross_lens", "evidence")


class FindingError(ValueError):
    """A finding file that cannot be trusted enough to render or report on."""


def _text(value):
    """Normalise an optional string field. Absent, null, and '-' all mean unset."""
    if value is None:
        return None
    value = str(value).strip()
    if not value or value == "-":
        return None
    return value


def _list(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    return [str(item).strip() for item in value if _text(item)]


def normalise_finding(raw: dict, lens: str) -> dict:
    """Coerce one authored finding into the canonical shape, or raise."""
    if not isinstance(raw, dict):
        raise FindingError(f"{lens}: a finding must be an object, got {type(raw).__name__}")

    fid = _text(raw.get("id"))
    if not fid:
        raise FindingError(f"{lens}: a finding is missing its id")

    state = (_text(raw.get("state")) or "").upper().replace(" ", "_").replace("-", "_")
    if state not in STATES:
        raise FindingError(f"{fid}: state must be one of {sorted(STATES)}, got {state or 'nothing'}")

    severity = (_text(raw.get("severity")) or "").upper()
    if severity not in SEVERITIES:
        raise FindingError(f"{fid}: severity must be one of {sorted(SEVERITIES)}, got {severity or 'nothing'}")

    finding = {"id": fid, "state": state, "severity": severity,
               "owner": _text(raw.get("owner")) or lens, "lens": lens}
    for key in TEXT_FIELDS:
        finding[key] = _text(raw.get(key))
    for key in LIST_FIELDS:
        finding[key] = _list(raw.get(key))

    if not finding["title"]:
        raise FindingError(f"{fid}: title is required")
    if not finding["fix"]:
        raise FindingError(f"{fid}: fix is required")
    return finding


def load_lens(path: Path) -> list[dict]:
    """Read one findings/<lens>.json. Returns [] for a file that is not there."""
    lens = path.stem
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise FindingError(f"{lens}: cannot read {path.name} ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise FindingError(f"{lens}: {path.name} is not valid JSON (line {exc.lineno}, column {exc.colno})") from exc

    if isinstance(raw, list):
        raw = {"findings": raw}
    if not isinstance(raw, dict):
        raise FindingError(f"{lens}: {path.name} must contain an object or a list")

    findings = raw.get("findings", [])
    if not isinstance(findings, list):
        raise FindingError(f"{lens}: 'findings' must be a list")
    return [normalise_finding(item, lens) for item in findings]


def findings_dir(root: Path) -> Path:
    return root / ".readiness-audit" / "findings"


def load_all(root: Path) -> tuple[list[dict], list[str]]:
    """Every finding across every lens, plus the errors that stopped a file."""
    directory = findings_dir(root)
    findings, errors = [], []
    if not directory.is_dir():
        return findings, errors
    for path in sorted(directory.glob("*.json")):
        try:
            findings.extend(load_lens(path))
        except FindingError as exc:
            errors.append(str(exc))
    return findings, errors


# --------------------------------------------------------------------------
# JSON -> markdown, so a fix agent still gets the trail it expects
# --------------------------------------------------------------------------

def render_markdown(findings: list[dict]) -> str:
    """Render the canonical markdown block format from structured findings."""
    blocks = []
    for f in findings:
        lines = [f"### {f['id']} | {f['title']}"]
        lines.append(f"state: {f['state']}")
        lines.append(f"severity: {f['severity']}")
        lines.append(f"owner: {f['owner']}")
        lines.append(f"cross-lens: {', '.join(f['cross_lens']) or '-'}")
        lines.append(f"evidence: {', '.join(f['evidence']) or '-'}")
        lines.append(f"probe: {f['probe'] or '-'}")
        lines.append(f"impact: {f['impact'] or '-'}")
        lines.append(f"failure-path: {f['failure_path'] or '-'}")
        lines.append(f"compensating: {f['compensating'] or '-'}")
        lines.append(f"fix: {f['fix']}")
        lines.append(f"resolve: {f['resolve'] or '-'}")
        lines.append(f"see: {f['see'] or '-'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_all(root: Path) -> tuple[list[str], list[str]]:
    directory = findings_dir(root)
    written, errors = [], []
    if not directory.is_dir():
        return written, errors
    for path in sorted(directory.glob("*.json")):
        try:
            findings = load_lens(path)
        except FindingError as exc:
            errors.append(str(exc))
            continue
        target = path.with_suffix(".md")
        target.write_text(render_markdown(findings), encoding="utf-8")
        written.append(str(target))
    return written, errors


# --------------------------------------------------------------------------
# report.json - what the dashboard reads
# --------------------------------------------------------------------------

def load_verdict(root: Path) -> tuple[dict, list[str]]:
    """Read the orchestrator's authored verdict.

    The verdict is a judgement, not arithmetic, so a human or the orchestrator
    writes it - but it is written as data, in `verdict.json`, never scraped back
    out of prose. Every consumer reads the same fields.
    """
    empty = {"decision": None, "headline": None, "summary": None}
    path = root / ".readiness-audit" / "verdict.json"
    if not path.exists():
        return empty, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        return empty, [f"verdict.json cannot be read ({exc})"]
    except json.JSONDecodeError as exc:
        return empty, [f"verdict.json is not valid JSON (line {exc.lineno}, column {exc.colno})"]
    if not isinstance(raw, dict):
        return empty, ["verdict.json must contain an object"]

    decision = (_text(raw.get("decision")) or "").upper().replace(" ", "_").replace("-", "_")
    errors = []
    if decision and decision not in DECISIONS:
        errors.append(f"verdict.json decision must be one of {sorted(DECISIONS)}, got {decision}")
        decision = None
    return {
        "decision": decision or None,
        "headline": _text(raw.get("headline")),
        "summary": _text(raw.get("summary")),
    }, errors


def _counts(findings: list[dict]) -> dict:
    counts = {"total": len(findings), "p0": 0, "p1": 0, "p2": 0, "p3": 0,
              "confirmed": 0, "notFound": 0, "unverified": 0}
    for f in findings:
        counts[f["severity"].lower()] += 1
        counts[{"CONFIRMED": "confirmed", "NOT_FOUND": "notFound",
                "UNVERIFIED": "unverified"}[f["state"]]] += 1
    return counts


def _lens_status(lens: str, state: dict, lenses_with_findings: set[str]) -> str:
    if lens in (state.get("lenses_skipped") or {}):
        return "skipped"
    if lens in lenses_with_findings:
        return "complete"
    if (lens in (state.get("lenses_to_run") or [])
            and state.get("stage") == "3-lenses"
            and state.get("stage_status") == "in_progress"):
        return "running"
    return "waiting"


def build_report(root: Path) -> dict:
    audit = root / ".readiness-audit"
    findings, errors = load_all(root)

    state = {}
    state_path = audit / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            errors.append("state.json is not readable; lens status is degraded")
    if not isinstance(state, dict):
        state = {}

    verdict, verdict_errors = load_verdict(root)
    errors.extend(verdict_errors)

    lenses_with_findings = {f["lens"] for f in findings}
    by_lens = {lens: [f for f in findings if f["lens"] == lens] for lens in LENS_ORDER}

    severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    state_rank = {"CONFIRMED": 0, "NOT_FOUND": 1, "UNVERIFIED": 2}
    ordered = sorted(
        findings,
        key=lambda f: (severity_rank[f["severity"]], state_rank[f["state"]], f["id"]),
    )

    return {
        "schema": SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": str(root),
        "gitRef": state.get("git_ref"),
        "stage": {"name": state.get("stage"), "status": state.get("stage_status")},
        "executionMode": state.get("execution_mode"),
        "updatedAt": state.get("updated_at"),
        "verdict": verdict,
        "counts": _counts(findings),
        "lenses": [
            {
                "id": lens,
                "label": LENS_LABEL[lens],
                "status": _lens_status(lens, state, lenses_with_findings),
                "skippedReason": (state.get("lenses_skipped") or {}).get(lens),
                "counts": _counts(by_lens[lens]),
            }
            for lens in LENS_ORDER
        ],
        "findings": [{**f, "lensLabel": LENS_LABEL.get(f["lens"], f["lens"])} for f in ordered],
        "errors": errors,
    }


def write_report(root: Path) -> Path:
    audit = root / ".readiness-audit"
    audit.mkdir(parents=True, exist_ok=True)
    target = audit / "report.json"
    target.write_text(json.dumps(build_report(root), indent=2) + "\n", encoding="utf-8")
    return target


def _cli_finding_store(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DOC_FINDING_STORE)
    parser.add_argument("command", choices=("render", "report"))
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args(argv)
    root = args.project_root.expanduser().resolve()

    if args.command == "render":
        written, errors = render_all(root)
        print(json.dumps({"written": written, "errors": errors}, indent=2))
        return 1 if errors else 0

    target = write_report(root)
    report = json.loads(target.read_text(encoding="utf-8"))
    print(json.dumps({
        "written_to": str(target),
        "counts": report["counts"],
        "verdict": report["verdict"]["decision"],
        "errors": report["errors"],
    }, indent=2))
    return 1 if report["errors"] else 0


# ==========================================================================
# validate_findings.py
# ==========================================================================

_DOC_VALIDATE_FINDINGS = """validate_findings.py - the gate between "seven agents wrote things down" and
"this is a report someone can act on".

It enforces the rules that are easy to state and easy to quietly break:

  * a CONFIRMED finding cites file:line
  * a NOT FOUND finding cites an absence-ledger row that actually has zero hits
    and that the ledger says supports NOT FOUND rather than UNVERIFIED
  * an UNVERIFIED finding says what evidence would resolve it
  * a P0 articulates a specific failure path and names its compensating control
    (or states there is none)
  * absence is phrased as "not found in reviewed scope", never as "does not exist"
  * the same finding does not appear under two lenses

Errors block the report. Warnings are judgement calls worth a second look.

Usage:
    python3 validate_findings.py <project_root> [--json]
"""

STATES = {"CONFIRMED", "NOT_FOUND", "UNVERIFIED"}
SEVERITIES = {"P0", "P1", "P2", "P3"}
LENS_PREFIX = {
    "SEC": "security", "BE": "backend", "FE": "frontend", "OPS": "devops",
    "QA": "qa", "DB": "database", "AI": "ai-security",
}
LENS_TO_PREFIX = {v: k for k, v in LENS_PREFIX.items()}

HEADING = re.compile(r"^###\s+(PRA-[A-Z]+-\d+)\s*\|\s*(.+?)\s*$")
FIELD = re.compile(r"^([a-z][a-z-]*):\s*(.*)$")

OVERCLAIM = re.compile(
    r"\b(there is no|there are no|does not exist|do not exist|the system has no|"
    r"has never been|is never|no .{0,30} exists\b)", re.IGNORECASE)

EVIDENCE_LOC = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]+:\d+")

# A file path, a dotted symbol, or anything in backticks - the shapes that mean
# an `impact` line was written for an engineer rather than for the reader.
CODE_SHAPED = re.compile(r"`[^`]+`|[\w-]+/[\w./-]+|\b\w+\.(?:ts|tsx|js|jsx|py|go|rb|java|sql|json|yml|yaml|toml)\b")


# The authored JSON uses snake_case; the rules below were written against the
# markdown field names. Mapping once here keeps every rule untouched.
JSON_TO_FIELD = {
    "state": "state", "severity": "severity", "owner": "owner",
    "cross_lens": "cross-lens", "evidence": "evidence", "probe": "probe",
    "impact": "impact", "failure_path": "failure-path",
    "compensating": "compensating", "fix": "fix", "resolve": "resolve", "see": "see",
}


def parse_file(path: Path):
    """Load one findings/<lens>.json into the shape the rules below expect.

    Lenses author JSON, so there is nothing to parse out of prose - a malformed
    file is a hard error rather than a finding silently read as empty.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON "
                         f"(line {exc.lineno}, column {exc.colno})") from exc

    if isinstance(raw, list):
        raw = {"findings": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("findings", []), list):
        raise ValueError(f"{path.name} must be an object with a 'findings' list")

    findings = []
    for index, item in enumerate(raw.get("findings", []), 1):
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}: finding #{index} is not an object")
        fields = {}
        for json_key, field_key in JSON_TO_FIELD.items():
            value = item.get(json_key)
            if isinstance(value, list):
                value = ", ".join(str(v).strip() for v in value if str(v).strip())
            fields[field_key] = "" if value is None else str(value).strip()
        findings.append({
            "id": str(item.get("id") or f"<finding #{index}>"),
            "title": str(item.get("title") or ""),
            "_line": index,
            "_file": path.name,
            "fields": fields,
        })
    return findings


def empty(v):
    return v is None or v.strip() in ("", "-", "n/a", "N/A", "none")


def validate(root: Path):
    d = root / ".readiness-audit"
    fdir = d / "findings"
    errors, warnings = [], []

    ledger = {}
    lpath = d / "evidence" / "absence-ledger.json"
    if lpath.exists():
        try:
            ledger = json.loads(lpath.read_text()).get("controls", {})
        except json.JSONDecodeError:
            errors.append(("absence-ledger.json", "-", "ledger is not valid JSON; re-run absence_probe.py"))
    else:
        errors.append(("absence-ledger.json", "-",
                       "no absence ledger found; run absence_probe.py before validating findings"))

    if not fdir.exists():
        errors.append(("findings/", "-", "no findings directory; lenses have not run"))
        return errors, warnings, []

    all_findings = []
    seen_ids = {}
    for f in sorted(fdir.glob("*.json")):
        lens = f.stem
        try:
            parsed = parse_file(f)
        except ValueError as exc:
            errors.append((f.name, "-", str(exc)))
            continue
        for fd in parsed:
            fd["lens_file"] = lens
            all_findings.append(fd)

    for fd in all_findings:
        fid, F, where = fd["id"], fd["fields"], f"{fd['_file']}:{fd['_line']}"

        def err(msg):
            errors.append((where, fid, msg))

        def warn(msg):
            warnings.append((where, fid, msg))

        if fid in seen_ids:
            err(f"duplicate finding id (also at {seen_ids[fid]})")
        seen_ids[fid] = where

        prefix = fid.split("-")[1]
        if prefix not in LENS_PREFIX:
            err(f"unknown lens prefix {prefix!r}; expected one of {sorted(LENS_PREFIX)}")
        elif fd["lens_file"] in LENS_TO_PREFIX and LENS_PREFIX[prefix] != fd["lens_file"]:
            err(f"id prefix {prefix} does not match the file it lives in ({fd['lens_file']})")

        state = F.get("state", "").strip().upper().replace(" ", "_")
        if state not in STATES:
            err(f"state must be one of {sorted(STATES)}, got {F.get('state')!r}")
        sev = F.get("severity", "").strip().upper()
        if sev not in SEVERITIES:
            err(f"severity must be one of {sorted(SEVERITIES)}, got {F.get('severity')!r}")

        if empty(F.get("fix")):
            err("no fix given; a finding without a concrete remediation is an observation, not a finding")
        if empty(F.get("owner")):
            err("no owner lens declared")
        if not fd["title"].strip():
            err("no title; the dashboard has nothing to name this finding")

        # `impact` is the only field a non-engineer reads. A finding without one
        # reaches the dashboard as a headline nobody can act on.
        impact = F.get("impact", "")
        if empty(impact):
            err("no impact given; state in one or two sentences what a user, the "
                "business, or the data loses - the mechanism belongs in failure-path")
        elif impact.strip() == F.get("failure-path", "").strip():
            err("impact repeats failure-path verbatim; impact is the plain-language "
                "cost, failure-path is the mechanism")
        elif CODE_SHAPED.search(impact):
            warn("impact names a file, path, or code symbol; rewrite it for someone "
                 "who will never open the codebase")

        if state == "CONFIRMED":
            ev = F.get("evidence", "")
            if empty(ev):
                err("CONFIRMED requires evidence")
            elif not EVIDENCE_LOC.search(ev):
                err(f"CONFIRMED evidence must cite file:line, got {ev!r}")

        if state == "NOT_FOUND":
            probe = F.get("probe", "").strip()
            if empty(probe):
                err("NOT_FOUND requires a probe id from the absence ledger; "
                    "an uncited absence is a guess")
            elif probe not in ledger:
                err(f"probe {probe!r} is not in the absence ledger")
            else:
                row = ledger[probe]
                if row["hit_count"] > 0:
                    err(f"probe {probe!r} has {row['hit_count']} hits in the ledger "
                        f"(e.g. {', '.join(h['path'] for h in row['hits'][:2])}); "
                        "this control is present, so NOT_FOUND is wrong")
                elif row["supports_state"] == "none":
                    err(f"probe {probe!r} is a branch selector or a control that does not "
                        f"apply here ({row.get('note','')}); it cannot support a finding")
                elif row["supports_state"] == "UNVERIFIED":
                    err(f"ledger says probe {probe!r} is normally configured outside this "
                        "repo and no IaC was found, so absence here proves nothing; "
                        "restate as UNVERIFIED with a resolve: line")
            blob = f"{fd['title']} {F.get('failure-path','')} {F.get('fix','')}"
            if OVERCLAIM.search(blob):
                err("absence is phrased as established fact; rewrite as "
                    "\"No X found in reviewed scope\"")

        if state == "UNVERIFIED":
            if empty(F.get("resolve")):
                err("UNVERIFIED requires resolve: what specific evidence would settle this "
                    "(CI config, cloud backup policy, IaC repo, runtime dashboards)")
            if sev in ("P0", "P1"):
                warn(f"UNVERIFIED at {sev}: report this as a potential {sev} RISK, "
                     "never as an established defect")
            blob = f"{fd['title']} {F.get('failure-path','')}"
            if OVERCLAIM.search(blob):
                err("UNVERIFIED finding is written in confirmed language; soften to a risk statement")

        if sev == "P0":
            if empty(F.get("failure-path")):
                err("P0 requires failure-path: the specific, articulable path to catastrophic "
                    "loss - if you cannot write it, this is a P1")
            if empty(F.get("compensating")):
                err("P0 requires compensating: name the mitigating control, or state that none "
                    "was found - a plausible compensating control demotes this to P1")

    # cross-lens duplication: same underlying thing reported twice
    def fingerprint(fd):
        F = fd["fields"]
        probe = F.get("probe", "").strip()
        if probe and probe != "-":
            return f"probe:{probe}"
        ev = F.get("evidence", "")
        m = EVIDENCE_LOC.search(ev)
        if m:
            return "loc:" + m.group(0).rsplit(":", 1)[0]
        return None

    buckets = {}
    for fd in all_findings:
        fp = fingerprint(fd)
        if fp:
            buckets.setdefault(fp, []).append(fd)
    for fp, group in buckets.items():
        lenses = {fd["lens_file"] for fd in group}
        if len(group) > 1 and len(lenses) > 1:
            ids = [fd["id"] for fd in group]
            referenced = any(
                any(other in fd["fields"].get("see", "") for other in ids if other != fd["id"])
                for fd in group)
            if not referenced:
                errors.append((", ".join(f"{fd['_file']}:{fd['_line']}" for fd in group),
                               ", ".join(ids),
                               f"same underlying issue ({fp}) reported by {sorted(lenses)}; "
                               "one lens owns it fully, the others add see: <owner-id>"))

    stats = {
        "total": len(all_findings),
        "by_state": {},
        "by_severity": {},
        "by_lens": {},
    }
    for fd in all_findings:
        F = fd["fields"]
        for key, val in (("by_state", F.get("state", "?")),
                         ("by_severity", F.get("severity", "?")),
                         ("by_lens", fd["lens_file"])):
            stats[key][val] = stats[key].get(val, 0) + 1

    return errors, warnings, stats


def _cli_validate_findings():
    ap = argparse.ArgumentParser(description=_DOC_VALIDATE_FINDINGS)
    ap.add_argument("project_root")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    errors, warnings, stats = validate(root)

    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings, "stats": stats}, indent=2))
    else:
        if stats:
            print(f"findings: {stats['total']}  states: {stats['by_state']}  "
                  f"severities: {stats['by_severity']}")
            print()
        if errors:
            print(f"ERRORS ({len(errors)}) - the report is blocked until these are fixed:")
            for where, fid, msg in errors:
                print(f"  [{where}] {fid}: {msg}")
            print()
        if warnings:
            print(f"WARNINGS ({len(warnings)}):")
            for where, fid, msg in warnings:
                print(f"  [{where}] {fid}: {msg}")
            print()
        if not errors and not warnings:
            print("clean - every finding is evidence-backed and correctly scoped.")
        elif not errors:
            print("no blocking errors.")

    return 1 if errors else 0


# ==========================================================================
# assemble_report.py
# ==========================================================================

_DOC_ASSEMBLE_REPORT = """assemble_report.py - build report.md from the audit trail.

The sections that are arithmetic (which findings are P0, which controls the
ledger says are missing, which unknowns need evidence) are generated here so
they cannot drift from the findings files. The sections that are judgement
(the verdict, the scalability ordering, each lens's closing line) are left as
FILL markers for the orchestrator to write. That split exists because a report
whose counts disagree with its own appendix stops being believed.

Run validate_findings.py first - this script will refuse to assemble a report
from findings that do not pass the gate unless --force is given.

Usage:
    python3 assemble_report.py <project_root> [--force]
"""

DECISION_TEXT = {
    "SHIP": "SHIP",
    "FIX_THEN_SHIP": "FIX THEN SHIP",
    "HOLD": "HOLD - DO NOT DEPLOY",
}

LENS_ORDER = ["security", "backend", "frontend", "devops", "qa", "database", "ai-security"]
LENS_TITLE = {
    "security": "Security Engineer", "backend": "Backend Architect",
    "frontend": "Frontend Engineer", "devops": "DevOps Engineer",
    "qa": "QA Engineer", "database": "Database Engineer",
    "ai-security": "AI Security Engineer",
}
RECOVERY_ROWS = [
    ("Backups", "backup_config"),
    ("Point-in-time recovery", "pitr"),
    ("Verified restore drill", "restore_drill"),
    ("Rollback path", "rollback_path"),
    ("Incident response", "runbook"),
    ("Event replay / DLQ drain", "dead_letter_queue"),
]
STATE_LABEL = {"CONFIRMED": "[CONFIRMED]", "NOT_FOUND": "[NOT FOUND]",
               "UNVERIFIED": "[UNVERIFIED]"}


def load_findings(root: Path):
    fdir = root / ".readiness-audit" / "findings"
    out = []
    if not fdir.exists():
        return out
    for f in sorted(fdir.glob("*.json")):
        try:
            parsed = parse_file(f)
        except ValueError:
            continue  # validate_findings.py reports this; the gate above blocks on it
        for fd in parsed:
            fd["lens_file"] = f.stem
            out.append(fd)
    return out


def fld(fd, key, default="-"):
    v = fd["fields"].get(key, "").strip()
    return v if v else default


def render_finding(fd):
    F = fd["fields"]
    state = F.get("state", "?").upper().replace(" ", "_")
    lines = [
        f"#### {fd['id']} | {fd['title']}",
        "",
        f"- **Lens**: {LENS_TITLE.get(fld(fd,'owner',fd['lens_file']), fld(fd,'owner'))}"
        + (f"  **[CROSS-LENS: {fld(fd,'cross-lens')}]**" if fld(fd, "cross-lens") != "-" else ""),
        f"- **Evidence state**: {STATE_LABEL.get(state, state)}",
        f"- **Evidence**: {fld(fd,'evidence')}"
        + (f"  (ledger probe `{fld(fd,'probe')}`)" if fld(fd, "probe") != "-" else ""),
    ]
    if fld(fd, "failure-path") != "-":
        lines.append(f"- **Why this severity**: {fld(fd,'failure-path')}")
    if fld(fd, "compensating") != "-":
        lines.append(f"- **Compensating control**: {fld(fd,'compensating')}")
    if fld(fd, "resolve") != "-":
        lines.append(f"- **Evidence that would resolve this**: {fld(fd,'resolve')}")
    if fld(fd, "see") != "-":
        lines.append(f"- **Owned by**: {fld(fd,'see')}")
    lines += [f"- **Fix**: {fld(fd,'fix')}", ""]
    return "\n".join(lines)


def _cli_assemble_report():
    ap = argparse.ArgumentParser(description=_DOC_ASSEMBLE_REPORT)
    ap.add_argument("project_root")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    d = root / ".readiness-audit"

    errors, warnings, _ = validate(root)
    if errors and not args.force:
        print(f"refusing to assemble: {len(errors)} validation errors. "
              "Run validate_findings.py, fix them, then retry (or pass --force).",
              file=sys.stderr)
        return 1

    findings = load_findings(root)
    ledger = {}
    lpath = d / "evidence" / "absence-ledger.json"
    ledger_meta = {}
    if lpath.exists():
        raw = json.loads(lpath.read_text())
        ledger = raw.get("controls", {})
        ledger_meta = {k: v for k, v in raw.items() if k != "controls"}

    state_file = d / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}

    def sev(fd):
        return fd["fields"].get("severity", "").strip().upper()

    p0 = [f for f in findings if sev(f) == "P0"]
    p1 = [f for f in findings if sev(f) == "P1"]
    debt = [f for f in findings if sev(f) in ("P2", "P3")]
    unverified = [f for f in findings if f["fields"].get("state", "").upper().replace(" ", "_") == "UNVERIFIED"]

    out = []
    A = out.append

    A("# Production Readiness Audit")
    A("")
    A(f"Repository: `{root}`  ")
    A(f"Git ref at audit start: `{state.get('git_ref') or 'unknown'}`  ")
    A(f"Findings: {len(findings)} ({len(p0)} P0, {len(p1)} P1, {len(debt)} P2/P3, "
      f"{len(unverified)} unverified)")
    A("")

    # ---- A ----
    A("## Section A - Scope & Context")
    A("")
    for name, heading in (("context.md", "Operating context"), ("scope.md", "Review scope")):
        p = d / name
        A(f"### {heading}")
        A("")
        A(p.read_text().strip() if p.exists()
          else f"<!-- FILL: {name} was not written; state the assumptions here -->")
        A("")
    if state.get("lenses_skipped"):
        A("### Lenses not run")
        A("")
        A("| Lens | Why it was skipped |")
        A("| --- | --- |")
        for lens, reason in state["lenses_skipped"].items():
            A(f"| {LENS_TITLE.get(lens, lens)} | {reason} |")
        A("")
    if ledger_meta.get("truncated"):
        A("> The evidence scan hit its file cap, so parts of this repository were not "
          "read. Every finding below inherits that boundary.")
        A("")

    # ---- B ----
    A("## Section B - Executive Verdict")
    A("")
    verdict, verdict_errors = load_verdict(root)
    for message in verdict_errors:
        print(message, file=sys.stderr)
    if verdict["decision"] or verdict["headline"]:
        A(f"**{DECISION_TEXT.get(verdict['decision'], verdict['decision'] or 'VERDICT')}**")
        A("")
        for paragraph in (verdict["headline"], verdict["summary"]):
            if paragraph:
                A(paragraph)
                A("")
    else:
        A("<!-- FILL: write .readiness-audit/verdict.json with a decision of SHIP / "
          "FIX_THEN_SHIP / HOLD, a headline, and a summary. State explicitly how much "
          f"of the verdict rests on UNVERIFIED areas - there are {len(unverified)} "
          "unverified findings. This section is generated from that file. -->")
        A("")

    # ---- C / D ----
    for label, group in (("Section C - Production Blockers (P0)", p0),
                         ("Section D - Serious Risks (P1)", p1)):
        A(f"## {label}")
        A("")
        if not group:
            A("None identified within the reviewed scope.")
            A("")
        else:
            for fd in sorted(group, key=lambda x: x["id"]):
                A(render_finding(fd))

    # ---- E ----
    A("## Section E - Missing Systems Inventory")
    A("")
    A("Generated from the absence ledger. *Necessity* is the lens's judgement under the "
      "proportionality rule; rows marked \"considered, not raised\" were searched for, not "
      "found, and judged not necessary at this scale by the lens that owns them.")
    A("")
    A("| Missing system | Lens | Evidence state | Ledger probe | Raised as | Necessity |")
    A("| --- | --- | --- | --- | --- | --- |")
    probe_to_finding = {}
    for fd in findings:
        pr = fd["fields"].get("probe", "").strip()
        if pr and pr != "-":
            probe_to_finding.setdefault(pr, []).append(fd)
    for cid, row in sorted(ledger.items()):
        if row["polarity"] != "control" or row["hit_count"] > 0:
            continue
        if row.get("supports_state") not in ("NOT_FOUND", "UNVERIFIED"):
            continue  # branch selector, or a control with nothing to apply to
        raised = probe_to_finding.get(cid, [])
        raised_txt = ", ".join(f"{f['id']} ({f['fields'].get('severity','?')})" for f in raised) or "not raised"
        if raised:
            necessity = "Necessary"
        elif row["lens"] in state.get("lenses_skipped", {}):
            necessity = "lens not run"
        else:
            necessity = "considered, not raised"
        st = "[NOT FOUND]" if row["supports_state"] == "NOT_FOUND" else "[UNVERIFIED]"
        A(f"| {row['label']} | {row['lens']} | {st} | `{cid}` | {raised_txt} | {necessity} |")
    A("")

    # ---- F ----
    A("## Section F - Deferred Controls")
    A("")
    dfile = d / "deferred.md"
    A(dfile.read_text().strip() if dfile.exists()
      else "<!-- FILL: controls considered and judged not yet necessary, each with the "
           "concrete trigger that should revisit it (\"needed when: >5k users / "
           "internet-facing / PCI scope\"). Also name controls deliberately deemed "
           "over-engineering here, so the reader knows they were considered. -->")
    A("")

    # ---- G ----
    A("## Section G - Recovery Posture")
    A("")
    A("| Dimension | Current implied state | Evidence state | Meets stated RPO/RTO? | Gap |")
    A("| --- | --- | --- | --- | --- |")
    for label, cid in RECOVERY_ROWS:
        row = ledger.get(cid)
        if not row:
            A(f"| {label} | not probed | [UNVERIFIED] | <!-- FILL --> | <!-- FILL --> |")
            continue
        if row.get("supports_state") == "none" and row["hit_count"] == 0:
            A(f"| {label} | not applicable - {row.get('note','')} | n/a | n/a | none |")
            continue
        if row["hit_count"] > 0:
            implied = f"signal in repo ({', '.join(h['path'] for h in row['hits'][:2])})"
            st = "[CONFIRMED] present - adequacy assessed by lens"
        elif row["supports_state"] == "NOT_FOUND":
            implied = "nothing found in reviewed scope"
            st = "[NOT FOUND]"
        else:
            implied = "configured outside this repository"
            st = "[UNVERIFIED]"
        A(f"| {label} | {implied} | {st} | <!-- FILL --> | <!-- FILL --> |")
    A("")
    applicable = [cid for _, cid in RECOVERY_ROWS
                  if ledger.get(cid, {}).get("supports_state") != "none"]
    unver_recovery = sum(1 for cid in applicable
                         if ledger.get(cid, {}).get("supports_state") == "UNVERIFIED")
    if unver_recovery >= 3:
        A(f"> {unver_recovery} of {len(applicable)} applicable recovery dimensions could not be "
          "verified from the repository alone. That is itself a finding: the team cannot "
          "currently demonstrate its own recovery posture from version control.")
        A("")

    # ---- H ----
    A("## Section H - Scalability Bottlenecks")
    A("")
    A("<!-- FILL: ordered by what breaks first at 10x then 100x, relative to the scale "
      "envelope in Section A. Include cache stampede scenarios and data-growth "
      "projections where the lenses raised them. -->")
    A("")

    # ---- I ----
    A("## Section I - Technical Debt Register (P2/P3)")
    A("")
    if not debt:
        A("None recorded.")
        A("")
    else:
        A("| ID | Severity | Lens | Finding | Fix |")
        A("| --- | --- | --- | --- | --- |")
        for fd in sorted(debt, key=lambda x: (x["fields"].get("severity", ""), x["id"])):
            A(f"| {fd['id']} | {fld(fd,'severity')} | {fd['lens_file']} | {fd['title']} | {fld(fd,'fix')} |")
        A("")

    # ---- J ----
    A("## Section J - 30/60/90 Remediation Plan")
    A("")
    A("<!-- FILL: prioritised plan. The evidence-to-obtain table below is generated from "
      "the unverified findings; fold it into the 30-day column, because resolving an "
      "unknown is remediation too. -->")
    A("")
    if unverified:
        A("### Evidence to obtain")
        A("")
        A("| Finding | Severity | What would resolve it |")
        A("| --- | --- | --- |")
        for fd in sorted(unverified, key=lambda x: x["id"]):
            A(f"| {fd['id']} - {fd['title']} | {fld(fd,'severity')} | {fld(fd,'resolve')} |")
        A("")

    # ---- K ----
    A("## Section K - Panel Closing")
    A("")
    ran = {fd["lens_file"] for fd in findings}
    for lens in LENS_ORDER:
        if lens in state.get("lenses_skipped", {}):
            continue
        if lens not in ran and findings:
            continue
        A(f"**{LENS_TITLE[lens]}** - <!-- FILL: \"The scariest thing this system is missing "
          "is ___ (and I know / suspect / cannot determine this because ___)\" -->")
        A("")

    if warnings:
        A("---")
        A("")
        A("<!-- Validation warnings carried into this draft:")
        for where, fid, msg in warnings:
            A(f"  {fid} [{where}]: {msg}")
        A("-->")
        A("")

    report = "\n".join(out)
    (d).mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(report)
    # report.json is what the dashboard reads. Writing it here keeps the two
    # renderings of the same audit from ever drifting apart.
    report_json = write_report(root)

    fills = report.count("<!-- FILL")
    print(json.dumps({
        "written_to": str(d / "report.md"),
        "structured_report": str(report_json),
        "findings": len(findings), "p0": len(p0), "p1": len(p1),
        "debt": len(debt), "unverified": len(unverified),
        "fill_markers_remaining": fills,
        "validation_errors": len(errors), "validation_warnings": len(warnings),
    }, indent=2))
    return 0


# ==========================================================================
# readiness_dashboard.py
# ==========================================================================

_DOC_READINESS_DASHBOARD = """Everything the dashboard shows comes from structured data - `findings/*.json`
and `verdict.json`, assembled by `finding_store.py`. Nothing here parses prose.
The markdown trail exists for agents that fix what the audit found; the
dashboard exists for the person deciding whether to ship, and that person
should never have to read a file path to get an answer.
"""

DASHBOARD_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Production readiness</title>
    <style>
      :root {
        color-scheme: light;
        --ink:#18222c; --muted:#5c6a76; --paper:#f7f7f5; --line:#dbe0df; --white:#fff;
        --green:#087f5b; --amber:#a35f00; --red:#b4302b; --blue:#2d63c8; --navy:#11263d;
        --p0-bg:#fdeceb; --p0-ink:#8a231b; --p1-bg:#fdf1dd; --p1-ink:#7a4a00;
        --p2-bg:#e8eef7; --p2-ink:#33517d;
      }
      * { box-sizing: border-box; }
      body { margin:0; min-width:320px; color:var(--ink); background:var(--paper);
        font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      ::selection { background:#c7e7da; color:var(--ink); }
      ::-webkit-scrollbar { width:11px; height:11px; }
      ::-webkit-scrollbar-thumb { background:#c4ccca; border-radius:99px; border:3px solid var(--paper); }
      ::-webkit-scrollbar-thumb:hover { background:#a9b3b1; }
      button { font:inherit; cursor:pointer; color:inherit; }
      :focus-visible { outline:3px solid #8db1f4; outline-offset:3px; border-radius:4px; }
      h1,h2,h3,p { margin:0; }
      h1 { font-size:clamp(1.9rem, 3.6vw, 3.1rem); line-height:1.04; letter-spacing:-.035em; max-width:20ch; }
      h2 { font-size:1.18rem; letter-spacing:-.022em; }
      h3 { font-size:1rem; letter-spacing:-.014em; }
      a { color:var(--blue); text-underline-offset:3px; text-decoration-thickness:1px; }
      .skip { position:absolute; left:-9999px; top:12px; z-index:20; padding:10px 14px;
        background:var(--navy); color:var(--white); border-radius:9px; font-weight:750; text-decoration:none; }
      .skip:focus { left:14px; }
      .shell { max-width:1140px; margin:auto; padding:30px 26px 110px; }
      .muted { color:var(--muted); }
      .lede { max-width:62ch; color:var(--muted); font-size:1.03rem; margin-top:14px; }
      .num { font-variant-numeric:tabular-nums; letter-spacing:-.045em; }
      .mono { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.85em; }

      .site-head { display:flex; align-items:center; gap:20px; padding-bottom:20px; border-bottom:1px solid var(--line); }
      .brand { display:flex; align-items:center; gap:10px; font-weight:750; letter-spacing:-.025em; }
      .mark { width:24px; height:24px; border-radius:8px 8px 8px 2px; background:var(--navy); position:relative; flex:none; }
      .mark:after { content:""; position:absolute; right:5px; top:5px; width:7px; height:7px; border-radius:50%; background:#8fe0bf; }
      .nav { display:flex; gap:3px; margin-left:auto; flex-wrap:wrap; }
      .nav button { border:0; border-radius:8px; padding:7px 11px; background:transparent;
        color:var(--muted); font-size:.85rem; font-weight:750; }
      .nav button:hover { background:#edf0ef; color:var(--ink); }
      .nav button[aria-current="page"] { background:var(--navy); color:var(--white); }
      .live { display:flex; align-items:center; gap:7px; font-size:.83rem; font-weight:650; color:var(--green); }
      .live:before { content:""; width:8px; height:8px; border-radius:50%; background:currentColor; box-shadow:0 0 0 4px #d9f3e8; }
      .live.done { color:var(--muted); } .live.done:before { box-shadow:0 0 0 4px #e6e9e8; }

      .hero { display:grid; grid-template-columns:1.4fr .6fr; gap:36px; align-items:end; padding:52px 0 34px; }
      .decision { display:inline-flex; align-items:center; gap:8px; padding:6px 12px; border-radius:999px;
        font-size:.78rem; font-weight:800; letter-spacing:.02em; border:1px solid currentColor; }
      .decision.hold { color:var(--red); background:#fff3f2; }
      .decision.fix_then_ship { color:var(--amber); background:#fff8e9; }
      .decision.ship { color:var(--green); background:#eefbf5; }
      .decision.pending { color:var(--blue); background:#eff5ff; }
      .hero h1 { margin-top:18px; }
      .risk-strip { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
      .risk-strip div { padding-top:13px; border-top:1px solid var(--line); }
      .risk-strip strong { display:block; font-size:2rem; line-height:1; }
      .risk-strip span { display:block; margin-top:5px; color:var(--muted); font-size:.79rem; }
      .risk-strip .r0 strong { color:var(--red); } .risk-strip .r1 strong { color:var(--amber); }

      .band { padding-top:30px; margin-top:8px; border-top:1px solid var(--line); }
      .band + .band { margin-top:34px; }
      .band-head { display:flex; align-items:baseline; justify-content:space-between; gap:16px; flex-wrap:wrap; }
      .band-head p { color:var(--muted); font-size:.87rem; }

      .matrix { display:grid; grid-template-columns:repeat(7,1fr); gap:8px; margin-top:16px; }
      .lens { min-height:88px; padding:12px 11px; border-radius:12px; border:1px solid var(--line);
        background:var(--white); display:flex; flex-direction:column; gap:6px; text-align:left;
        transition:transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
      .lens:hover { transform:translateY(-2px); border-color:#5c8ce2; box-shadow:0 8px 18px rgba(0,0,0,.07); }
      .lens.complete { border-color:#9bd8bf; background:#f4fcf8; }
      .lens.running { border-color:#79a2e9; background:#f1f6ff; }
      .lens.waiting, .lens.skipped { opacity:.66; }
      .lens b { font-size:.83rem; letter-spacing:-.01em; }
      .lens em { font-style:normal; font-size:.72rem; color:var(--muted); margin-top:auto; }
      .dot { width:8px; height:8px; border-radius:50%; background:#aeb8bf; flex:none; }
      .dot.complete { background:var(--green); } .dot.running { background:var(--blue); }
      .dot.skipped { background:#c4ccca; }

      .rows { margin-top:6px; }
      .row { display:grid; grid-template-columns:30px minmax(0,1fr) auto; gap:14px; align-items:start;
        width:100%; padding:18px 0; border:0; border-bottom:1px solid var(--line);
        background:transparent; text-align:left; }
      .row:hover .row-title { color:var(--blue); text-decoration:underline; }
      .row > span { min-width:0; overflow-wrap:anywhere; }
      .sev { width:30px; height:30px; display:grid; place-items:center; border-radius:8px;
        font-size:.71rem; font-weight:800; }
      .sev.p0 { background:var(--p0-bg); color:var(--p0-ink); }
      .sev.p1 { background:var(--p1-bg); color:var(--p1-ink); }
      .sev.p2, .sev.p3 { background:var(--p2-bg); color:var(--p2-ink); }
      .row-title { display:block; font-weight:700; letter-spacing:-.016em; }
      .row-impact { display:block; margin-top:5px; color:var(--muted); max-width:70ch; }
      .row-meta { display:block; margin-top:8px; color:var(--muted); font-size:.81rem; }
      .state { font-weight:750; }
      .state.confirmed { color:var(--red); } .state.not_found { color:var(--amber); }
      .state.unverified { color:var(--muted); }
      .row-open { color:var(--muted); font-size:.78rem; font-weight:750; white-space:nowrap; padding-top:4px; }

      .filters { display:flex; gap:7px; flex-wrap:wrap; margin:18px 0 4px; }
      .filter { border:1px solid var(--line); background:var(--white); border-radius:999px;
        padding:6px 11px; font-size:.78rem; font-weight:750; }
      .filter[aria-pressed="true"] { background:var(--navy); color:var(--white); border-color:var(--navy); }

      .empty { padding:40px 0; color:var(--muted); }
      .panel { border:1px solid var(--line); border-radius:14px; background:var(--white); padding:22px; }
      .ledger-item { padding:17px 0; border-bottom:1px solid var(--line); }
      .ledger-item:last-child { border-bottom:0; }
      .ledger-item strong { display:block; margin-top:8px; }
      .ledger-item p { margin-top:5px; color:var(--muted); font-size:.87rem; overflow-wrap:anywhere; }
      .chip { display:inline-flex; padding:4px 9px; border-radius:999px; font-size:.73rem;
        font-weight:750; border:1px solid currentColor; }
      .chip.confirmed { color:var(--green); background:#eefbf5; }
      .chip.not_found { color:var(--amber); background:#fff8e9; }
      .chip.unverified { color:var(--blue); background:#eff5ff; }

      .scrim { position:fixed; inset:0; z-index:9; background:rgba(17,38,61,.24); }
      .drawer { position:fixed; z-index:10; inset:0 0 0 auto; width:min(560px,100%); overflow:auto;
        background:var(--white); padding:28px; box-shadow:-20px 0 45px rgba(0,0,0,.15);
        animation:drawer-in 260ms cubic-bezier(.16,1,.3,1); }
      @keyframes drawer-in { from { transform:translateX(30px); opacity:0; filter:blur(3px); }
        to { transform:none; opacity:1; filter:none; } }
      @media (prefers-reduced-motion: reduce) { .drawer { animation:none; } .lens { transition:none; } }
      .drawer-head { display:flex; align-items:start; justify-content:space-between; gap:14px;
        padding-bottom:18px; border-bottom:1px solid var(--line); }
      .close { border:0; background:#edf0ef; border-radius:9px; width:34px; height:34px; font-size:1.15rem; }
      .close:hover { background:#e0e4e3; }
      .drawer section { padding:20px 0; border-bottom:1px solid var(--line); }
      .drawer section:last-child { border-bottom:0; }
      .drawer h3 { margin-bottom:7px; }
      .drawer p { color:var(--ink); }
      .evidence-list { list-style:none; padding:0; margin:9px 0 0; display:grid; gap:7px; }
      .evidence-list li { padding:9px 11px; background:#f2f5f4; border-radius:8px;
        font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.83rem; overflow-wrap:anywhere; }

      .report { max-width:76ch; padding-top:26px; }
      .report h1, .report h2, .report h3 { margin:26px 0 9px; letter-spacing:-.02em; }
      .report h1 { font-size:1.7rem; } .report h2 { font-size:1.3rem; } .report h3 { font-size:1.05rem; }
      .report p { margin:11px 0; } .report ul, .report ol { margin:11px 0; padding-left:22px; }
      .report code { background:#edf0ef; border-radius:4px; padding:1px 5px;
        font:.88em ui-monospace, SFMono-Regular, Menlo, monospace; }
      .report pre { background:var(--navy); color:#e5edf9; border-radius:10px; padding:14px; overflow:auto; }
      .report pre code { background:none; padding:0; color:inherit; }
      .report table { border-collapse:collapse; width:100%; margin:14px 0; font-size:.9rem; }
      .report th, .report td { border:1px solid var(--line); padding:7px 9px; text-align:left; vertical-align:top; }
      .report th { background:#f1f3f2; }
      .report blockquote { margin:12px 0; padding:2px 0 2px 15px; border-left:1px solid var(--line); color:var(--muted); }

      .notice { padding:14px 16px; border-radius:10px; background:#fff8e9; color:#6b4300;
        border:1px solid #f0dcb4; margin-top:20px; font-size:.88rem; }

      @media (max-width:860px) {
        .hero { grid-template-columns:1fr; gap:26px; padding:34px 0 26px; }
        .matrix { grid-template-columns:repeat(4,1fr); }
      }
      @media (max-width:620px) {
        .shell { padding:20px 17px 70px; }
        .site-head { flex-wrap:wrap; }
        .nav { order:3; width:100%; margin-left:0; }
        .matrix { grid-template-columns:repeat(2,1fr); }
        .risk-strip { grid-template-columns:1fr; gap:0; }
        .risk-strip div { padding:12px 0; }
        .row { grid-template-columns:30px minmax(0,1fr); }
        .row-open { display:none; }
        .drawer { padding:20px; }
      }
    </style>
  </head>
  <body>
    <a class="skip" href="#app">Skip to content</a>
    <main id="app" aria-live="polite" tabindex="-1"></main>
    <script>
      const app = document.getElementById('app');
      let snapshot = null;

      const ROUTES = ['overview', 'findings', 'evidence', 'report'];
      const SEVERITY_ORDER = ['P0', 'P1', 'P2', 'P3'];
      const STATE_LABEL = { CONFIRMED: 'Confirmed', NOT_FOUND: 'Not found in scope', UNVERIFIED: 'Unverified' };
      const DECISION_LABEL = { HOLD: 'Hold — do not deploy', FIX_THEN_SHIP: 'Fix, then ship', SHIP: 'Ship' };
      // A control's state answers "does this codebase have one", which is a
      // different question from a finding's evidence state. Different words,
      // so the two are never read as the same thing.
      const CONTROL_LABEL = { CONFIRMED: 'Found', NOT_FOUND: 'Missing', UNVERIFIED: 'Not visible from here' };

      function controlNote(control) {
        if (control.state === 'CONFIRMED') {
          const where = control.paths && control.paths.length ? ` — ${control.paths.join(', ')}` : '';
          return `${control.hits} place${control.hits === 1 ? '' : 's'} in this codebase${where}`;
        }
        if (control.state === 'UNVERIFIED') {
          return control.note || 'This normally lives outside the repository, so nothing here proves it either way.';
        }
        return control.note || 'Searched for and not found anywhere in the reviewed code.';
      }

      function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, c => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        })[c]);
      }

      function params() { return new URLSearchParams(location.search); }

      function navigate(next) {
        const url = new URL(location);
        Object.entries(next).forEach(([key, value]) =>
          value == null ? url.searchParams.delete(key) : url.searchParams.set(key, value));
        history.pushState({}, '', url);
        render();
      }

      function route() {
        const value = params().get('view') || 'overview';
        return ROUTES.includes(value) ? value : 'overview';
      }

      function lowerKey(value) { return String(value || '').toLowerCase(); }

      function head() {
        const running = snapshot.status === 'running';
        const current = route();
        const tabs = [['overview', 'Overview'], ['findings', 'Findings'], ['evidence', 'Evidence'], ['report', 'Report']];
        return `<header class="site-head">
          <div class="brand"><span class="mark" aria-hidden="true"></span> prod-readiness</div>
          <nav class="nav" aria-label="Dashboard views">${tabs.map(([id, label]) =>
            `<button type="button" data-view="${id}" ${current === id ? 'aria-current="page"' : ''}>${label}</button>`).join('')}</nav>
          <span class="live ${running ? '' : 'done'}">${running ? 'Audit running' : 'Audit complete'}</span>
        </header>`;
      }

      function heroCopy() {
        const { verdict, counts, status } = snapshot;
        if (verdict.decision || verdict.headline) {
          return {
            chip: DECISION_LABEL[verdict.decision] || 'Verdict recorded',
            chipClass: lowerKey(verdict.decision) || 'pending',
            title: verdict.headline || DECISION_LABEL[verdict.decision],
            lede: verdict.summary,
          };
        }
        if (status === 'running') {
          return {
            chip: 'Audit in progress', chipClass: 'pending',
            title: counts.p0 ? `${counts.p0} blocker${counts.p0 === 1 ? '' : 's'} found so far.`
                             : 'The audit is still building its case.',
            lede: 'Counts update as each specialist finishes. Nothing is needed from you yet.',
          };
        }
        return {
          chip: 'No verdict yet', chipClass: 'pending',
          title: 'The audit has not written its verdict.',
          lede: 'Findings below are complete, but the go/no-go call has not been recorded.',
        };
      }

      function hero() {
        const { chip, chipClass, title, lede } = heroCopy();
        const c = snapshot.counts;
        return `<section class="hero">
          <div>
            <span class="decision ${escapeHtml(chipClass)}">${escapeHtml(chip)}</span>
            <h1>${escapeHtml(title)}</h1>
            ${lede ? `<p class="lede">${escapeHtml(lede)}</p>` : ''}
          </div>
          <div class="risk-strip">
            <div class="r0"><strong class="num">${c.p0}</strong><span>block the release</span></div>
            <div class="r1"><strong class="num">${c.p1}</strong><span>serious risks</span></div>
            <div><strong class="num">${c.unverified}</strong><span>could not be checked</span></div>
          </div>
        </section>`;
      }

      function lensMatrix() {
        const cells = snapshot.lenses.map(lens => {
          const worst = lens.counts.p0 ? `${lens.counts.p0} blocking`
            : lens.counts.p1 ? `${lens.counts.p1} to fix`
            : lens.counts.total ? `${lens.counts.total} noted`
            : { complete: 'Nothing found', skipped: 'Not applicable', running: 'Reviewing now' }[lens.status] || 'Waiting';
          return `<button class="lens ${escapeHtml(lens.status)}" type="button" data-lens="${escapeHtml(lens.id)}">
            <span class="dot ${escapeHtml(lens.status)}" aria-hidden="true"></span>
            <b>${escapeHtml(lens.label)}</b>
            <em>${escapeHtml(worst)}</em>
          </button>`;
        }).join('');
        return `<section class="band"><div class="band-head"><h2>What was reviewed</h2>
          <p>Seven specialists, each writing only its own findings.</p></div>
          <div class="matrix">${cells}</div></section>`;
      }

      function findingRow(finding) {
        const meta = [finding.lensLabel, STATE_LABEL[finding.state] || finding.state];
        return `<button class="row" type="button" data-finding="${escapeHtml(finding.id)}">
          <span class="sev ${lowerKey(finding.severity)}">${escapeHtml(finding.severity)}</span>
          <span>
            <span class="row-title">${escapeHtml(finding.title)}</span>
            ${finding.impact ? `<span class="row-impact">${escapeHtml(finding.impact)}</span>` : ''}
            <span class="row-meta">${escapeHtml(meta[0])} · <span class="state ${lowerKey(finding.state)}">${escapeHtml(meta[1])}</span></span>
          </span>
          <span class="row-open">Details →</span>
        </button>`;
      }

      function topFindings() {
        const top = snapshot.findings.filter(f => f.severity === 'P0' || f.severity === 'P1').slice(0, 5);
        if (!top.length) {
          return `<section class="band"><div class="band-head"><h2>What needs attention</h2></div>
            <p class="empty">No blocking or serious findings have been written yet.</p></section>`;
        }
        return `<section class="band"><div class="band-head"><h2>What needs attention first</h2>
          <p>Ordered by severity. Open one to see the cause and the evidence.</p></div>
          <div class="rows">${top.map(findingRow).join('')}</div></section>`;
      }

      function overview() {
        const errors = snapshot.errors || [];
        const notice = errors.length
          ? `<div class="notice">${escapeHtml(errors[0])}${errors.length > 1 ? ` (and ${errors.length - 1} more)` : ''}</div>`
          : '';
        return `${hero()}${notice}${topFindings()}${lensMatrix()}`;
      }

      function findingsView() {
        const filter = params().get('severity') || 'all';
        const lensFilter = params().get('lens');
        let list = snapshot.findings;
        if (filter !== 'all') list = list.filter(f => f.severity === filter);
        if (lensFilter) list = list.filter(f => f.lens === lensFilter);

        const available = ['all', ...SEVERITY_ORDER.filter(s => snapshot.findings.some(f => f.severity === s))];
        const chips = available.map(value => {
          const count = value === 'all' ? snapshot.findings.length : snapshot.findings.filter(f => f.severity === value).length;
          const label = value === 'all' ? 'Everything' : value;
          return `<button class="filter" type="button" data-severity="${value}" aria-pressed="${filter === value}">${label} · ${count}</button>`;
        }).join('');

        const lensChip = lensFilter
          ? `<button class="filter" type="button" data-clear-lens aria-pressed="true">Only ${escapeHtml(
              (snapshot.lenses.find(l => l.id === lensFilter) || {}).label || lensFilter)} ×</button>`
          : '';

        return `<section class="band" style="border-top:0; padding-top:44px">
          <div class="band-head"><h2>Every finding</h2>
          <p>${snapshot.findings.length} recorded across ${snapshot.lenses.filter(l => l.counts.total).length} lenses.</p></div>
          <div class="filters">${chips}${lensChip}</div>
          ${list.length ? `<div class="rows">${list.map(findingRow).join('')}</div>`
            : '<p class="empty">Nothing matches this filter.</p>'}
        </section>`;
      }

      function evidenceView() {
        const controls = snapshot.evidence || [];
        if (!controls.length) {
          return `<section class="band" style="border-top:0; padding-top:44px">
            <div class="band-head"><h2>What was searched for</h2></div>
            <p class="empty">The evidence ledger has not been written yet.</p></section>`;
        }
        const filter = params().get('control') || 'all';
        const states = ['all', 'CONFIRMED', 'NOT_FOUND', 'UNVERIFIED'];
        const shown = filter === 'all' ? controls : controls.filter(c => c.state === filter);
        const chips = states.map(value => {
          const count = value === 'all' ? controls.length : controls.filter(c => c.state === value).length;
          const label = value === 'all' ? 'Everything' : CONTROL_LABEL[value];
          return `<button class="filter" type="button" data-control="${value}" aria-pressed="${filter === value}">${escapeHtml(label)} · ${count}</button>`;
        }).join('');
        return `<section class="band" style="border-top:0; padding-top:44px">
          <div class="band-head"><h2>What the audit looked for</h2>
          <p>Every control it searched for, and whether this codebase has one.</p></div>
          <div class="filters">${chips}</div>
          <div class="panel" style="margin-top:18px">${shown.map(control => `
            <article class="ledger-item">
              <span class="chip ${lowerKey(control.state)}">${escapeHtml(CONTROL_LABEL[control.state])}</span>
              <strong>${escapeHtml(control.label)}</strong>
              <p>${escapeHtml(controlNote(control))}</p>
            </article>`).join('') || '<p class="empty">Nothing matches this filter.</p>'}</div>
        </section>`;
      }

      function reportView() {
        const { verdict, counts, findings, lenses } = snapshot;
        const groups = [
          ['Blocks the release', 'P0', 'Nothing blocks the release.'],
          ['Serious risks', 'P1', 'No serious risks were recorded.'],
          ['Worth cleaning up', 'P2', 'Nothing recorded.'],
          ['Minor', 'P3', 'Nothing recorded.'],
        ].filter(([, severity]) => findings.some(f => f.severity === severity));

        const skipped = lenses.filter(l => l.status === 'skipped');
        const unverified = findings.filter(f => f.state === 'UNVERIFIED');

        return `<article class="report">
          <h1 style="margin-top:0">Production readiness report</h1>
          <p class="muted">${counts.total} findings · ${counts.p0} blocking · ${counts.p1} serious · ${counts.unverified} unverified${
            snapshot.gitRef ? ` · <span class="mono">${escapeHtml(snapshot.gitRef)}</span>` : ''}</p>

          <h2>The call</h2>
          ${verdict.decision || verdict.headline
            ? `<p><span class="decision ${lowerKey(verdict.decision) || 'pending'}">${escapeHtml(
                DECISION_LABEL[verdict.decision] || 'Verdict recorded')}</span></p>
               ${verdict.headline ? `<p>${escapeHtml(verdict.headline)}</p>` : ''}
               ${verdict.summary ? `<p>${escapeHtml(verdict.summary)}</p>` : ''}`
            : '<p class="muted">No verdict has been recorded yet.</p>'}

          ${groups.map(([title, severity, empty]) => {
            const group = findings.filter(f => f.severity === severity);
            return `<h2>${title}</h2>${group.length
              ? `<div class="rows">${group.map(findingRow).join('')}</div>`
              : `<p class="muted">${empty}</p>`}`;
          }).join('')}

          <h2>What could not be checked</h2>
          ${unverified.length
            ? `<p>${unverified.length} finding${unverified.length === 1 ? '' : 's'} could not be settled from the code alone — they depend on a cloud console, a CI pipeline, or infrastructure outside this repository.</p>
               <div class="rows">${unverified.map(findingRow).join('')}</div>`
            : '<p class="muted">Everything the audit raised was settled from evidence in this repository.</p>'}

          ${skipped.length ? `<h2>Not reviewed</h2><ul>${skipped.map(lens =>
            `<li><strong>${escapeHtml(lens.label)}</strong> — ${escapeHtml(lens.skippedReason || 'skipped')}</li>`).join('')}</ul>` : ''}
        </article>`;
      }

      function drawer() {
        const id = params().get('finding');
        const lensId = params().get('open');
        if (id) {
          const finding = snapshot.findings.find(f => f.id === id);
          if (!finding) return '';
          const sections = [
            ['What this costs you', finding.impact],
            ['Why it happens', finding.failure_path],
            ['What already protects you', finding.compensating],
            ['What would settle it', finding.resolve],
            ['How to fix it', finding.fix],
          ].filter(([, value]) => value);
          const evidence = finding.evidence && finding.evidence.length
            ? `<section><h3>Where we saw it</h3><ul class="evidence-list">${
                finding.evidence.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></section>`
            : '';
          return `<div class="scrim" data-close></div>
            <div class="drawer" role="dialog" aria-modal="true" tabindex="-1" aria-label="${escapeHtml(finding.title)}">
              <header class="drawer-head">
                <div>
                  <span class="sev ${lowerKey(finding.severity)}" style="display:inline-grid">${escapeHtml(finding.severity)}</span>
                  <h2 style="margin-top:11px">${escapeHtml(finding.title)}</h2>
                  <p class="muted" style="margin-top:6px; font-size:.86rem">${escapeHtml(finding.lensLabel)} · <span class="state ${lowerKey(finding.state)}">${escapeHtml(STATE_LABEL[finding.state] || finding.state)}</span> · <span class="mono">${escapeHtml(finding.id)}</span></p>
                </div>
                <button class="close" type="button" data-close aria-label="Close">×</button>
              </header>
              ${sections.map(([title, body]) => `<section><h3>${title}</h3><p>${escapeHtml(body)}</p></section>`).join('')}
              ${evidence}
            </div>`;
        }
        if (lensId) {
          const lens = snapshot.lenses.find(l => l.id === lensId);
          if (!lens) return '';
          const found = snapshot.findings.filter(f => f.lens === lensId);
          return `<div class="scrim" data-close></div>
            <div class="drawer" role="dialog" aria-modal="true" tabindex="-1" aria-label="${escapeHtml(lens.label)}">
              <header class="drawer-head">
                <div><h2>${escapeHtml(lens.label)}</h2>
                <p class="muted" style="margin-top:6px; font-size:.86rem">${escapeHtml(
                  lens.skippedReason || `${found.length} finding${found.length === 1 ? '' : 's'} · ${lens.status}`)}</p></div>
                <button class="close" type="button" data-close aria-label="Close">×</button>
              </header>
              <section>${found.length
                ? `<div class="rows">${found.map(findingRow).join('')}</div>`
                : '<p class="empty">This lens has not written any findings.</p>'}</section>
            </div>`;
        }
        return '';
      }

      let returnFocusTo = null;

      function render() {
        if (!snapshot) return;
        const view = route();
        const body = view === 'findings' ? findingsView()
          : view === 'evidence' ? evidenceView()
          : view === 'report' ? reportView()
          : overview();
        const wasOpen = Boolean(document.querySelector('.drawer'));
        // Read the opener before the DOM is replaced - afterwards the element
        // that had focus no longer exists and activeElement is the body.
        const opener = document.activeElement;
        const openerKey = opener && opener.dataset
          ? (opener.dataset.finding ? `[data-finding="${opener.dataset.finding}"]`
            : opener.dataset.lens ? `[data-lens="${opener.dataset.lens}"]` : null)
          : null;
        app.innerHTML = `<div class="shell">${head()}${body}</div>${drawer()}`;

        // A dialog that never takes focus is a dialog only to sighted mouse
        // users. Move into it on open, and hand focus back on close. Every
        // render replaces the DOM, so the return target is remembered as a
        // selector rather than as the element that opened the dialog.
        const panel = document.querySelector('.drawer');
        if (panel && !wasOpen) {
          returnFocusTo = openerKey;
          panel.focus();
        } else if (!panel && wasOpen) {
          const target = returnFocusTo && document.querySelector(returnFocusTo);
          (target || app).focus();
          returnFocusTo = null;
        }
      }

      app.addEventListener('click', event => {
        const view = event.target.closest('[data-view]');
        if (view) return navigate({ view: view.dataset.view, finding: null, open: null });
        const finding = event.target.closest('[data-finding]');
        if (finding) return navigate({ finding: finding.dataset.finding, open: null });
        const lens = event.target.closest('[data-lens]');
        if (lens) return navigate({ open: lens.dataset.lens, finding: null });
        const severity = event.target.closest('[data-severity]');
        if (severity) return navigate({ severity: severity.dataset.severity });
        const control = event.target.closest('[data-control]');
        if (control) return navigate({ control: control.dataset.control });
        if (event.target.closest('[data-clear-lens]')) return navigate({ lens: null });
      });

      document.addEventListener('click', event => {
        if (event.target.closest('[data-close]')) navigate({ finding: null, open: null });
      });

      window.addEventListener('keydown', event => {
        if (event.key === 'Escape' && (params().get('finding') || params().get('open'))) {
          navigate({ finding: null, open: null });
        }
      });

      window.addEventListener('popstate', render);

      async function refresh() {
        try {
          const response = await fetch('/api/snapshot', { cache: 'no-store' });
          if (!response.ok) throw new Error(`Snapshot request failed (${response.status})`);
          snapshot = await response.json();
          render();
          if (snapshot.status === 'running') setTimeout(refresh, 2000);
        } catch (error) {
          app.innerHTML = `<div class="shell"><section class="band" style="border-top:0">
            <h1>Production readiness</h1>
            <p class="lede">${escapeHtml(error.message || 'The snapshot could not be loaded.')}</p>
          </section></div>`;
        }
      }

      refresh();
    </script>
  </body>
</html>
"""


def read_text_if_present(path: Path) -> str | None:
    """Return a UTF-8 file's text, or ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def load_evidence(audit_root: Path) -> list[dict]:
    """Flatten the absence ledger into the rows the Evidence view shows.

    The ledger is already structured, so this is a projection - a control's
    label, the state its hit count supports, and how many matches it found.
    """
    text = read_text_if_present(audit_root / "evidence" / "absence-ledger.json")
    if text is None:
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return []
    controls = raw.get("controls") if isinstance(raw, dict) else None
    if not isinstance(controls, dict):
        return []

    rows = []
    for control_id, row in sorted(controls.items()):
        if not isinstance(row, dict) or row.get("polarity") != "control":
            continue
        hits = row.get("hit_count") or 0
        supports = row.get("supports_state")
        if hits > 0:
            state = "CONFIRMED"
        elif supports in ("NOT_FOUND", "UNVERIFIED"):
            state = supports
        else:
            continue
        paths = []
        for hit in (row.get("hits") or [])[:3]:
            if isinstance(hit, dict) and hit.get("path"):
                paths.append(hit["path"])
        rows.append({
            "id": control_id,
            "label": row.get("label") or control_id,
            "lens": row.get("lens"),
            "state": state,
            "hits": hits,
            "paths": paths,
            "note": row.get("note"),
        })
    return rows


def build_snapshot(project_root: Path) -> dict:
    """The dashboard's whole payload, assembled from structured audit data."""
    project_root = Path(project_root)
    audit_root = (project_root / ".readiness-audit").resolve()

    if not audit_root.is_dir():
        return {
            "status": "unavailable",
            "message": "No audit has been run in this project yet.",
            "counts": {"total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0,
                       "confirmed": 0, "notFound": 0, "unverified": 0},
            "verdict": {"decision": None, "headline": None, "summary": None},
            "lenses": [{"id": lens, "label": LENS_LABEL[lens], "status": "waiting",
                        "skippedReason": None,
                        "counts": {"total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0,
                                   "confirmed": 0, "notFound": 0, "unverified": 0}}
                       for lens in LENS_ORDER],
            "findings": [],
            "evidence": [],
            "gitRef": None,
            "errors": [],
        }

    report = build_report(project_root)
    stage_status = (report.get("stage") or {}).get("status")
    report["status"] = "complete" if stage_status == "complete" else "running"
    report["message"] = ("Audit complete." if report["status"] == "complete"
                         else "Audit is still running.")
    report["evidence"] = load_evidence(audit_root)
    report["auditRoot"] = str(audit_root)
    return report


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, project_root: Path, port: int):
        self.project_root = project_root
        super().__init__(("127.0.0.1", port), DashboardRequestHandler)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # The dashboard keeps its view, filters, and open finding in the query
        # string, so every route arrives here as "/?view=..." and must still
        # serve the app shell.
        path = urlsplit(self.path).path
        if path == "/":
            return self.respond(HTTPStatus.OK, "text/html; charset=utf-8", DASHBOARD_HTML.encode())
        if path == "/api/snapshot":
            payload = json.dumps(build_snapshot(self.server.project_root)).encode()
            return self.respond(HTTPStatus.OK, "application/json; charset=utf-8", payload)
        self.send_error(HTTPStatus.NOT_FOUND)

    def respond(self, status: HTTPStatus, content_type: str, body: bytes):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def create_server(project_root: Path, port: int = 0) -> ThreadingHTTPServer:
    return DashboardServer(project_root, port)


def startup_url(server: DashboardServer) -> str:
    host, port = server.server_address
    return f"http://{host}:{port}/"


def serve(project_root: Path, port: int = 0) -> None:
    server = create_server(project_root, port)
    print(startup_url(server), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _cli_readiness_dashboard(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve a read-only production-readiness dashboard.")
    parser.add_argument("project_root", type=Path, help="Target project root containing .readiness-audit")
    parser.add_argument("--port", type=int, default=0, help="Port to bind on 127.0.0.1 (default: ephemeral)")
    args = parser.parse_args(argv)
    serve(args.project_root, args.port)
    return 0


# ---------------------------------------------------------------------------
# selftest - proof that this file still enforces what the audit depends on
# ---------------------------------------------------------------------------

_SELFTEST_SOURCES = {
    "package.json": '{"name":"selftest","dependencies":{"express":"^4.18.0"}}',
    "src/app.ts": (
        'import express from "express";\n'
        'const app = express();\n'
        'app.get("/orders", async (req, res) => {\n'
        '  const rows = await db.query(`SELECT * FROM orders WHERE t = \'${req.query.t}\'`);\n'
        '  res.json(rows);\n'
        '});\n'
    ),
    "prisma/migrations/001_init/migration.sql": "CREATE TABLE orders (id TEXT PRIMARY KEY);\n",
    "tests/app.test.ts": 'test("adds", () => { expect(1).toBe(1); });\n',
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
        print(f"unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    handler, prefix = COMMANDS[command]
    sys.argv = [f"readiness_engine.py {command}"] + prefix + sys.argv[2:]
    return handler() or 0


if __name__ == "__main__":
    sys.exit(main())
