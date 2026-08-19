from __future__ import annotations

import re


class ProductionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details or {}

    def to_payload(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
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
        if lower_code in {"operation_cancelled", "adapter_operation_cancelled"}:
            category = "cancelled"
        elif self.status == 409 or "conflict" in lower_code or "revision" in lower_code:
            category = "conflict"
        elif lower_code.startswith(("model_", "fetch_models", "ai_preflight_failed")):
            category = "model_transient"
        elif lower_code.startswith(("compile", "aa_compile", "storyforge_render")):
            category = "compiler"
        elif self.status >= 500:
            category = "internal"
        elif self.status in {400, 413, 422}:
            category = "validation"
        else:
            category = "environment"

        retryability = "never"
        if category == "cancelled":
            retryability = "never"
        elif category == "model_transient" or self.status in {502, 503, 504}:
            retryability = "automatic"
        elif self.status == 409 or category in {"validation", "conflict"}:
            retryability = "after_user_action"

        details = self.details if isinstance(self.details, dict) else {}
        uuid_pattern = (
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        )
        scope_refs = []
        for key in ("run_id", "work_item_id"):
            value = str(details.get(key) or "").strip()
            if re.fullmatch(uuid_pattern, value, re.IGNORECASE):
                scope_refs.append(f"{key}:{value}")
        attempt = str(details.get("attempt_id") or "").strip()
        caused_by_attempt = (
            attempt if re.fullmatch(uuid_pattern, attempt, re.IGNORECASE) else None
        )
        message = str(self)
        message = re.sub(
            r"(?i)bearer\s+[^\s,;]+|(?:api[_ -]?key|secret|token|password)\s*[=:]\s*[^\s,;]+",
            "[已隐藏凭据]",
            message,
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
            "scope_refs": scope_refs,
            "caused_by_attempt": caused_by_attempt,
        }
