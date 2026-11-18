
from datetime import datetime, timedelta, timezone
import os
import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import soudh


class ContestSmokeTest(unittest.TestCase):

    def setUp(self):
        self.original_engine = soudh.engine
        self.original_session_local = soudh.SessionLocal
        self.original_worker_setting = os.environ.get(
            "RUN_JUDGE_WORKER"
        )

        os.environ["RUN_JUDGE_WORKER"] = "0"

        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        soudh.engine = self.engine
        soudh.SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

        self.client = TestClient(soudh.app)
        self.client.__enter__()

        self.organizer = {
            "Authorization": "Bearer organizer-a"
        }

        self.student = {
            "Authorization": "Bearer student-a"
        }

        self.other_organizer = {
            "Authorization": "Bearer organizer-b"
        }

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.engine.dispose()

        soudh.engine = self.original_engine
        soudh.SessionLocal = self.original_session_local

        if self.original_worker_setting is None:
            os.environ.pop("RUN_JUDGE_WORKER", None)
        else:
            os.environ["RUN_JUDGE_WORKER"] = (
                self.original_worker_setting
            )

    # ========================================================
    # TEST 1: SUBMISSION, JUDGING, LEADERBOARD AND REJUDGE
    # ========================================================

    def test_auth_contest_submission_judge_and_leaderboard_flow(self):

        self.assertEqual(
            self.client.get("/auth/me").status_code,
            401,
        )

        identity = self.client.get(
            "/auth/me",
            headers={"Authorization": "bearer organizer-a"},
        )

        self.assertEqual(identity.status_code, 200)
        self.assertEqual(identity.json()["role"], "organizer")

        contest_response = self.client.post(
            "/contests",
            headers=self.organizer,
            json={
                "title": "Smoke Contest",
                "duration_minutes": 60,
            },
        )

        self.assertEqual(
            contest_response.status_code,
            200,
            contest_response.text,
        )

        contest_id = contest_response.json()["id"]

        problem_response = self.client.post(
            f"/contests/{contest_id}/problems",
            headers=self.organizer,
            json={
                "title": "Add numbers",
                "tests": [
                    {
                        "input": "2 3\n",
                        "expected": "5\n",
                    },
                    {
                        "input": "4 6\n",
                        "expected": "10\n",
                        "hidden": True,
                    },
                ],
            },
        )

        self.assertEqual(
            problem_response.status_code,
            200,
            problem_response.text,
        )

        problem_id = problem_response.json()["id"]

        listed_problems = self.client.get(
            f"/contests/{contest_id}/problems",
            headers=self.student,
        )

        self.assertEqual(listed_problems.status_code, 200)
        self.assertEqual(len(listed_problems.json()[0]["tests"]), 1)

        request = {
            "problem_id": problem_id,
            "source": (
                "a, b = map(int, input().split())\n"
                "print(a + b)"
            ),
        }

        submission_headers = {
            **self.student,
            "Idempotency-Key": "smoke-1",
        }

        not_joined = self.client.post(
            f"/contests/{contest_id}/submissions",
            headers=submission_headers,
            json=request,
        )

        self.assertEqual(not_joined.status_code, 403)

        self.assertEqual(
            self.client.post(
                f"/contests/{contest_id}/join",
                headers=self.student,
            ).status_code,
            204,
        )

        self.assertEqual(
            self.client.post(
                f"/contests/{contest_id}/join",
                headers=self.student,
            ).status_code,
            204,
        )

        submission_response = self.client.post(
            f"/contests/{contest_id}/submissions",
            headers=submission_headers,
            json=request,
        )

        self.assertEqual(
            submission_response.status_code,
            202,
            submission_response.text,
        )

        submission_id = submission_response.json()["submission_id"]

        participant_submissions = self.client.get(
            f"/contests/{contest_id}/submissions",
            headers=self.student,
        )

        self.assertEqual(participant_submissions.status_code, 200)
        self.assertEqual(
            participant_submissions.json()["total"],
            1,
        )

        self.assertEqual(
            participant_submissions.json()["items"][0]["id"],
            submission_id,
        )

        organizer_submissions = self.client.get(
            f"/contests/{contest_id}/submissions?status=queued&limit=1",
            headers=self.organizer,
        )

        self.assertEqual(organizer_submissions.status_code, 200)
        self.assertEqual(
            organizer_submissions.json()["total"],
            1,
        )

        duplicate = self.client.post(
            f"/contests/{contest_id}/submissions",
            headers=submission_headers,
            json=request,
        )

        self.assertEqual(duplicate.status_code, 202)
        self.assertEqual(
            duplicate.json()["submission_id"],
            submission_id,
        )

        task = soudh._claim()

        self.assertIsNotNone(task)

        claimed_id, attempt_id, _, _ = task

        self.assertEqual(claimed_id, submission_id)

        soudh._finish(
            submission_id,
            attempt_id,
            "accepted",
            None,
        )

        submission = self.client.get(
            f"/submissions/{submission_id}",
            headers=self.student,
        )

        self.assertEqual(submission.status_code, 200)
        self.assertEqual(
            submission.json()["verdict"],
            "accepted",
        )

        attempts = self.client.get(
            f"/submissions/{submission_id}/attempts",
            headers=self.student,
        )

        self.assertEqual(attempts.status_code, 200)
        self.assertEqual(len(attempts.json()), 1)
        self.assertEqual(attempts.json()[0]["verdict"], "accepted")

        leaderboard = self.client.get(
            f"/contests/{contest_id}/leaderboard",
            headers=self.student,
        )

        self.assertEqual(leaderboard.status_code, 200)
        self.assertEqual(
            leaderboard.json()[0]["participant_id"],
            "student-a",
        )
        self.assertEqual(leaderboard.json()[0]["score"], 100)
        self.assertEqual(leaderboard.json()[0]["solved"], 1)

        updated = self.client.put(
            f"/problems/{problem_id}",
            headers=self.organizer,
            json={
                "tests": [
                    {
                        "input": "1 1\n",
                        "expected": "2\n",
                    }
                ]
            },
        )

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["version"], 2)

        unauthorized_rejudge = self.client.post(
            f"/submissions/{submission_id}/rejudge",
            headers=self.student,
        )

        self.assertEqual(unauthorized_rejudge.status_code, 403)

        rejudge = self.client.post(
            f"/submissions/{submission_id}/rejudge",
            headers=self.organizer,
        )

        self.assertEqual(
            rejudge.status_code,
            202,
            rejudge.text,
        )

        self.assertEqual(rejudge.json()["problem_version"], 2)

        rejudge_task = soudh._claim()

        self.assertIsNotNone(rejudge_task)

        rejudged_id, rejudge_attempt_id, _, _ = rejudge_task

        self.assertEqual(rejudged_id, submission_id)

        soudh._finish(
            submission_id,
            rejudge_attempt_id,
            "accepted",
            None,
        )

        rejudged_submission = self.client.get(
            f"/submissions/{submission_id}",
            headers=self.student,
        )

        self.assertEqual(rejudged_submission.status_code, 200)
        self.assertEqual(
            rejudged_submission.json()["verdict"],
            "accepted",
        )

        rejudge_history = self.client.get(
            f"/submissions/{submission_id}/attempts",
            headers=self.student,
        )

        self.assertEqual(rejudge_history.status_code, 200)
        self.assertEqual(len(rejudge_history.json()), 2)

    # ========================================================
    # TEST 2: INTERRUPTED SUBMISSION RECOVERY
    # ========================================================

    def test_interrupted_submission_recovery(self):

        now = datetime.now(timezone.utc)
        old_time = now - timedelta(minutes=5)

        contest = soudh.Contest(
            id=str(uuid.uuid4()),
            organization_id="org-a",
            title="Recovery Test",
            opens_at=old_time,
            closes_at=now + timedelta(hours=1),
        )

        problem = soudh.Problem(
            id=str(uuid.uuid4()),
            contest_id=contest.id,
            title="Recovery Problem",
            version=1,
            tests=[
                {
                    "input": "1\n",
                    "expected": "1\n",
                    "hidden": False,
                }
            ],
            active=True,
        )

        submission_id = str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())

        submission = soudh.Submission(
            id=submission_id,
            contest_id=contest.id,
            problem_id=problem.id,
            actor_id="student-a",
            idempotency_key="recovery-test",
            request_hash="recovery-hash",
            source="print(input())",
            problem_version=1,
            test_snapshot=problem.tests,
            status="running",
            verdict=None,
            score=0,
            active_attempt_id=attempt_id,
            attempts_count=1,
            created_at=old_time,
        )

        attempt = soudh.JudgeAttempt(
            id=attempt_id,
            submission_id=submission_id,
            number=1,
            problem_version=1,
            verdict="running",
            mode="docker",
            started_at=old_time,
            finished_at=None,
            heartbeat_at=old_time,
        )

        with soudh.SessionLocal.begin() as session:
            session.add(contest)
            session.flush()

            session.add(problem)
            session.flush()

            session.add(submission)
            session.flush()

            session.add(attempt)

        recovered = soudh.recover_interrupted_submissions()

        self.assertEqual(recovered, 1)

        with soudh.SessionLocal() as session:

            recovered_submission = session.get(
                soudh.Submission,
                submission_id,
            )

            recovered_attempt = session.get(
                soudh.JudgeAttempt,
                attempt_id,
            )

            self.assertIsNotNone(recovered_submission)
            self.assertIsNotNone(recovered_attempt)

            self.assertEqual(recovered_submission.status, "queued")
            self.assertEqual(recovered_submission.attempts_count, 1)

            self.assertIsNone(
                recovered_submission.active_attempt_id
            )

            self.assertEqual(
                recovered_attempt.verdict,
                "judge_error",
            )

            self.assertIsNotNone(
                recovered_attempt.finished_at
            )

    # ========================================================
    # TEST 3: ORGANIZATION SCOPE AND ROLE ENFORCEMENT
    # ========================================================

    def test_organization_scope_and_role_enforcement(self):

        contest_response = self.client.post(
            "/contests",
            headers=self.organizer,
            json={
                "title": "Private Contest",
                "duration_minutes": 60,
            },
        )

        self.assertEqual(contest_response.status_code, 200)

        contest_id = contest_response.json()["id"]

        other_organizer_response = self.client.get(
            f"/contests/{contest_id}",
            headers=self.other_organizer,
        )

        self.assertEqual(
            other_organizer_response.status_code,
            404,
        )

        unauthorized_problem = self.client.post(
            f"/contests/{contest_id}/problems",
            headers=self.student,
            json={
                "title": "Unauthorized",
                "tests": [
                    {
                        "input": "",
                        "expected": "",
                    }
                ],
            },
        )

        self.assertEqual(
            unauthorized_problem.status_code,
            403,
        )

    # ========================================================
    # TEST 4: SUBMISSION IDEMPOTENCY
    # ========================================================

    def test_submission_idempotency(self):

        contest_response = self.client.post(
            "/contests",
            headers=self.organizer,
            json={
                "title": "Idempotency Contest",
                "duration_minutes": 60,
            },
        )

        self.assertEqual(contest_response.status_code, 200)

        contest_id = contest_response.json()["id"]

        problem_response = self.client.post(
            f"/contests/{contest_id}/problems",
            headers=self.organizer,
            json={
                "title": "Identity Problem",
                "tests": [
                    {
                        "input": "1\n",
                        "expected": "1\n",
                    }
                ],
            },
        )

        self.assertEqual(problem_response.status_code, 200)

        problem_id = problem_response.json()["id"]

        join_response = self.client.post(
            f"/contests/{contest_id}/join",
            headers=self.student,
        )

        self.assertEqual(join_response.status_code, 204)

        request = {
            "problem_id": problem_id,
            "source": "print(input())",
        }

        headers = {
            **self.student,
            "Idempotency-Key": "idempotency-test-001",
        }

        first = self.client.post(
            f"/contests/{contest_id}/submissions",
            headers=headers,
            json=request,
        )

        self.assertEqual(first.status_code, 202)

        second = self.client.post(
            f"/contests/{contest_id}/submissions",
            headers=headers,
            json=request,
        )

        self.assertEqual(second.status_code, 202)

        self.assertEqual(
            first.json()["submission_id"],
            second.json()["submission_id"],
        )

        submissions = self.client.get(
            f"/contests/{contest_id}/submissions",
            headers=self.student,
        )

        self.assertEqual(submissions.status_code, 200)
        self.assertEqual(submissions.json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
