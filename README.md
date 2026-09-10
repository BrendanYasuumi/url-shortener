# URL Shortener and Analytics API

A RESTful backend service for creating fixed-width short links, redirecting
visitors, and tracking aggregate link usage. The service uses FastAPI for its
HTTP interface and Python's standard `sqlite3` driver for persistent storage.

## Features

- HTTP and HTTPS URL validation with Pydantic v2
- Unique six-character codes using Base62 (`0-9`, `a-z`, and `A-Z`)
- Transactional SQLite persistence
- Explicitly indexed short-code lookups
- Atomic click increments under concurrent requests
- `307 Temporary Redirect` responses that preserve complete destination URLs
- Analytics containing the original URL, code, total clicks, and creation time
- Safe `404`, `422`, and `503` error responses
- Isolated API, database, algorithm, concurrency, and benchmark tests
- Configurable 500-request concurrent benchmark
- Multi-stage, non-root Docker image with persistent storage and health checks
- Continuous integration for automated tests and container smoke testing
- Reusable Postman collection with success and error-response checks

## Architecture

```text
url-shortener/
├── .github/
│   └── workflows/
│       └── ci.yml        # Automated tests and Docker verification
├── app/
│   ├── __init__.py
│   ├── database.py       # Schema, connection lifecycle, transactions, queries
│   ├── main.py           # FastAPI application, dependencies, and routes
│   ├── schemas.py        # Pydantic request and response contracts
│   └── utils.py          # Fixed-width Base62 encoding and decoding
├── postman/
│   ├── Local.postman_environment.json
│   └── URL Shortener API.postman_collection.json
├── tests/
│   ├── test_benchmark.py
│   ├── test_database.py
│   ├── test_main.py
│   ├── test_schemas.py
│   └── test_utils.py
├── benchmark.py
├── Dockerfile
└── requirements.txt
```

The main creation flow is:

```text
POST /shorten
  -> validate JSON
  -> insert original URL
  -> receive SQLite ID
  -> encode ID as six-character Base62
  -> update and commit the row
  -> return 201 Created
```

Each HTTP request receives its own transactional database connection. Normal
completion commits the transaction; failures roll it back; cleanup always
closes the connection.

## API

Interactive OpenAPI documentation is available from a running server at
`/docs`. ReDoc is available at `/redoc`.

| Method | Endpoint | Success | Description |
|---|---|---:|---|
| `GET` | `/` | `200` | Check that the API process is running |
| `POST` | `/shorten` | `201` | Validate and persist a destination URL |
| `GET` | `/{short_code}` | `307` | Record a click and redirect to the destination |
| `GET` | `/analytics/{short_code}` | `200` | Retrieve URL metadata and total clicks |

Invalid request bodies return `422`. Unknown short codes return `404`.
Unexpected SQLite failures are logged internally and returned as a safe `503`
without exposing database details.

### Create a short URL

Request:

```bash
curl -i -X POST http://127.0.0.1:8000/shorten \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/articles/fastapi?source=readme"}'
```

Example response from a new database:

```json
{
  "original_url": "https://example.com/articles/fastapi?source=readme",
  "short_code": "000001",
  "short_url": "http://127.0.0.1:8000/000001",
  "created_at": "2026-09-09T12:00:00"
}
```

### Inspect a redirect

Do not pass `-L` if you want to inspect the redirect instead of following it:

```bash
curl -i http://127.0.0.1:8000/000001
```

```http
HTTP/1.1 307 Temporary Redirect
Location: https://example.com/articles/fastapi?source=readme
```

### Read analytics

```bash
curl http://127.0.0.1:8000/analytics/000001
```

```json
{
  "original_url": "https://example.com/articles/fastapi?source=readme",
  "short_code": "000001",
  "clicks": 1,
  "created_at": "2026-09-09T12:00:00"
}
```

## Postman

The [`postman`](postman) directory contains a Collection v2.1 file and a local
environment. To exercise the API manually:

1. Start the development server with `uvicorn app.main:app --reload`.
2. In Postman, select **Import** and import both JSON files from `postman/`.
3. Select the **URL Shortener - Local** environment.
4. Open the **URL Shortener API** collection and run it with the Collection
   Runner.

Run the requests in their saved order. The creation request stores the newly
generated `short_code` as a collection variable, which the redirect and
analytics requests then reuse. Redirect following is disabled for redirect
checks, so Postman inspects the API's `307` response without contacting the
external destination.

The seven saved requests check the complete successful workflow as well as
invalid-URL and unknown-code errors. Their post-response scripts verify status
codes, response bodies, the `Location` header, the Base62 code format, and the
recorded click count.

## Local development

### Requirements

- Python 3.11
- `pip`

Create and activate an isolated environment:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Start the development server:

```bash
uvicorn app.main:app --reload
```

The API will be available at <http://127.0.0.1:8000>. The default database file
is `urls.db` in the current directory.

To use a different database location:

```bash
URL_SHORTENER_DB_PATH=/absolute/path/to/urls.db \
  uvicorn app.main:app --reload
```

## Base62 codes

The database assigns each URL an auto-incremented integer ID. The application
converts that ID into Base62 and pads the result to six characters:

