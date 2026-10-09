"""Fail-closed sensitive-data protection for snapshots, and SQL literal scanning."""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

from sqlctx.core.enums import SensitivityClass
from sqlctx.core.models import MaskingDecision
from sqlctx.security.protector import REDACTED, ValueProtector
from sqlctx.security.runtime import EncryptedSnapshotSecretStore
from sqlctx.security.sensitivity import (
    CARD,
    EMAIL,
    HIGH_RISK,
    LATIN_TITLED_NAME,
    PHONE,
    THAI_ID,
    THAI_TITLED_NAME,
    TREATMENT,
    Treatment,
)

__all__ = [
    "HIGH_RISK",
    "DeterministicMaskingEngine",
    "column_marker",
    "redact_pii_text",
    "scan_and_redact_pii_literals",
    "scan_and_redact_sql_literals",
]


CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


def _crockford(data: bytes) -> str:
    value = int.from_bytes(data, "big")
    chars: list[str] = []
    while value:
        value, remainder = divmod(value, 32)
        chars.append(CROCKFORD[remainder])
    return ("".join(reversed(chars)) or "0").rjust(52, "0")


def column_marker(sensitivity: SensitivityClass) -> str:
    """Visible marker for a protected column, e.g. `⟨FAKE:PERSONAL_NAME⟩`; empty if public."""
    treatment = TREATMENT.get(sensitivity, "redact")
    if treatment == "public":
        return ""
    label = {
        "fake": "FAKE",
        "alias": "ALIAS",
        "generalize": "GENERALIZED",
        "redact": "REDACTED",
        "scan": "SCANNED",
    }[treatment]
    return f"⟨{label}:{sensitivity.value.upper()}⟩"


class DeterministicMaskingEngine:
    """Snapshot-scoped protection: one encrypted key per catalog snapshot keeps fakes stable."""

    def __init__(self, secrets_store: EncryptedSnapshotSecretStore) -> None:
        self.secrets_store = secrets_store
        self._protectors: dict[str, ValueProtector] = {}

    def classify(
        self,
        column_name: str,
        value: Any,
        *,
        owner_override: SensitivityClass | None = None,
        database_classification: SensitivityClass | None = None,
    ) -> SensitivityClass:
        if owner_override is not None:
            return owner_override
        classes = (
            {column_name: database_classification} if database_classification is not None else {}
        )
        return ValueProtector(b"", column_classes=classes).classify(column_name, value)

    def protector(
        self,
        snapshot_id: str,
        column_classes: Mapping[str, SensitivityClass] | None = None,
    ) -> ValueProtector:
        def alias(cls: SensitivityClass, raw: str) -> str:
            return self._alias(snapshot_id, cls, raw)

        if column_classes:
            return ValueProtector(
                self.secrets_store.get_or_create_key(snapshot_id),
                column_classes=column_classes,
                alias=alias,
            )
        cached = self._protectors.get(snapshot_id)
        if cached is None:
            cached = ValueProtector(self.secrets_store.get_or_create_key(snapshot_id), alias=alias)
            self._protectors[snapshot_id] = cached
        return cached

    def _alias(self, snapshot_id: str, sensitivity: SensitivityClass, raw_value: str) -> str:
        """Crockford alias with a protected per-snapshot registry for resume and collisions."""
        key = self.secrets_store.get_or_create_key(snapshot_id)
        normalized = raw_value.strip().casefold().encode()
        digest = hmac.new(key, normalized, hashlib.sha256).digest()
        digest_hex = digest.hex()
        encoded = _crockford(digest)
        registry = self.secrets_store.load_registry(snapshot_id)
        reverse = {alias: known_digest for known_digest, alias in registry.items()}
        length = 10
        while True:
            alias = f"user_{encoded[:length]}"
            if sensitivity == SensitivityClass.EMAIL:
                alias += "@example.invalid"
            if alias not in reverse or reverse[alias] == digest_hex:
                break
            length += 2
        registry[digest_hex] = alias
        self.secrets_store.save_registry(snapshot_id, registry)
        return alias

    def protect_rows(
        self,
        *,
        snapshot_id: str,
        columns: list[str],
        rows: list[list[Any]],
        column_classes: Mapping[str, SensitivityClass] | None = None,
    ) -> tuple[list[list[Any]], dict[str, str]]:
        """Protect sample rows and return each protected column's visible marker."""
        protector = self.protector(snapshot_id, column_classes)
        protected = [
            [protector.protect(column, value) for column, value in zip(columns, row, strict=True)]
            for row in rows
        ]
        markers: dict[str, str] = {}
        for index, column in enumerate(columns):
            marker = column_marker(protector.column_class(column))
            if not marker and any(
                original[index] != masked[index]
                for original, masked in zip(rows, protected, strict=True)
            ):
                marker = "⟨SCANNED⟩"
            if marker:
                markers[column] = marker
        return protected, markers

    def mask(
        self,
        *,
        column_name: str,
        value: Any,
        snapshot_id: str,
        owner_override: SensitivityClass | None = None,
        database_classification: SensitivityClass | None = None,
    ) -> MaskingDecision:
        sensitivity = self.classify(
            column_name,
            value,
            owner_override=owner_override,
            database_classification=database_classification,
        )
        if value is None or sensitivity == SensitivityClass.PUBLIC:
            masked = (
                value
                if value is None
                else self.protector(snapshot_id).protect(column_name, value, sensitivity)
            )
            return MaskingDecision(
                sensitivity=sensitivity,
                action="keep" if masked == value else "scan",
                masked_value=masked,
                rule="public" if masked == value else "embedded-pii-scan",
            )
        treatment: Treatment = TREATMENT.get(sensitivity, "redact")
        masked = self.protector(snapshot_id).protect(column_name, value, sensitivity)
        if masked == REDACTED:
            treatment = "redact"
        return MaskingDecision(
            sensitivity=sensitivity,
            action="keep" if treatment == "public" else treatment,
            masked_value=masked,
            rule={
                "redact": "high-risk-secret" if sensitivity in HIGH_RISK else "fail-closed",
                "fake": "snapshot-hmac-fake",
                "alias": "snapshot-hmac",
                "generalize": "strict-generalization",
                "scan": "embedded-pii-scan",
                "public": "public",
            }[treatment],
        )


