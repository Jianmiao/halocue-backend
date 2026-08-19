# B3 Production Adapter Design

## Goal

Create a stable production-adapter boundary that lets HaloCue translate the
standard `PerformanceDraft` and frozen production inputs to AzureArchive (AA)
or an independent StoryForge renderer without moving ownership of Work,
Revision, or `ScriptRelease` into either engine.

## Current Context

The production service currently wires `Legacy093Adapter` directly into the
large application service. `AdapterCapabilities/1.0` and `BuildBundle/1.0`
examples and validators already exist, but there is no formal Python adapter
protocol and no StoryForge adapter boundary. B2 provides the required
`ScriptRelease/1.1`, `ProductionRequest/1.1`, `AssetManifest/1.0`, ArtifactStore,
and persistent runtime primitives.

The AA implementation remains local-only. This work must not copy or publish AA
source code, databases, assets, private serialization, or physical paths.

## Decisions

### Unified adapter protocol

Add a focused protocol in `src/halocue_production/adapters/base.py` with these
operations:

```text
capabilities()
preflight(request)
create_performance_draft(request, scope)
update_performance_draft(draft_ref, patch)
validate(request, draft_ref)
compile(request, draft_ref)
render(request, draft_ref, options)
cancel(attempt_ref)
```

The protocol accepts formal domain payloads or stable references only. `request`
must reference a frozen `ScriptRelease`, `AssetManifest`, and production policy.
`draft_ref` must identify a reviewed, content-hashed `PerformanceDraft`.
Compilation and rendering return only ArtifactStore-registered immutable output
references, normally a validated `BuildBundle/1.0`. Installation is not part of
this protocol and remains a separate explicit user action.

An adapter never owns or mutates Work, Revision, ScriptRelease, or the source
content. It may translate a local copy into an engine-specific representation,
but that representation is not a HaloCue domain fact.

### AA compatibility adapter

Add `src/halocue_production/adapters/aa/` with a thin wrapper and translation
boundary around the existing `Legacy093Adapter`. The wrapper will expose
standard capability names and normalize preflight, validation, compilation,
cancellation, and unavailable-environment errors. Existing AA behavior stays
behind the boundary; no AA private format becomes a HaloCue contract.

The first B3 implementation may use a local test double for the compiler when
the real AA workspace or non-distributable fixtures are unavailable. Real AA
installation remains an integration/manual check and is never required by the
unit test suite.

### StoryForge adapter

Add `src/halocue_production/adapters/storyforge/` as an independent adapter.
It will expose capability discovery, deterministic preflight, preview/render
hooks, and video-export hooks against the standard `PerformanceDraft`. It will
not own Work, Revision, ScriptRelease, or the production request, and it will
not assume AA's three-slot character model or AA serialization.

The first implementation provides a deterministic local renderer boundary and
explicit unavailable states for operations that need a future engine. It must
still validate input and ArtifactStore references before returning output.

### Capability matrix

The public capability payload remains `AdapterCapabilities/1.0` and must list
only operations that are actually available for the selected adapter and
target. The initial matrix is:

| Capability | AA | StoryForge |
|---|---|---|
| `preflight` | available when local adapter is loaded | available |
| `create_performance_draft` | available through translation | available through standard draft boundary |
| `update_performance_draft` | limited by translation | available |
| `validate` | AA format and resource checks | StoryForge render/resource checks |
| `compile_aap` | available when compiler/environment exists | unavailable |
| `render_preview` | unavailable as an AA side effect | available |
| `export_video` | unavailable | available through the local renderer boundary |
| `cancel` | cooperative/child-process cancellation | cooperative cancellation |
| `install_aap` | separate explicit operation | unavailable |

Missing capabilities produce a stable structured unavailable result and never a
synthetic success or a fake artifact.

## Data flow

```text
ScriptRelease/1.1 + AssetManifest/1.0
                 |
                 v
        ProductionAdapter.preflight
                 |
                 v
 PerformanceDraft review and immutable hash
          /                       \
         v                         v
 AA validate -> compile_aap     StoryForge validate -> preview/video
         |                         |
         +------ validated BuildBundle/1.0 ------+
```

Every external operation is associated with the existing persistent runtime
`ProductionRun`, `WorkItem`, and `JobAttempt`. An adapter may emit progress, but
the runtime owns state transitions and only accepts a verified ArtifactStore
reference after a successful operation. A cancelled or abandoned attempt
cannot publish a late result.

## Error and security behavior

Adapter errors are normalized to stable production error codes first. The
existing HTTP error wrapper remains compatible; the later API composition work
can map it to the formal `ApiError/1.0` envelope without changing adapter
semantics.

The adapters must reject unsupported contract versions, missing or mismatched
hashes, AssetManifest references outside the frozen whitelist, path traversal,
and unregistered output files. Logs and public results must not include API
keys, complete model requests, private draft text, or absolute local paths.

## Testing strategy

Contract tests will cover:

1. AA and StoryForge capability payloads validate as `AdapterCapabilities/1.0`.
2. A missing capability returns a stable unavailable error and no artifact.
3. Fixed formal input produces a valid, content-addressed BuildBundle or an
   explicit unavailable result; running it twice is semantically equivalent.
4. An output is accepted only after ArtifactStore registration and hash checks.
5. Cancellation prevents a late adapter result from becoming a formal artifact.
6. StoryForge never receives or mutates writing-domain ownership objects.
7. AA compatibility tests use small authorized fixtures or doubles and do not
   require a real AA installation.

The existing Production, Writing, and Integrated suites remain mandatory. Any
real AA installation or non-distributable engine validation is marked manual or
integration and kept outside the default test command.

## Explicit non-goals

- Do not split the backend into services or add a message queue.
- Do not move the existing AA source tree, database, or asset library into this
  repository.
- Do not make installation an implicit side effect of compile or render.
- Do not make StoryForge the owner or source of truth for HaloCue works.
- Do not redesign the existing UI or rewrite the entire production service in
  this phase.
