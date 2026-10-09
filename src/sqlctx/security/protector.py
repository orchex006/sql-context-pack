"""Keyed, irreversible protection of sensitive values: fake, alias, generalize or redact.

Every value that can leave the service passes through `ValueProtector.protect`. Fakes are
deterministic for one key (the same real value always becomes the same fake, so joins and
grouping still line up) and are chosen by HMAC, so a fake cannot be reversed without the
key. Fake identifiers are built to be recognisably unreal: Thai IDs, phone numbers, card and
account numbers always start with `0`, which no issued Thai ID or Thai card number does.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlctx.core.enums import SensitivityClass
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
    classify_column_name,
    classify_value,
    thai_id_check_digit,
    tokens,
)

REDACTED = "[REDACTED]"

THAI_GIVEN = (
    "กมล",
    "กานดา",
    "เกศินี",
    "ขวัญใจ",
    "จันทร์เพ็ญ",
    "จิราพร",
    "ชนิดา",
    "ชัยวัฒน์",
    "ณัฐพล",
    "ดวงใจ",
    "ทองดี",
    "ธนพล",
    "นภา",
    "นิพนธ์",
    "บุญมี",
    "ปรีชา",
    "พรทิพย์",
    "พิมพ์ใจ",
    "มณีรัตน์",
    "ยุทธนา",
    "รัตนา",
    "ลำดวน",
    "วันเพ็ญ",
    "วิชัย",
    "ศรีสุดา",
    "สมพร",
    "สุดารัตน์",
    "สุริยา",
    "อนันต์",
    "อรุณี",
    "อำไพ",
    "เอกชัย",
)
THAI_FAMILY = (
    "แก้วมณี",
    "คำแสง",
    "จันทร์หอม",
    "ชัยมงคล",
    "ดีประเสริฐ",
    "ทองคำ",
    "นาคสวัสดิ์",
    "บุญเรือง",
    "ประเสริฐสุข",
    "พรหมมา",
    "ภูมิสุข",
    "มีสุข",
    "รักไทย",
    "ศรีสวัสดิ์",
    "สายทอง",
    "สุขสบาย",
    "แสงจันทร์",
    "หอมจันทร์",
    "อินทร์แก้ว",
    "อุดมสุข",
    "เจริญผล",
    "ทองมา",
    "บัวแก้ว",
    "พันธุ์ดี",
    "รุ่งเรือง",
    "วงศ์ใหญ่",
    "ศักดิ์ดี",
    "ใจงาม",
)
LATIN_GIVEN = (
    "Alex",
    "Blair",
    "Casey",
    "Dana",
    "Drew",
    "Elliot",
    "Finley",
    "Gray",
    "Harper",
    "Jamie",
    "Jordan",
    "Kai",
    "Logan",
    "Morgan",
    "Noel",
    "Parker",
    "Quinn",
    "Reese",
    "Riley",
    "Rowan",
    "Sage",
    "Taylor",
    "Skyler",
    "Avery",
)
LATIN_FAMILY = (
    "Archer",
    "Bailey",
    "Carter",
    "Dalton",
    "Ellis",
    "Fisher",
    "Garner",
    "Hayes",
    "Irving",
    "Keller",
    "Lane",
    "Marsh",
    "Nolan",
    "Porter",
    "Reed",
    "Sawyer",
    "Turner",
    "Vaughn",
    "Walker",
    "Young",
    "Barton",
    "Hollis",
    "Sutton",
    "Wade",
)

_THAI_CHAR = re.compile(r"[ก-๏]")
_GIVEN_HINTS = {"first", "fname", "firstname", "given", "givenname", "nick", "nickname"}
_FAMILY_HINTS = {"last", "lname", "lastname", "surname", "sur", "family", "familyname"}


class ValueProtector:
    def __init__(
        self,
        key: bytes,
        *,
        column_classes: Mapping[str, SensitivityClass] | None = None,
        alias: Callable[[SensitivityClass, str], str] | None = None,
    ) -> None:
        self._key = key
        # Snapshot exports supply a registry-backed alias so resume reuses the same alias.
        self._alias_hook = alias
        self._column_classes = {
            name.casefold(): sensitivity for name, sensitivity in (column_classes or {}).items()
        }
        self.counts: Counter[str] = Counter()

    # Classification -------------------------------------------------------------------

    def column_class(self, column_name: str) -> SensitivityClass:
        """Class of a column from database classification and name rules, without a value."""
        by_name = classify_column_name(column_name)
        by_database = self._column_classes.get(column_name.casefold())
        if by_database is not None and by_database != SensitivityClass.PUBLIC:
            if by_database == SensitivityClass.UNKNOWN_SENSITIVE and by_name not in {
                SensitivityClass.PUBLIC,
                SensitivityClass.FREE_TEXT,
            }:
                return by_name
            return by_database
        return by_name

    def classify(self, column_name: str, value: Any = None) -> SensitivityClass:
        column = self.column_class(column_name)
        if column != SensitivityClass.PUBLIC:
            return column
        return classify_value(value)

    # Protection -----------------------------------------------------------------------

    def protect(
        self, column_name: str, value: Any, sensitivity: SensitivityClass | None = None
    ) -> Any:
        if value is None:
            return None
        if sensitivity is None and isinstance(value, str):
            # Structured JSON is protected key by key, never classified as one opaque string.
            parsed = _json_container(value)
            if parsed is not None and self.column_class(column_name) in {
                SensitivityClass.PUBLIC,
                SensitivityClass.FREE_TEXT,
            }:
                masked = self._protect_json(parsed, column_name)
                return json.dumps(masked, ensure_ascii=False, separators=(",", ":"))
        cls = sensitivity if sensitivity is not None else self.classify(column_name, value)
        if cls in HIGH_RISK:
            return self._count("redact", REDACTED)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return value if cls == SensitivityClass.PUBLIC else self._count("redact", REDACTED)
        if cls in {SensitivityClass.PUBLIC, SensitivityClass.FREE_TEXT}:
            if not isinstance(value, str):
                return value
            parsed = _json_container(value)
            if parsed is not None:
                masked = self._protect_json(parsed, column_name)
                return json.dumps(masked, ensure_ascii=False, separators=(",", ":"))
            return self.scan_text(value)
        treatment = TREATMENT[cls]
        if cls == SensitivityClass.PERSONAL_NAME:
            return self._count(treatment, self._fake_name(column_name, str(value)))
        if cls == SensitivityClass.NATIONAL_ID:
            return self._count(treatment, self._fake_thai_id(str(value)))
        if cls in {
            SensitivityClass.PHONE,
            SensitivityClass.FINANCIAL_ACCOUNT,
            SensitivityClass.CREDIT_CARD,
        }:
            return self._count(treatment, self._fake_digits(cls, str(value)))
        if cls == SensitivityClass.EMAIL:
            return self._count(treatment, self._alias(cls, str(value), "@example.invalid"))
        if cls == SensitivityClass.USERNAME:
            return self._count(treatment, self._alias(cls, str(value), ""))
        if cls == SensitivityClass.DATE_OF_BIRTH:
            return self._count(treatment, _birth_year(value))
        if cls == SensitivityClass.PRECISE_LOCATION:
            return self._count(treatment, _coarse_location(value))
        if cls == SensitivityClass.ADDRESS:
            return self._count(treatment, "[ADDRESS]")
        return self._count("redact", REDACTED)

    def _protect_json(self, value: Any, parent: str) -> Any:
        parent_class = self.column_class(parent)
        if parent_class in HIGH_RISK:
            return self._count("redact", REDACTED)
        if isinstance(value, dict):
            return {str(key): self._protect_json(item, str(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [self._protect_json(item, parent) for item in value]
        return self.protect(parent, value)

    def scan_text(self, text: str) -> str:
        """Replace PII embedded anywhere in free text, keeping the surrounding text."""
        spans: list[tuple[int, int, str]] = []

        def collect(pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str]) -> None:
            for match in pattern.finditer(text):
                start, stop = match.span()
                if all(stop <= s or start >= e for s, e, _ in spans):
                    spans.append((start, stop, replace(match)))

        collect(
            EMAIL,
            lambda m: self._count(
                "alias", self._alias(SensitivityClass.EMAIL, m.group(0), "@example.invalid")
            ),
        )
        collect(
            CARD,
            lambda m: self._count(
                "fake", self._fake_digits(SensitivityClass.CREDIT_CARD, m.group(0))
            ),
        )
        collect(THAI_ID, lambda m: self._count("fake", self._fake_thai_id(m.group(0))))
        collect(
            PHONE,
            lambda m: self._count("fake", self._fake_digits(SensitivityClass.PHONE, m.group(0))),
        )
        collect(THAI_TITLED_NAME, lambda m: self._count("fake", self._fake_titled(m, thai=True)))
        collect(LATIN_TITLED_NAME, lambda m: self._count("fake", self._fake_titled(m, thai=False)))
        if not spans:
            return text
        result = text
        for start, stop, replacement in sorted(spans, key=lambda item: item[0], reverse=True):
            result = result[:start] + replacement + result[stop:]
        return result

    # Fakes ----------------------------------------------------------------------------

    def _digest(self, cls: SensitivityClass, raw: str) -> bytes:
        normalized = raw.strip().casefold()
        return hmac.new(self._key, f"{cls.value}|{normalized}".encode(), hashlib.sha256).digest()

    def _digits(self, cls: SensitivityClass, raw: str, count: int) -> str:
        seed = self._digest(cls, raw)
        digits = ""
        while len(digits) < count:
            digits += str(int.from_bytes(seed, "big"))
            seed = hashlib.sha256(seed).digest()
        return digits[-count:]

    def _alias(self, cls: SensitivityClass, raw: str, suffix: str) -> str:
        if self._alias_hook is not None:
            return self._alias_hook(cls, raw)
        return f"user_{self._digest(cls, raw).hex()[:10]}{suffix}"

    def _fake_digits(self, cls: SensitivityClass, raw: str) -> str:
        positions = [index for index, char in enumerate(raw) if char.isdigit()]
        if not positions:
            return REDACTED
        fresh = "0" + self._digits(cls, raw, len(positions) - 1)
        chars = list(raw)
        for index, digit in zip(positions, fresh, strict=True):
            chars[index] = digit
        return "".join(chars)

    def _fake_thai_id(self, raw: str) -> str:
        positions = [index for index, char in enumerate(raw) if char.isdigit()]
        if len(positions) != 13:
            return self._fake_digits(SensitivityClass.NATIONAL_ID, raw)
        twelve = "0" + self._digits(SensitivityClass.NATIONAL_ID, raw, 11)
        fresh = twelve + str(thai_id_check_digit(twelve))
        chars = list(raw)
        for index, digit in zip(positions, fresh, strict=True):
            chars[index] = digit
        return "".join(chars)

    def _pick(self, pool: tuple[str, ...], raw: str, salt: str) -> str:
        digest = self._digest(SensitivityClass.PERSONAL_NAME, f"{salt}|{raw}")
        return pool[int.from_bytes(digest[:4], "big") % len(pool)]

    def _fake_name(self, column_name: str, raw: str) -> str:
        thai = bool(_THAI_CHAR.search(raw))
        titled = (THAI_TITLED_NAME if thai else LATIN_TITLED_NAME).match(raw.strip())
        if titled:
            return self._fake_titled(titled, thai=thai)
        hints = set(tokens(column_name)) | {"".join(tokens(column_name))}
        given_pool, family_pool = (THAI_GIVEN, THAI_FAMILY) if thai else (LATIN_GIVEN, LATIN_FAMILY)
        given = self._pick(given_pool, raw, "given")
        family = self._pick(family_pool, raw, "family")
        if hints & _GIVEN_HINTS:
            return given
        if hints & _FAMILY_HINTS:
            return family
        return f"{given} {family}"

    def _fake_titled(self, match: re.Match[str], *, thai: bool) -> str:
        given_pool, family_pool = (THAI_GIVEN, THAI_FAMILY) if thai else (LATIN_GIVEN, LATIN_FAMILY)
        raw = match.group(0)
        title = match.group("title")
        given = self._pick(given_pool, raw, "given")
        if match.group("last"):
            return f"{title}{'' if thai else ' '}{given} {self._pick(family_pool, raw, 'family')}"
        return f"{title}{'' if thai else ' '}{given}"

    def _count(self, treatment: Treatment, value: Any) -> Any:
        self.counts[treatment] += 1
        return value


def _json_container(value: str) -> Any:
    stripped = value.lstrip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _birth_year(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return str(value.year)
    match = re.search(r"(?<![0-9])(1[89][0-9]{2}|2[0-6][0-9]{2})(?![0-9])", str(value))
    return match.group(1) if match else "[DATE_OF_BIRTH]"


def _coarse_location(value: Any) -> Any:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return "[PRECISE_LOCATION]"
    if not number.is_finite():
        return "[PRECISE_LOCATION]"
    # One decimal place is ~11 km: enough for a region, never a plot or a house.
    return str(number.quantize(Decimal("0.1")))
