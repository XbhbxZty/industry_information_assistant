# Database migration authority

Alembic revisions in this directory are the application-schema authority.

Phase 3.4D2a1 only establishes the empty-database baseline.  Existing databases
must not be stamped yet: the legacy fingerprint/adoption gate is delivered in
3.4D2a2, and runtime `create_all()` is removed only in 3.4D2a3.

Run from `backend/`:

```text
python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

The seven restaurant/stock/legal/transport demo tables from the old Docker init
SQL are outside this managed application schema.  Alembic never drops them.
