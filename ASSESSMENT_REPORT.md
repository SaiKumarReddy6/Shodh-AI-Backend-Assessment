# Shodh AI — Backend Engineer Intern Assessment Report

## 1. Project Overview

Shodh AI is a backend system for managing programming contests and evaluating code submissions. It provides REST APIs for contest management, submission handling, automated code execution, judging, rejudging, and leaderboard generation.

The backend is developed using Python, FastAPI, PostgreSQL, SQLAlchemy, and Docker.

## 2. Technology Stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL
- **ORM:** SQLAlchemy
- **Code Execution:** Docker containers
- **Testing:** Pytest
- **API Documentation:** Swagger UI / OpenAPI
- **Container Management:** Docker

## 3. Core Features Implemented

### Contest and Submission Management

- Contest-based programming submissions.
- Submission creation and status tracking.
- Contest and problem association.
- Organization-level access isolation.

### Automated Code Judging

- Docker-based execution of submitted programs.
- Automated test case evaluation.
- Verdict generation.
- Execution time tracking.
- Runtime error detection.
- Time limit enforcement.
- Output limit enforcement.

### Submission Verdicts

The system supports the following verdicts:

- Accepted
- Wrong Answer
- Runtime Error
- Time Limit Exceeded
- Output Limit Exceeded

### Database Persistence

- PostgreSQL integration.
- Persistent submission records.
- Judge attempt records.
- Submission status updates.
- Leaderboard data persistence.

### Queue and Worker Processing

- Background judge worker.
- Queued submission processing.
- Database-backed task claiming.
- Submission attempt tracking.
- Worker recovery mechanisms.
- Protection against processing stale attempts.

### Idempotency

- Idempotency key support for submission requests.
- Duplicate request handling.
- Conflict detection when an idempotency key is reused with different request data.

### Rejudging

- Support for rejudging submissions.
- Tracking of judge attempts.
- Updating submission verdicts following rejudging.

### Leaderboard

- Score calculation.
- Solved problem tracking.
- Penalty calculation.
- Leaderboard retrieval from persisted database records.

### Execution Isolation

Submitted programs are executed using Docker-based isolation with resource restrictions and controlled execution environments.

## 4. Database

PostgreSQL is used as the primary database.

The application has been verified to connect successfully to PostgreSQL and persist submission and judging data.

Database connectivity was verified through the health endpoint:

```json
{
  "status": "ok",
  "database_available": true,
  "execution_mode": "docker",
  "judge_available": true
}
```

## 5. Testing and Verification

### Automated Tests

The project test suite was executed using Pytest.

**Result:**

```text
4 passed, 1 warning
```

### Manual Integration Verification

The following functionality was manually verified:

- Backend health endpoint.
- PostgreSQL database connectivity.
- Docker execution environment.
- Submission creation.
- Accepted verdict.
- Wrong Answer verdict.
- Runtime Error verdict.
- Time Limit Exceeded verdict.
- Output Limit handling.
- Submission status retrieval.
- Submission persistence in PostgreSQL.
- Idempotent submission requests.
- Idempotency key conflict handling.
- Rejudging.
- Leaderboard retrieval.
- Organization-level access isolation.

## 6. API Verification

The API was tested using HTTP requests against the locally running FastAPI application.

Health check:

```http
GET /health
```

Submission creation:

```http
POST /contests/{contest_id}/submissions
```

Submission status:

```http
GET /submissions/{submission_id}
```

The application also provides interactive API documentation through Swagger UI.

## 7. Reliability and Data Integrity

The backend includes mechanisms for:

- Database-backed submission processing.
- Tracking individual judge attempts.
- Preventing duplicate submission processing.
- Handling worker failures.
- Recovering interrupted submissions.
- Verifying active judge attempts before updating submission results.
- Maintaining persisted submission status.

## 8. Limitations and Future Improvements

Potential future improvements include:

- Dedicated automated multi-worker race-condition testing.
- Load and performance benchmarking.
- Expanded automated integration tests.
- Production-grade authentication and authorization.
- Managed database migration tooling.
- Monitoring, metrics, and structured operational dashboards.
- Further hardening of the execution environment.

## 9. Conclusion

The Shodh AI backend implements the primary programming contest submission and automated judging workflow. PostgreSQL persistence, Docker-based code execution, verdict handling, submission tracking, idempotency, rejudging, and leaderboard functionality have been implemented and verified.

The application has been tested locally, and the automated test suite passes successfully.

The project is ready for final review and submission, subject to the assessment's specific requirements.
