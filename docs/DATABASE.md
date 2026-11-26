# FinSight database (PostgreSQL)

In Docker, FinSight keeps all durable state in one PostgreSQL 16 database (`finsight`, image `pgvector/pgvector:pg16`):
- the LangGraph checkpoints (every step of every research thread)
- the long-term memory store and its pgvector index
- FinSight's thread registry and idempotency keys

The FAISS research collection is not in Postgres. It lives in the `faiss_index` Docker volume.

Local runs without Docker still use SQLite files in `backend/data/` (see the README).

## 1. Roles: who connects as what

| Role | Used by | Can |
|---|---|---|
| `finsight_admin` (`POSTGRES_USER`) | `migrate` service, backups, restores | Everything (database owner, superuser). Never given to the API. |
| `finsight_app` (`APP_DB_USER`) | `api` and `cleanup` services | `SELECT/INSERT/UPDATE/DELETE` on rows. **Cannot** create, alter or drop tables (`permission denied for schema public`, `must be owner of table`). Max 60 connections. |
| `finsight_readonly` (`READONLY_DB_USER`) | You, in DBeaver / psql | `SELECT` only. Every session is read-only (`cannot execute INSERT in a read-only transaction`) with a 60 s statement timeout. Max 10 connections. |

How the roles are set up:
- `docker/db/init/01-roles.sh` creates them the first time the `pg_data` volume is initialised. It also creates the `finsight_test` database and the `vector` extension.
- Passwords come from `.env` (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `READONLY_DB_PASSWORD`).
- The app and read-only users get access to future tables through `ALTER DEFAULT PRIVILEGES`, so every table a migration adds is covered automatically.

**Changing a password:** edit `.env`, then run `bash scripts/db-roles.sh` (it re-runs the same idempotent script). Then run `docker compose up -d --force-recreate api cleanup`.

## 2. Connect with DBeaver (read-only)

The dev stack publishes Postgres on **`127.0.0.1:5433`**, on your machine only (`docker-compose.override.yml`). The production stack doesn't publish it; see the SSH tunnel steps below.

1. Start the stack: `docker compose up -d`. `docker compose ps` should show `db` as `healthy`.
2. Install [DBeaver Community](https://dbeaver.io/download/) if you don't have it.
3. **Database → New Database Connection → PostgreSQL → Next.**
4. On the **Main** tab:

   | Field | Value |
   |---|---|
   | Connect by | Host |
   | Host | `127.0.0.1` |
   | Port | `5433` |
   | Database | `finsight` |
   | Authentication | Database Native |
   | Username | `finsight_readonly` |
   | Password | the `READONLY_DB_PASSWORD` value from your `.env` |
   | Show all databases | optional (tick it to also see `finsight_test`) |

5. **Driver:** the first time, DBeaver says the PostgreSQL driver files are missing. Click **Download** (it fetches the PostgreSQL JDBC driver, `org.postgresql:postgresql`). Behind a proxy or offline: download the JAR from <https://jdbc.postgresql.org/download/>, then **Edit Driver Settings → Libraries → Add File** and select it.
6. Click **Test Connection…**. It should say *Connected*. Then click **Finish**.
7. Optional, on the connection's **General** tab: set the type to *Production* and tick **Read-only connection**. The role is read-only anyway; this makes DBeaver say so too.
8. Expand `finsight → Schemas → public → Tables`. Open an **SQL Editor** (Ctrl+]) and paste the queries from section 4.

**Production (no published port): an SSH tunnel.**
1. On the server, publish Postgres on the host's loopback only, for example by adding `ports: ["127.0.0.1:5433:5432"]` to `db` in a private override file. Or use the container's IP.
2. In DBeaver, on the **SSH** tab: tick *Use SSH Tunnel* and enter the server's SSH host, user and key.
3. On the **Main** tab: Host `127.0.0.1`, Port `5433`, the read-only user as above.

**Quick look without DBeaver:**

