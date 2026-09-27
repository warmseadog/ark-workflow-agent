# Resumable video workflow

The application now exposes a versioned workflow contract under
`/api/workflow`. The existing `/api/jobs` endpoints remain available for
backwards compatibility.

## Lifecycle

```text
DRAFT -> SOURCE_INGESTING -> SOURCE_REVIEW -> SOURCE_READY
      -> REDACTION_EDITING -> REDACTION_REVIEW -> REDACTION_APPROVED
      -> FACE_MATERIAL_REVIEW / GARMENT_MATERIAL_REVIEW
      -> READY_FOR_EXECUTION
```

The face and garment branches are independent after redaction approval. A
snapshot freezes the selected source, redaction revision, and approved
reference assets so a later execution can be reproduced.

## API contract

The first implementation keeps the API intentionally small:

* `POST /api/workflow/projects` creates a project.
* `GET /api/workflow/projects/{project_id}` returns the current stage and
  persisted assets.
* `POST /api/workflow/projects/{project_id}/source` records an uploaded or URL
  source and its metadata.
* `POST /api/workflow/projects/{project_id}/tasks` enqueues an idempotent
  stage task.
* `POST /api/workflow/projects/{project_id}/stage` performs a validated stage
  transition.
* `POST /api/workflow/projects/{project_id}/redaction/review` records the
  approval or rejection of a redaction revision.
* `POST /api/workflow/projects/{project_id}/materials` records a versioned
  `face` or `garment` reference asset.
* `POST /api/workflow/projects/{project_id}/snapshot` freezes the current
  inputs for a future execution provider.

The new workflow store is separate from the legacy discovery and one-shot job
records. This keeps the migration reversible. The next integration step is to
point both stores at the same configured SQLite/PostgreSQL URL and add a
worker adapter around the existing FFmpeg and deface pipeline.

## Reliability rules

Stage tasks carry an idempotency key, attempt count, and explicit state. Raw
video and derived artifacts should be written through the atomic publishing
helper and never overwrite the source. Review decisions and state changes are
stored as events so a refresh or worker restart can recover the current stage.
## Entry points

The default / and /v1 pages now open the manual workflow. The previous studio
page remains available at /studio. Local videos use the
POST /api/workflow/projects/{project_id}/source-upload endpoint; URL sources
use POST /api/workflow/projects/{project_id}/source. Uploads are streamed to
the workflow storage directory, hashed, and registered before the stage can
advance.

POST /api/workflow/projects/{project_id}/redaction/render starts the durable redaction worker and records a verified output hash.
