# standalone/

The seven-lens production readiness audit, packaged so it runs anywhere - not
only inside Claude Code.

| File | What it is |
| --- | --- |
| `DPM-READINESS-AUDITOR.md` | **The FDA / OGD / ORO / DPM edition.** Audits AI tools, prompts, agents, scripts, dashboards, and vendor products - run by the person who owns the work, not by an engineer. 64 controls, 7 lenses, 3 stakes tiers. Output is a one-page director memo over a working annex. |
| `EXAMPLE-AUDIT.md` | A completed audit produced with that method, on an invented tool. Hand this to a colleague before the method itself. |
| `AUDITOR-COMPACT.md` | **The prompt.** The whole audit distilled to ~130 lines - every rule, no engine. Paste it into any model and go. **Start here.** |
| `PRODUCTION-READINESS-AUDITOR.md` | The long form. Same rules, plus the full regex catalogue, the seven mandates verbatim, and the engine inline. Reference, not a paste. |
| `readiness_engine.py` | The deterministic engine. One file, Python 3.9+, standard library only. |
| `AUDITOR.template.md` | The source the document is generated from. Edit this, never the generated file. |

## Use it

```bash
python3 readiness_engine.py selftest     # prove the engine is intact
python3 readiness_engine.py init  /path/to/project
python3 readiness_engine.py scan  /path/to/project
python3 readiness_engine.py probe /path/to/project
# ... lenses write findings/<lens>.json ...
python3 readiness_engine.py validate /path/to/project
python3 readiness_engine.py render   /path/to/project
python3 readiness_engine.py assemble /path/to/project
python3 readiness_engine.py serve    /path/to/project    # optional dashboard
```

Hand `PRODUCTION-READINESS-AUDITOR.md` to any coding agent and point it at a
project. Appendix C has a launch card for each host.

## These files are generated

`readiness_engine.py` and `PRODUCTION-READINESS-AUDITOR.md` are built from
`scripts/`, `agents/`, and `AUDITOR.template.md`. Do not hand-edit them - the
next build overwrites the change, and `tests/` fails if they drift.

```bash
python3 scripts/build_standalone.py            # rebuild
python3 scripts/build_standalone.py --check    # fail if the tracked files are stale
python3 -m pytest tests/                       # parity, gate, and document tests
```

The parity suite runs the plugin scripts and the merged engine over the same
fixture and diffs every artefact. They agree byte for byte, which is the only
reason a second implementation is safe to ship.
