# Database migration authority

Alembic revisions in this directory are the sole authority for the application
schema. Runtime startup and seed scripts never call `create_all()` and never
run Alembic implicitly: they only verify that `public.alembic_version` exactly
matches this checkout's dynamically resolved Alembic head.

Run from `backend/`:

```text
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

Run `upgrade head` explicitly before every backend startup. The Docker image and
`start-app.ps1` do this before launching Uvicorn; a direct local Uvicorn command
must do the same. Do not use `alembic stamp` to make a legacy database appear
current. A legacy database needs the reviewed adoption procedure; an unversioned
or out-of-date database will fail the read-only startup guard with the upgrade
command instead of being modified automatically.

The old `docker/init-db/01-init.sql` remains in the repository only for manual
legacy investigation. Root `docker-compose.yml` no longer mounts it, preserving
existing PostgreSQL volumes while preventing automatic legacy business DDL. The
seven restaurant/stock/legal/transport demo tables, if present in an old volume,
remain outside this managed application schema; Alembic never drops them.

## Local legacy adoption (development/maintenance only)

This is a local command, not a web API or a signed approval service. Host access
and the configured PostgreSQL maintenance credentials are the authority boundary.
It supports only the reviewed PostgreSQL 15 `base-full` legacy schema. Docker
legacy variants and drift are rejected, never automatically repaired. Stop all
external writers and independently check the target and backup before applying.

Both commands load `backend/.env`; existing process environment variables take
precedence. Inspect the selected `DATABASE_URL` target before any apply:

```text
python app/scripts/adopt_legacy_schema.py inspect
```

Inspect performs no writes. Check `preflight.target_identity` and require
`preflight.status` to be `exact_adoptable`. Save `policy_template` and
`approval_template` as separate JSON files. Keep the reviewed policy in local
maintenance configuration (not an API request or source control), and set
`LEGACY_ADOPTION_POLICY_FILE` to its absolute path. Fill the blank approval fields:
operator reference, backup reference, maintenance-window reference, and the exact
confirmation phrase `I CONFIRM LEGACY SCHEMA ADOPTION`. These are attestations,
not proof that a backup is recoverable or that other writers have stopped.

The generated approval lasts 15 minutes; inspect again if expired. Do not alter
the policy ID/content or digest fields after review. Then run:

```text
python app/scripts/adopt_legacy_schema.py apply --approval /absolute/path/approval.json
python -m alembic upgrade head
```

Apply compares the policy, target and fresh preflight inside one locked writer
transaction, stamps only `20260831_0001`, and verifies before commit. JSON output
includes the approval ID, operator reference and outcome; save it with the local
maintenance record if needed. Exit 0 means adopted/already managed; 2 means
rejected; 3 means configuration/connection failure; 4 means outcome unknown.
For outcome unknown, **do not blindly retry**: run inspect read-only and check
whether the version is already managed. No persistent approval ledger or
production deployment automation is provided by this development tool.

For a fresh development environment, use a separately named empty database and
`upgrade head`; never remove/reinitialize an existing PostgreSQL volume.

Build the backend image from the repository root with
`docker build -f backend/app/Dockerfile backend`. The backend build context
contains migrations; `.dockerignore` excludes local environment secrets. Supply
configuration at container runtime instead of baking `.env` into the image.

To add those optional Text2SQL demonstration tables to a database that is
already at Alembic head, run this manual, one-time command from `backend/`:

```text
python app/scripts/seed_demo_data.py
```

It first uses the same read-only head guard, then creates and inserts all seven
demo tables in one transaction. It refuses if any demo table already exists;
it never replaces, appends to, or runs automatically for existing data.
