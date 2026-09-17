"""Validated deterministic category rules and owner overrides."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sqlctx.core.errors import SqlCtxError


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    exact_names: list[str] = Field(default_factory=list)
    prefixes: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    comment_keywords: list[str] = Field(default_factory=list)
    column_keywords: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
            raise ValueError("category name must be a safe lowercase path segment")
        return value


class CategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    categories: list[CategoryRule] = Field(min_length=1)

    @property
    def names(self) -> set[str]:
        return {item.name for item in self.categories}


class OverrideConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    overrides: dict[str, str] = Field(default_factory=dict)


class CategoryRuleRepository:
    def __init__(self, rules_path: Path, overrides_path: Path) -> None:
        self.rules_path = rules_path
        self.overrides_path = overrides_path

    @staticmethod
    def _read(path: Path) -> object:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise SqlCtxError(
                "CATEGORY_CONFIG_INVALID", "Category configuration is unreadable."
            ) from exc

    def load(self) -> tuple[CategoryConfig, OverrideConfig]:
        rules = CategoryConfig.model_validate(self._read(self.rules_path))
        overrides = OverrideConfig.model_validate(self._read(self.overrides_path))
        unknown = set(overrides.overrides.values()) - rules.names
        if unknown:
            raise SqlCtxError(
                "CATEGORY_CONFIG_INVALID", "Owner overrides reference unknown categories."
            )
        return rules, overrides

    def save_override(self, object_id: str, category: str) -> None:
        rules, overrides = self.load()
        if category not in rules.names:
            raise SqlCtxError(
                "UNKNOWN_CATEGORY", "Owner resolution references an unknown category."
            )
        overrides.overrides[object_id] = category
        payload = yaml.safe_dump(overrides.model_dump(mode="json"), sort_keys=True)
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.overrides_path.with_suffix(".yaml.new")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(self.overrides_path)
