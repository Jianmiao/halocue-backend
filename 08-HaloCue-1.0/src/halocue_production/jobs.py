from __future__ import annotations

import inspect
import json
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .errors import ProductionError
from .models import new_id
from .runtime import RuntimeStore


class JobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class JobOutcome:
    result: dict[str, Any]
    commit: Callable[[], None] | None = None


class CancellationToken:
    def __init__(
        self, runtime: RuntimeStore, attempt_id: str, event: threading.Event
    ) -> None:
        self._runtime = runtime
        self.attempt_id = attempt_id
        self._event = event

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set() or not self._runtime.attempt_accepts_result(
            self.attempt_id
        )

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise JobCancelled("job attempt was cancelled")


@dataclass
class JobRecord:
    job_id: str
    kind: str
    state: str
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    run_id: str | None = None
    # Only non-sensitive inputs needed to submit the same stage again.
    retry_context: dict[str, Any] = field(default_factory=dict)
    attempt_id: str | None = None
    work_item_id: str | None = None
    production_run_id: str | None = None
    ordinal: int = 1
    provider: str | None = None
    model_or_engine: str | None = None
    request_digest: str | None = None
    cancellation_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JobRecord":
        return cls(
            job_id=str(value["job_id"]),
            kind=str(value["kind"]),
            state=str(value["state"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            result=value.get("result") if isinstance(value.get("result"), dict) else None,
            error=value.get("error") if isinstance(value.get("error"), dict) else None,
            run_id=str(value.get("run_id") or "").strip() or None,
            retry_context=(
                value.get("retry_context")
                if isinstance(value.get("retry_context"), dict)
                else {}
            ),
            attempt_id=str(value.get("attempt_id") or "").strip() or None,
            work_item_id=str(value.get("work_item_id") or "").strip() or None,
            production_run_id=(
                str(value.get("production_run_id") or "").strip() or None
            ),
            ordinal=int(value.get("ordinal") or 1),
            provider=str(value.get("provider") or "").strip() or None,
            model_or_engine=(
                str(value.get("model_or_engine") or "").strip() or None
            ),
            request_digest=str(value.get("request_digest") or "").strip() or None,
            cancellation_requested=bool(value.get("cancellation_requested", False)),
        )


Task = Callable[..., dict[str, Any] | JobOutcome]
FailureHandler = Callable[[Exception], None]


class JobRegistry:
    _JOB_ID = re.compile(r"job-[0-9a-f]{12}")

    def __init__(
        self,
        base_dir: Path | RuntimeStore,
        *,
        runtime: RuntimeStore | None = None,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="halocue")
        self._futures: dict[str, Future] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._lock = threading.RLock()
        self._commit_lock = threading.RLock()
        if isinstance(base_dir, RuntimeStore):
            if runtime is not None and runtime is not base_dir:
                raise ValueError("conflicting runtime stores")
            self._base_dir: Path | None = None
            self._runtime = base_dir
        else:
            self._base_dir = Path(base_dir)
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._runtime = runtime or RuntimeStore(
                self._base_dir.parent / "runtime.sqlite3"
            )
            self._import_legacy_jobs()
        self._runtime.abandon_active_attempts()

    def _import_legacy_jobs(self) -> None:
        if self._base_dir is None:
            return
        for path in self._base_dir.glob("job-*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                record = JobRecord.from_dict(value)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if self._JOB_ID.fullmatch(record.job_id):
                self._runtime.import_legacy_attempt(record.to_dict())

    @staticmethod
    def _invoke(task: Task, token: CancellationToken) -> dict[str, Any] | JobOutcome:
        try:
            inspect.signature(task).bind(token)
        except (TypeError, ValueError):
            return task()
        return task(token)

    def _create_record(
        self,
        kind: str,
        *,
        run_id: str | None,
        retry_context: dict[str, Any] | None,
        work_item_id: str | None,
        provider: str | None,
        model_or_engine: str | None,
    ) -> JobRecord:
        value = self._runtime.create_attempt(
            job_id=new_id("job"),
            kind=kind,
            legacy_run_id=run_id,
            retry_context=dict(retry_context or {}),
            work_item_id=work_item_id,
            provider=provider,
            model_or_engine=model_or_engine,
        )
        return JobRecord.from_dict(value)

    def _fail_attempt(
        self,
        attempt_id: str,
        token: CancellationToken,
        exc: Exception,
        on_failure: FailureHandler | None,
    ) -> None:
        with self._commit_lock:
            if token.is_cancelled():
                return
            if on_failure is not None:
                on_failure(exc)
            self._runtime.fail_attempt(
                attempt_id,
                {
                    "code": str(getattr(exc, "code", "job_failed")),
                    "message": str(exc),
                    "status": int(getattr(exc, "status", 500)),
                    "details": getattr(exc, "details", {}) or {},
                },
            )

    def _execute(
        self,
        record: JobRecord,
        task: Task,
        token: CancellationToken,
        on_failure: FailureHandler | None,
    ) -> dict[str, Any] | None:
        attempt_id = str(record.attempt_id)
        if not self._runtime.start_attempt(attempt_id):
            return None
        try:
            token.raise_if_cancelled()
            result = self._invoke(task, token)
            token.raise_if_cancelled()
        except JobCancelled:
            self._runtime.cancel_attempt(attempt_id)
            return None
        except Exception as exc:
            self._fail_attempt(attempt_id, token, exc, on_failure)
            return None
        outcome = result if isinstance(result, JobOutcome) else JobOutcome(result)
        if not isinstance(outcome.result, dict):
            error = TypeError("job result must be an object")
            self._fail_attempt(attempt_id, token, error, on_failure)
            return None
        with self._commit_lock:
            if token.is_cancelled():
                return None
            try:
                if outcome.commit is not None:
                    outcome.commit()
            except Exception as exc:
                self._fail_attempt(attempt_id, token, exc, on_failure)
                return None
            if not self._runtime.succeed_attempt(attempt_id, outcome.result):
                return None
        return outcome.result

    def submit(
        self,
        kind: str,
        task: Task,
        *,
        run_id: str | None = None,
        retry_context: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        provider: str | None = None,
        model_or_engine: str | None = None,
        on_failure: FailureHandler | None = None,
    ) -> JobRecord:
        record = self._create_record(
            kind,
            run_id=run_id,
            retry_context=retry_context,
            work_item_id=work_item_id,
            provider=provider,
            model_or_engine=model_or_engine,
        )
        event = threading.Event()
        token = CancellationToken(self._runtime, str(record.attempt_id), event)
        with self._lock:
            self._tokens[record.job_id] = token
            future = self._executor.submit(
                self._execute, record, task, token, on_failure
            )
            self._futures[record.job_id] = future
        return record

    def run_sync(
        self,
        kind: str,
        task: Task,
        *,
        run_id: str | None = None,
        retry_context: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        provider: str | None = None,
        model_or_engine: str | None = None,
        on_failure: FailureHandler | None = None,
    ) -> tuple[JobRecord, dict[str, Any]]:
        record = self._create_record(
            kind,
            run_id=run_id,
            retry_context=retry_context,
            work_item_id=work_item_id,
            provider=provider,
            model_or_engine=model_or_engine,
        )
        event = threading.Event()
        token = CancellationToken(self._runtime, str(record.attempt_id), event)
        with self._lock:
            self._tokens[record.job_id] = token
        result = self._execute(record, task, token, on_failure)
        if result is None:
            completed = self.get(record.job_id)
            if completed and completed.state == "failed" and completed.error:
                error = completed.error
                raise ProductionError(
                    str(error.get("code") or "job_failed"),
                    str(error.get("message") or "后台任务失败"),
                    status=int(error.get("status") or 500),
                    details=(error.get("details") if isinstance(error.get("details"), dict) else {}),
                )
            raise JobCancelled("job attempt was cancelled")
        completed = self.get(record.job_id)
        return completed or record, result

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            with self._commit_lock:
                record = self.get(job_id)
                if record is None or record.state not in {"queued", "running", "started"}:
                    return False
                token = self._tokens.get(job_id)
                future = self._futures.get(job_id)
                if token is not None:
                    token.cancel()
                cancelled = self._runtime.cancel_attempt(str(record.attempt_id))
                if cancelled and future is not None:
                    future.cancel()
                return cancelled

    def get(self, job_id: str) -> JobRecord | None:
        if not self._JOB_ID.fullmatch(str(job_id)):
            return None
        value = self._runtime.get_attempt(job_id)
        return JobRecord.from_dict(value) if value else None

    def list(self) -> list[JobRecord]:
        return [JobRecord.from_dict(value) for value in self._runtime.list_attempts()]

    def close(self) -> None:
        with self._lock:
            with self._commit_lock:
                for token in self._tokens.values():
                    token.cancel()
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._runtime.abandon_active_attempts()
