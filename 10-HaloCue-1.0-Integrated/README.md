# HaloCue 1.0 Integrated

This directory is the composition root for HaloCue 1.0. It does not copy or
merge the writing and AA production domain implementations.

- `/` serves the writing workbench from `09-HaloCue-1.0-Writing`.
- `/api/v1/*` serves the writing API.
- `/production/` serves the AA production workbench from `08-HaloCue-1.0`.
- `/production/api/v1/*` serves the AA production API.

The browser receives one origin. The writing service still hands off only an
immutable `ScriptRelease`; production owns its own frozen source copy and
`ProductionRun`.

The writing document is the single application page. Selecting AA production
mounts the production workbench into that document without navigating to a
second page: the full-width top bar and primary navigation stay mounted, while
the contextual second column and central task surface switch to production.
The production UI runs in an isolated ShadowRoot and continues to call its own
`/production/api/v1` domain. `/production/` remains a compatibility/debug URL,
not the normal product navigation path. Mobile uses the same single-page route
and preserves the current Work, ScriptRelease, and ProductionRun.

## Start

```powershell
$env:PYTHONPATH='10-HaloCue-1.0-Integrated/src'
python -m halocue_integrated.server --port 8910
```

The default runtime reuses the existing durable data directories in `08` and
`09`. Use `--writing-data-dir` and `--production-data-dir` for isolated QA.

`IntegratedRuntime.start()` starts both private domain servers and the public
gateway. `serve_forever()` is the CLI foreground entry point; `close()` is
idempotent and safely handles a runtime that was never started. The writing
service uses the private production address for the handoff, but diagnostics
expose only the public `/production` route.

The writing database records schema versions in `PRAGMA user_version` and
`writing_schema_migrations`. A newer or corrupt database stops startup with a
stable domain error instead of silently rebuilding the workspace.

## Current model boundary

The writing Provider is still the visibly labelled Fake Provider. This
integration does not claim that real model calls, token accounting, automatic
canon maintenance, or a distributable `ba-writing` WritingPack are complete.

HTTP compatibility remains explicit: both domain handlers and the Gateway
return the legacy error wrapper by default. A client must send
`Accept: application/vnd.halocue.api-error+json; version=1.0` to receive the
formal `ApiError/1.0` object.
