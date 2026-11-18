# Shodh AI — Programming Contest Backend

A backend system for managing programming contests, code submissions, automated judging, and leaderboards. Built with FastAPI, PostgreSQL, SQLAlchemy, and Docker.

## Features

- **Contest Management** — Manage programming contests, problems, and submissions.
- **Automated Code Judging** — Execute submitted programs inside Docker containers.
- **Multiple Verdicts** — Accepted, Wrong Answer, Runtime Error, Time Limit Exceeded, and Output Limit Exceeded.
- **PostgreSQL Persistence** — Persist contest, submission, and judge attempt data.
- **Background Judge Worker** — Process queued submissions asynchronously.
- **Submission Recovery** — Recover interrupted submissions and handle worker failures.
- **Idempotency** — Prevent duplicate submission processing through idempotency keys.
- **Rejudging** — Re-evaluate submissions and track individual judge attempts.
- **Leaderboard** — Calculate scores, solved problems, and penalties.
- **Organization Isolation** — Enforce organization-level access control.
- **Role-Based Access Control** — Restrict operations based on user roles.
- **Execution Isolation** — Execute code using Docker with resource restrictions.

## Technology Stack

| Technology | Purpose |
|---|---|
| Python 3.13 | Backend programming language |
| FastAPI | REST API framework |
| PostgreSQL 16 | Database |
| SQLAlchemy | Database ORM |
| Docker | Isolated code execution |
| Pytest | Automated testing |
| Uvicorn | ASGI server |

## Architecture

The system consists of the following major components:

1. **API Layer** — Handles HTTP requests, authentication, contest operations, and submissions.
2. **Database Layer** — Stores contests, problems, submissions, judge attempts, and related data in PostgreSQL.
3. **Submission Queue** — Manages queued submissions for judging.
4. **Judge Worker** — Claims queued submissions, executes code, evaluates test cases, and updates verdicts.
5. **Docker Execution Environment** — Runs submitted programs in isolated containers.
6. **Leaderboard** — Calculates contest standings using submission results.

### Submission Workflow

```text
Client
  |
  v
FastAPI Backend
  |
  v
PostgreSQL
  |
  v
Submission Queue
  |
  v
Judge Worker
  |
  v
Docker Execution
  |
  v
Test Case Evaluation
  |
  v
Verdict Update
  |
  v
Leaderboard
```

## Project Structure

```text
Shodh Ai/
│
├── soudh.py
├── compose.yaml
├── requirements.txt
├── .env.example
├── README.md
├── ASSESSMENT_REPORT.md
│
├── tests/
│   └── test_contest_smoke.py
│
├── docker/
│
├── docker-compose/
│
└── .gitignore
```

Additional database files and local development artifacts may exist but are not required for the core application.

## Prerequisites

Install the following before running the project:

- Python 3.11 or newer
- Docker Desktop
- PostgreSQL (provided through Docker Compose)
- Git (optional)

Verify installations:

```bash
python --version
docker --version
docker compose version
```

## Installation and Setup

### 1. Clone the repository

```bash
git clone <repository-url>
cd Shodh-Ai
```

If working with the downloaded project, navigate directly to the project directory.

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file using `.env.example` as a template.

Example database connection:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/shodh
```

Update the environment variables according to your local configuration.

### 4. Start PostgreSQL

Start the PostgreSQL container using Docker Compose:

```bash
docker compose up -d
```

Check the running containers:

```bash
docker ps
```

Verify that PostgreSQL is healthy and accessible.

### 5. Start the backend

Run the FastAPI application:

```bash
python -m uvicorn soudh:app --host 127.0.0.1 --port 8000
```

The backend will be available at:

```text
http://127.0.0.1:8000
```

## API Documentation

FastAPI provides interactive API documentation.

### Swagger UI

```text
http://127.0.0.1:8000/docs
```

### OpenAPI Schema

```text
http://127.0.0.1:8000/openapi.json
```

Use Swagger UI to explore endpoints, inspect request and response schemas, and test API operations.

## Health Check

Endpoint:

```http
GET /health
```

Example response:

```json
{
  "status": "ok",
  "database_available": true,
  "execution_mode": "docker",
  "judge_available": true
}
```

The health endpoint provides information about application status, database connectivity, and judge availability.

## Submission API

### Create a Submission

```http
POST /contests/{contest_id}/submissions
```

Example:

```bash
curl -X POST "http://127.0.0.1:8000/contests/{contest_id}/submissions" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer student-a" \
  -H "Idempotency-Key: submission-001" \
  -d '{"problem_id":"sum-a","source_code":"..."}'