_SQL_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(password|passwd|pwd|secret|api[_-]?key|token)\s*=\s*(['\"])(?!\[REDACTED\]\2)(.*?)\2"
    ),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~-]+"),
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.S,
    ),
)


def scan_and_redact_sql_literals(sql: str) -> tuple[str, int]:
    cleaned = sql
    count = 0
    for pattern in _SQL_SECRET_PATTERNS:
        if pattern.groups >= 3:
            cleaned, replaced = pattern.subn(
                lambda match: f"{match.group(1)}='[REDACTED]'", cleaned
            )
        else:
            cleaned, replaced = pattern.subn("[REDACTED]", cleaned)
        count += replaced
    return cleaned, count


_STRING_LITERAL = re.compile(r"N?'(?:[^']|'')*'")
_PII_LITERAL_PATTERNS = (
    (EMAIL, "EMAIL"),
    (CARD, "CREDIT_CARD"),
    (THAI_ID, "NATIONAL_ID"),
    (PHONE, "PHONE"),
    (THAI_TITLED_NAME, "PERSONAL_NAME"),
    (LATIN_TITLED_NAME, "PERSONAL_NAME"),
)


def scan_and_redact_pii_literals(sql: str) -> tuple[str, int]:
    """Replace personal data inside SQL string literals with visible `[REDACTED:<CLASS>]` marks.

    Routine bodies get marks rather than fakes: a fake value in code could be redeployed and
    silently change behaviour, while a mark is obviously not a real value.
    """
    count = 0

    def scrub(literal: re.Match[str]) -> str:
        nonlocal count
        text, replaced = redact_pii_text(literal.group(0))
        count += replaced
        return text

    return _STRING_LITERAL.sub(scrub, sql), count


def redact_pii_text(text: str) -> tuple[str, int]:
    """Replace personal data anywhere in prose (e.g. a table description) with marks."""
    count = 0
    for pattern, label in _PII_LITERAL_PATTERNS:
        text, replaced = pattern.subn(f"[REDACTED:{label}]", text)
        count += replaced
    return text, count