```text
1     -> 000001
10    -> 00000a
61    -> 00000Z
62    -> 000010
3,843 -> 0000ZZ
```

Six Base62 positions represent `62^6`, or 56,800,235,584 values. Inputs outside
that fixed-width range fail explicitly rather than producing longer codes.
Encoding database IDs guarantees uniqueness, but the resulting codes are
predictable and are not intended to function as secrets.

## Database design

The `urls` table contains:

| Column | Definition | Purpose |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Unique source value for Base62 |
| `original_url` | `TEXT NOT NULL` | Redirect destination |
| `short_code` | `TEXT UNIQUE` | Public lookup key |
| `clicks` | `INTEGER NOT NULL DEFAULT 0` | Aggregate redirect count |
| `created_at` | `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` | UTC creation time |

`idx_urls_short_code` explicitly indexes the public lookup key. The test suite
also inspects SQLite's query plan to confirm that code lookup uses an index.

Click tracking uses this database-side operation:

```sql
UPDATE urls SET clicks = clicks + 1 WHERE short_code = ?;
```

Performing the increment in SQLite prevents concurrent requests from reading
the same value and overwriting one another's updates. Parameter placeholders
keep external values separate from SQL syntax.

SQLite runs in Write-Ahead Logging mode with a ten-second busy timeout. WAL
allows readers and a writer to make progress concurrently, while the timeout
lets overlapping writers wait briefly instead of immediately failing with a
locked-database error.

## Tests

Run the complete suite with:

```bash
pytest -v
```

The current suite contains 78 collected cases covering:

- request validation;
- Base62 boundaries, failures, and round trips;
- schema constraints and defaults;
- commit and rollback behavior;
- indexed query planning;
- URL persistence and application restarts;
- status codes and redirect headers;
- click analytics;
- safe database failures;
- overlapping redirect correctness; and
- benchmark percentile calculations.

Every API test receives a new temporary SQLite file, so tests never modify the
development database or depend on execution order.

## Continuous integration

GitHub Actions runs the following independent checks for every push to `main`
and every pull request targeting `main`:

1. **Python tests** — installs the pinned dependencies on Python 3.11 and runs
   the complete pytest suite.
2. **Docker build and smoke test** — builds the production image, starts a
   container, and verifies that its HTTP health endpoint responds successfully.

The workflow can also be started manually from the repository's **Actions**
tab. Dependency caching avoids downloading unchanged Python packages on every
run, and concurrency control cancels an obsolete run when a newer commit is
pushed to the same branch.

## Benchmark

Start the API without development reload or access-log overhead:

```bash
URL_SHORTENER_DB_PATH=benchmark.db \
  uvicorn app.main:app --no-access-log
```

In another terminal, run:

```bash
python benchmark.py
```

The default workload creates a new short URL and sends 500 redirect requests
using 50 worker threads. Redirect following is disabled, so the benchmark does
not send traffic to the destination website. It reports:

- HTTP status counts and failure samples;
- wall-clock time and throughput;
- average, minimum, median, p95, and maximum successful-request latency; and
- the recorded click count compared with the successful redirect count.

The workload is configurable:

```bash
python benchmark.py --requests 1000 --concurrency 75 --timeout 20
python benchmark.py --base-url http://127.0.0.1:9000
```

### Sample local result

The following result was measured on an Apple M4 MacBook Pro with 16 GB memory,
Python 3.11.16, one Uvicorn process, a fresh local SQLite database, disabled
access logging, 500 requests, and concurrency 50:

```text
Status counts: {'307': 500}
Recorded clicks: 500 (expected 500)
Wall time: 0.486 s
Throughput: 1029.6 requests/second
Average latency: 26.14 ms
Minimum latency: 3.15 ms
Median latency: 8.33 ms
p95 latency: 124.14 ms
Maximum latency: 465.75 ms
```

These are end-to-end local HTTP measurements, not isolated SQL lookup times or
production guarantees. Results will vary with hardware and system load.

## Docker

Build the image:

```bash
docker build -t url-shortener .
```

Run it with a named volume so SQLite data survives container replacement:

```bash
docker run --rm \
  -p 8000:8000 \
  -v url-shortener-data:/data \
  url-shortener
```

The container:

- uses a multi-stage Python 3.11 slim build;
- runs as the unprivileged `appuser` account;
- stores SQLite at `/data/urls.db`;
- exposes port `8000`; and
- includes an HTTP health check.

The verified local image was approximately 64.5 MB. Container replacement was
tested against the same named volume, preserving the stored URL, click count,
and creation timestamp.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `URL_SHORTENER_DB_PATH` | `urls.db` locally, `/data/urls.db` in Docker | SQLite file location |

The public short URL is generated from the host and scheme of the creation
request. A deployment behind a reverse proxy must configure trusted forwarded
headers correctly.

## Operational scope

This project is designed for a small, single-instance deployment. SQLite allows
many readers but only limited concurrent writing. Multiple Uvicorn workers
sharing one SQLite file, network filesystems, or sustained high write volume are
outside the intended architecture.

A larger public service would typically add authentication for private
analytics, rate limiting, abuse controls, backups, schema migrations, structured
observability, HTTPS termination, and a client-independent public base URL.