```

Replace the contest ID, problem ID, and source code with the appropriate values.

### Retrieve Submission Status

```http
GET /submissions/{submission_id}
```

Example:

```bash
curl -X GET "http://127.0.0.1:8000/submissions/{submission_id}" \
  -H "accept: application/json" \
  -H "Authorization: Bearer student-a"
```

The endpoint returns submission information, processing status, and judge results.

## Authentication and Authorization

The application supports authenticated API operations and organization-level access control.

Development identities are used for local testing.

Example authorization header:

```http
Authorization: Bearer student-a
```

Authentication and role enforcement are verified through the automated tests.

**Note:** Development authentication credentials are intended for local testing and must be replaced with a production-grade authentication mechanism before deployment.

## Automated Code Judging

Submitted source code is executed using Docker containers.

The judging system supports:

- Test case evaluation
- Standard input and output handling
- Execution time limits
- Output size limits
- Runtime error detection
- Verdict generation
- Submission attempt tracking

### Supported Verdicts

| Verdict | Description |
|---|---|
| Accepted | All test cases passed |
| Wrong Answer | Output did not match expected results |
| Runtime Error | Program terminated with an error |
| Time Limit Exceeded | Execution exceeded the allowed time |
| Output Limit Exceeded | Program exceeded the permitted output size |

The judge uses Docker-based execution with restrictions designed to isolate submitted code.

## Submission Processing and Reliability

The backend includes mechanisms for reliable submission processing:

- Database-backed submission queue.
- Background worker processing.
- Submission attempt tracking.
- Recovery of interrupted submissions.
- Idempotency key handling.
- Duplicate submission prevention.
- Active attempt verification before updating results.

These mechanisms help maintain submission consistency when processing failures occur.

## Rejudging

Submissions can be re-evaluated through the rejudging functionality.

The system tracks judge attempts and updates submission results after re-evaluation.

## Leaderboard

The leaderboard is generated from persisted submission results.

It supports:

- Contest participant rankings.
- Total score calculation.
- Solved problem tracking.
- Penalty calculation.

Leaderboard data is retrieved from the database.

## Testing

The project uses Pytest.

Run the test suite:

```bash
python -m pytest -v
```

### Verified Automated Tests

| Test | Description |
|---|---|
| Contest, submission, judging, and leaderboard flow | Verifies the main contest workflow |
| Interrupted submission recovery | Verifies recovery of interrupted submissions |
| Organization scope and role enforcement | Verifies access control |
| Submission idempotency | Verifies duplicate request handling |

Latest verification:

```text
4 passed, 1 warning
```

### Manual Verification

The following functionality has also been manually verified:

- PostgreSQL connectivity.
- Backend health check.
- Docker judge availability.
- Accepted submissions.
- Wrong Answer verdict.
- Runtime Error verdict.
- Time Limit Exceeded verdict.
- Output Limit handling.
- Submission retrieval.
- PostgreSQL persistence.
- Idempotency.
- Idempotency key conflict handling.
- Rejudging.
- Leaderboard retrieval.

## Database

PostgreSQL is used as the primary database.

The backend persists submission data, judge attempts, and related contest information.

Database connectivity is verified through the health endpoint and integration testing.

## Development

Start the backend:

```bash
python -m uvicorn soudh:app --host 127.0.0.1 --port 8000 --reload
```

Run tests:

```bash
python -m pytest -v
```

Check application health:

```bash
curl http://127.0.0.1:8000/health
```

Access API documentation:

```text
http://127.0.0.1:8000/docs
```

## Known Limitations and Future Improvements

Potential future enhancements include:

- Production-grade authentication.
- Dedicated multi-worker concurrency testing.
- Load and performance benchmarking.
- Expanded automated integration testing.
- Managed database migration tooling.
- Monitoring and operational metrics.
- Further execution environment hardening.

## Assessment Verification

The following components have been implemented and verified locally:

- FastAPI backend.
- PostgreSQL integration.
- Docker-based code execution.
- Submission processing.
- Automated verdict generation.
- Submission recovery.
- Idempotency.
- Organization isolation.
- Role enforcement.
- Rejudging.
- Leaderboard functionality.
- Automated tests.

## Conclusion

Shodh AI provides a backend foundation for programming contests and automated code evaluation.

The system integrates FastAPI, PostgreSQL, and Docker-based execution to process submissions, evaluate code, persist results, and generate contest leaderboards.

The implementation has been verified locally through automated and manual testing.