```bash
docker compose exec db psql -U finsight_admin -d finsight      # owner (careful)
docker compose exec db sh -c 'PGPASSWORD="$READONLY_DB_PASSWORD" psql -h 127.0.0.1 -U finsight_readonly -d finsight'
```

## 3. Tables

### FinSight's own tables (Alembic: `backend/migrations/`)

**`threads`**: the thread registry. It records who owns which LangGraph thread, plus its title and run status.

| Column | Type | Notes |
|---|---|---|
| `thread_id` | varchar(64) PK | The LangGraph `thread_id` (uuid hex). |
| `user_id` | varchar(64) | From the `X-User-Id` header. Every API call filters on it (another user's thread is a 404). |
| `title` | varchar(200) | First question of the thread. |
| `created_at`, `updated_at` | timestamptz | `updated_at` changes on every status change. |
| `status` | varchar(16) | `idle`, `running`, `interrupted` (paused at a gate), `completed` or `failed`. Runs start with an atomic `UPDATE … WHERE status IN (…)`, so two requests can't run one thread at once. |
| `error` | varchar(500) | Why the last run failed (`/threads/{id}/retry` resumes it). |

Indexes:
- `ix_threads_user_updated (user_id, updated_at)`: a user's threads, newest first
- `ix_threads_updated_at (updated_at)`: TTL cleanup

**`idempotency_keys`**: responses stored for `Idempotency-Key` retries of JSON `POST` endpoints.

| Column | Type | Notes |
|---|---|---|
| `user_id`, `key` | varchar PK | Keys are scoped per user. |
| `method`, `path`, `request_hash` | varchar | A retry must match: a different body is rejected with 422. |
| `state` | varchar(16) | `in_progress` or `done`. |
| `status_code`, `response` | int, json | The stored response, returned to retries (`Idempotent-Replayed: true`). |
| `created_at`, `updated_at` | timestamptz | Deleted after `IDEMPOTENCY_TTL_HOURS` (24) by the cleanup job. |

Indexes: `ix_idempotency_keys_user_id (user_id)` and `ix_idempotency_keys_updated_at (updated_at)`.

**`alembic_version`**: the applied migration (`0001_initial`). The API refuses to start if this isn't the latest revision.

### LangGraph checkpointer (`AsyncPostgresSaver.setup()`)

Each graph run saves a checkpoint after every step (`durability="sync"`). That is what makes threads survive restarts and power resume, retry, replay and fork.

| Table | What it holds |
|---|---|
| `checkpoints` | One row per step. Key `(thread_id, checkpoint_ns, checkpoint_id)`; `checkpoint_id` is time-ordered, so the largest is the newest. `parent_checkpoint_id` links the steps into a tree: replay and fork create branches. `checkpoint` (jsonb) has `ts`, `channel_versions`, and `channel_values` for small values (question, draft, answer, flags) stored inline. `metadata` (jsonb) has `source` (`input`, `loop` or `update`), `step` and the writes. FinSight's threads use `checkpoint_ns = ''`. |
| `checkpoint_blobs` | Larger channel values (messages, documents, sources, web results, memories), stored once per version and serialized with msgpack (`type`, `blob`). Key `(thread_id, checkpoint_ns, channel, version)`; a checkpoint points to them through `channel_versions`. |
| `checkpoint_writes` | Pending writes of a step: outputs of tasks in a step that didn't finish, and **`__interrupt__`** rows when a gate pauses the graph. Key `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)`. They let a crashed run resume without redoing finished tasks. |
| `checkpoint_migrations` | LangGraph's own schema version for these tables (`v`). |

### LangGraph long-term memory store (`AsyncPostgresStore.setup()`)

| Table | What it holds |
|---|---|
| `store` | One row per memory. `prefix` is the namespace joined with dots: FinSight uses `(user_id, "memories")`, so `alice.memories`. `key` is `pref:<hash>`, `company:<slug>` or `report:<thread>:<turn>`. `value` (jsonb) has `kind`, `text`, `thread_id` and `updated_at`. Also `created_at`, `updated_at`, and `expires_at`/`ttl_minutes` (unused: memories don't expire). |
| `store_vectors` | The embedding of each memory's `text` field (`vector(EMBED_DIMS)`: 384 for bge-small, 768 for nomic), with an **HNSW** cosine index (`store_vectors_embedding_idx`). Used for "recall memories relevant to this question" at the start of a thread. |
| `store_migrations`, `vector_migrations` | LangGraph's schema versions for the store and its vector index. |

All LangGraph tables are created and upgraded by the `migrate` service. Never edit them by hand: the API reads them through LangGraph only.

## 4. Inspection queries

Run them as `finsight_readonly`. They were tested against a real thread in `finsight_test`.

**List threads (newest first)**

```sql
SELECT thread_id, user_id, title, status, error, updated_at
FROM threads
ORDER BY updated_at DESC
LIMIT 50;
```

**One user's threads**

```sql
SELECT thread_id, title, status, created_at, updated_at
FROM threads
WHERE user_id = 'local'
ORDER BY updated_at DESC;
```

**Checkpoints per thread**

```sql
SELECT c.thread_id,
       t.user_id,
       count(*)                                AS checkpoints,
       min((c.checkpoint->>'ts')::timestamptz) AS first_step,
       max((c.checkpoint->>'ts')::timestamptz) AS last_step
FROM checkpoints c
LEFT JOIN threads t ON t.thread_id = c.thread_id
WHERE c.checkpoint_ns = ''
GROUP BY c.thread_id, t.user_id
ORDER BY last_step DESC;
```

**Latest checkpoint of every thread**

```sql
SELECT DISTINCT ON (thread_id)
       thread_id,
       checkpoint_id,
       (checkpoint->>'ts')::timestamptz AS at,
       metadata->>'source'              AS source,
       (metadata->>'step')::int         AS step
FROM checkpoints
WHERE checkpoint_ns = ''
ORDER BY thread_id, checkpoint_id DESC;
```

**Latest state of one thread.** These are the inline values: question, draft, answer, review, flags, turn.

```sql
SELECT checkpoint_id, jsonb_pretty(checkpoint->'channel_values') AS state
FROM checkpoints
WHERE thread_id = '<thread_id>' AND checkpoint_ns = ''
ORDER BY checkpoint_id DESC
LIMIT 1;
```

**…and which larger values it has in `checkpoint_blobs`.** These are msgpack, so they aren't readable in SQL. Use `GET /threads/{id}` or `GET /threads/{id}/history?full=true` for their contents.

```sql
WITH head AS (
  SELECT thread_id, checkpoint
  FROM checkpoints
  WHERE thread_id = '<thread_id>' AND checkpoint_ns = ''
  ORDER BY checkpoint_id DESC
  LIMIT 1
)
SELECT v.key AS channel, b.type, length(b.blob) AS bytes
FROM head
CROSS JOIN LATERAL jsonb_each_text(head.checkpoint->'channel_versions') AS v(key, version)
JOIN checkpoint_blobs b
  ON b.thread_id = head.thread_id AND b.checkpoint_ns = ''
 AND b.channel = v.key AND b.version = v.version
ORDER BY v.key;
```

**Step-by-step history of one thread** (what the dashboard's checkpoint panel shows)

```sql
SELECT checkpoint_id, parent_checkpoint_id,
       (metadata->>'step')::int AS step, metadata->>'source' AS source,
       (checkpoint->>'ts')::timestamptz AS at
FROM checkpoints
WHERE thread_id = '<thread_id>' AND checkpoint_ns = ''
ORDER BY checkpoint_id DESC;
```

**Threads paused at a gate right now** (an interrupt on their newest checkpoint)

```sql
WITH head AS (
  SELECT DISTINCT ON (thread_id) thread_id, checkpoint_id
  FROM checkpoints
  WHERE checkpoint_ns = ''
  ORDER BY thread_id, checkpoint_id DESC
)
SELECT h.thread_id, t.user_id, t.title, t.status, h.checkpoint_id
FROM head h
JOIN checkpoint_writes w
  ON w.thread_id = h.thread_id AND w.checkpoint_ns = '' AND w.checkpoint_id = h.checkpoint_id
 AND w.channel = '__interrupt__'
LEFT JOIN threads t ON t.thread_id = h.thread_id;
```

**Long-term memories by user**

```sql
SELECT split_part(prefix, '.', 1) AS user_id,
       key,
       value->>'kind'             AS kind,
       value->>'text'             AS text,
       updated_at
FROM store
WHERE prefix LIKE '%.memories'
ORDER BY user_id, updated_at DESC;
```

Add `AND prefix = 'alice.memories'` for one user. User IDs may contain dots, which `split_part` would cut. Filter on the full `prefix` when that matters.

**Memories per user, and whether each has its vector**

```sql
SELECT s.prefix, count(*) AS memories, count(v.key) AS with_vector
FROM store s
LEFT JOIN store_vectors v ON v.prefix = s.prefix AND v.key = s.key
GROUP BY s.prefix
ORDER BY memories DESC;
```

**Recent idempotent requests**

```sql
SELECT user_id, key, method, path, state, status_code, updated_at
FROM idempotency_keys
ORDER BY updated_at DESC
LIMIT 50;
```

**Table sizes**

```sql
SELECT relname AS table, n_live_tup AS rows_estimate,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
```

## 5. Migrations

- **FinSight tables:** Alembic, in `backend/migrations/versions/`. The `migrate` service runs `alembic upgrade head` as the owner on every `docker compose up`; it is a no-op when the database is current.
- **New migration after changing a `Table`** in `backend/threads.py` or `backend/idempotency.py`, with the dev stack running:

  ```bash
  MIGRATION_DATABASE_URL=postgresql://finsight_admin:<POSTGRES_PASSWORD>@127.0.0.1:5433/finsight \
    alembic revision --autogenerate -m "describe the change"
  ```

  Review the generated file, then run `docker compose run --rm migrate`. `tests/test_migrations.py` fails if the migrations and the models drift apart.
- **LangGraph tables:** `saver.setup()` and `store.setup()`, also run by `migrate`. They track their own versions in the `*_migrations` tables.
- **The vector size is fixed when the store is first set up.** Use one `LLM_PROVIDER` per database: 384 for Groq + bge-small, 768 for Ollama + nomic.

## 6. Backups and restore

```bash
bash scripts/backup.sh                     # backups/finsight_YYYYmmdd_HHMMSS.dump (pg_dump -Fc), keeps the newest 14
KEEP=30 bash scripts/backup.sh
bash scripts/restore.sh backups/finsight_20261126_083955.dump        # asks you to type the database name
bash scripts/restore.sh --yes backups/finsight_20261126_083955.dump  # no prompt
```

`restore.sh` works like this:
1. Stops `api` and `cleanup`.
2. Drops and recreates the database, then runs `pg_restore`.
3. Re-applies roles and grants, and runs `migrate`.
4. Starts the services and waits for `/health`.

For prod, prefix either script with `COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"`.

Schedule `backup.sh` with cron or Task Scheduler, and copy `backups/` off the machine. A backup stored next to the database doesn't survive losing the disk.

## 7. Cleanup

The `cleanup` service runs `python -m backend.housekeeping --loop` every `CLEANUP_INTERVAL_HOURS` (6). It deletes:
- threads idle longer than `CHECKPOINT_TTL_DAYS` (30): their registry row, checkpoints, blobs and writes
- orphan checkpoint threads with no registry row, past the same TTL
- idempotency keys older than `IDEMPOTENCY_TTL_HOURS`

It holds a Postgres advisory lock while it works, so a cron run and the service never overlap. Long-term memories are kept.

For a one-off run: `docker compose run --rm cleanup python -m backend.housekeeping`.
