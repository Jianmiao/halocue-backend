from __future__ import annotations

import re


class DomainError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_payload(self) -> dict:
        """Return the legacy HTTP wrapper retained for existing callers."""
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }

    def to_api_error_payload(self) -> dict:
        """Return the negotiated ApiError/1.0 view without private details."""
        raw_code = re.sub(r"[^A-Z0-9_]", "_", self.code.upper())
        code = (
            raw_code
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", raw_code)
            else "HALOCUE_ERROR"
        )
        lower_code = self.code.casefold()
        if self.status == 409 or "conflict" in lower_code or "revision" in lower_code:
            category = "conflict"
        elif lower_code.startswith(("model_", "production_unavailable")):
            category = "model_transient" if lower_code.startswith("model_") else "environment"
        elif self.status >= 500:
            category = "internal"
        elif self.status in {400, 413, 422}:
            category = "validation"
        else:
            category = "environment"

        retryability = "never"
        if self.status in {409}:
            retryability = "after_user_action"
        elif self.status in {502, 503, 504} or category == "model_transient":
            retryability = "automatic"
        message = re.sub(
            r"(?i)bearer\s+[^\s,;]+|(?:api[_ -]?key|secret|token|password)\s*[=:]\s*[^\s,;]+",
            "[已隐藏凭据]",
            self.message,
        )
        message = re.sub(
            r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\)[^\s,;]+",
            "[已隐藏路径]",
            message,
        )
        return {
            "schema_version": "1.0",
            "code": code,
            "category": category,
            "message": message,
            "detail_ref": None,
            "retryability": retryability,
            "scope_refs": [],
            "caused_by_attempt": None,
        }


class NotFound(DomainError):
    def __init__(self, resource: str, resource_id: str):
        super().__init__(
            "not_found",
            f"{resource} 不存在。",
            status=404,
            details={"resource": resource, "id": resource_id},
        )


class RevisionConflict(DomainError):
    def __init__(self, expected: int, actual: int):
        super().__init__(
            "revision_conflict",
            "内容已在其他位置更新，请刷新后重试。",
            status=409,
            details={"expected_version": expected, "actual_version": actual},
        )
