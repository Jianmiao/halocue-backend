# HaloCue 1.0 Integrated

This directory is the composition root for HaloCue 1.0. It does not copy or
merge the writing and AA production domain implementations.

- `/` serves the writing workbench from `09-HaloCue-1.0-Writing`.
- `/api/v1/*` serves the writing API.
- `/production/` serves the AA production workbench from `08-HaloCue-1.0`.
- `/production/api/v1/*` serves the AA production API.

The browser receives one origin. The writing service hands off an immutable
`ScriptRelease`. In the integrated composition root, the handoff publishes a
formal `ScriptRelease/1.1`, an initial `AssetManifest/1.0`, and a
`ProductionRequest/1.1` into the shared local ArtifactStore before creating the
production run. A standalone writing service without that injected store keeps
the `WritingHandoff/1.0` compatibility path. Production owns its own frozen
source copy and `ProductionRun`.

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

The writing Provider is the visibly labelled Fake Provider in the repository
fixtures. This is an intentional deterministic local boundary for the 1.0
backend loop; real provider calls, token accounting, automatic canon maintenance,
and a distributable `ba-writing` WritingPack are separate provider/product
extensions and are not required for the production handoff or BuildBundle path.

HTTP compatibility remains explicit: both domain handlers and the Gateway
return the legacy error wrapper by default. A client must send
`Accept: application/vnd.halocue.api-error+json; version=1.0` to receive the
formal `ApiError/1.0` object.
