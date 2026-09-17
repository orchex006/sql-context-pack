"""Reviewed-query DB-API adapter base with identifier and sampling guardrails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from fnmatch import fnmatchcase
from threading import Lock
from typing import Any, Protocol

from sqlctx.core.enums import ConstraintType, DatabaseEngine, EdgeType, ObjectType
from sqlctx.core.errors import SqlCtxError
from sqlctx.core.models import (
    ColumnMetadata,
    ConstraintMetadata,
    DatabaseCapabilities,
    DatabaseObject,
    DependencyEdge,
    ForeignKeyMetadata,
    IndexMetadata,
    ObjectRef,
    ResolvedConnectionProfile,
    SamplePage,
)


class CursorLike(Protocol):
    description: Sequence[Sequence[Any]] | None
    timeout: int

    def execute(self, query: str, parameters: Any = ...) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def fetchmany(self, size: int = ...) -> list[Any]: ...
    def fetchone(self) -> Any: ...
    def close(self) -> None: ...


class ConnectionLike(Protocol):
    def cursor(self) -> CursorLike: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


ConnectionFactory = Callable[[ResolvedConnectionProfile], ConnectionLike]


@dataclass(frozen=True)
class AdapterQueries:
    server_info: str
    schemas: str
    objects: str
    columns: str
    constraints: str
    foreign_keys: str
    table_definition: str | None
    procedure_definition: str
    routine_dependencies: str
    function_definition: str | None = None
    table_comment: str | None = None
    indexes: str | None = None
    read_only_setup: str | None = None


@dataclass(frozen=True)
class QueryColumnMetadata:
    """Driver result metadata safe for the isolated Query Data renderer."""

    name: str
    data_type: str = ""


class QueryRowStream:
    """Small DB-API cursor facade that deliberately exposes no fetchall operation."""

    def __init__(self, cursor: CursorLike) -> None:
        self._cursor = cursor
        self.columns = tuple(
            QueryColumnMetadata(
                name=str(item[0]),
                data_type=str(item[1]) if len(item) > 1 and item[1] is not None else "",
            )
            for item in (cursor.description or [])
        )

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return [tuple(row) for row in self._cursor.fetchmany(size)]


class BaseDatabaseAdapter:
    engine: DatabaseEngine
    dialect: str
    queries: AdapterQueries
    parameter_token = "?"
    quote_left = '"'
    quote_right = '"'
    supports_cancel = True
    supports_consistent_snapshot = False
    native_definition_tables = False

    def __init__(
        self, connection_factory: ConnectionFactory, *, statement_timeout_seconds: int = 30
    ) -> None:
        self.connection_factory = connection_factory
        self.statement_timeout_seconds = statement_timeout_seconds
        self._catalog: dict[str, ObjectRef] = {}
        self._active_cursor: CursorLike | None = None
        self._cursor_lock = Lock()

    def capabilities(self) -> DatabaseCapabilities:
        return DatabaseCapabilities(
            engine=self.engine,
            sqlfluff_dialect=self.dialect,
            supports_query_cancel=self.supports_cancel,
            supports_consistent_snapshot=self.supports_consistent_snapshot,
        )

    def quote_identifier(self, identifier: str) -> str:
        if not identifier or any(char in identifier for char in ("\x00", "\r", "\n", ".")):
            raise SqlCtxError("INVALID_IDENTIFIER", "Database identifier is invalid.")
        escaped = identifier.replace(self.quote_right, self.quote_right * 2)
        return f"{self.quote_left}{escaped}{self.quote_right}"

    def parameter_placeholder(self, position: int) -> str:
        """Return the driver placeholder for a one-based source literal position."""
        if self.parameter_token == ":":
            return f":{position}"
        return self.parameter_token

    def assert_query_read_only(
        self, profile: ResolvedConnectionProfile, tables: tuple[ObjectRef, ...]
    ) -> None:
        """Use transaction-level read-only setup for engines that enforce it."""

    @contextmanager
    def open_query(
        self,
        profile: ResolvedConnectionProfile,
        query: str,
        parameters: tuple[Any, ...],
    ) -> Iterator[QueryRowStream]:
        """Execute one already-validated parameterized SELECT with deterministic cleanup."""
        connection = self.connection_factory(profile)
        cursor = connection.cursor()
        with self._cursor_lock:
            self._active_cursor = cursor
        try:
            if self.queries.read_only_setup:
                cursor.execute(self.queries.read_only_setup)
            if hasattr(cursor, "timeout"):
                cursor.timeout = self.statement_timeout_seconds
            cursor.execute(query, self._parameters(*parameters))
            yield QueryRowStream(cursor)
        except SqlCtxError:
            raise
        except Exception as exc:
            raise SqlCtxError(
                "QUERY_EXECUTION_FAILED",
                "The read-only query could not be completed.",
                status_code=503,
            ) from exc
        finally:
            with suppress(Exception):
                connection.rollback()
            with self._cursor_lock:
                self._active_cursor = None
            try:
                cursor.close()
            finally:
                connection.close()

    def _assert_allowed(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> None:
        if ref.schema_name not in profile.allowed_schemas:
            raise SqlCtxError(
                "SCHEMA_NOT_ALLOWED",
                "Object schema is outside the profile allowlist.",
                status_code=403,
            )
        if ref.object_type not in profile.allowed_object_types:
            raise SqlCtxError(
                "OBJECT_TYPE_NOT_ALLOWED",
                "Object type is outside the profile allowlist.",
                status_code=403,
            )
        if self._catalog.get(ref.object_id) != ref:
            raise SqlCtxError(
                "OBJECT_NOT_DISCOVERED",
                "Object was not found in the discovered catalog.",
                status_code=404,
            )

    def _execute(
        self, profile: ResolvedConnectionProfile, query: str, parameters: Any = None
    ) -> list[dict[str, Any]]:
        connection = self.connection_factory(profile)
        cursor = connection.cursor()
        with self._cursor_lock:
            self._active_cursor = cursor
        try:
            if self.queries.read_only_setup:
                cursor.execute(self.queries.read_only_setup)
            cursor.execute(query, parameters or ())
            rows = cursor.fetchall()
            columns = [str(item[0]).lower() for item in (cursor.description or [])]
            return [dict(zip(columns, row, strict=False)) for row in rows]
        except SqlCtxError:
            raise
        except Exception as exc:
            with suppress(Exception):
                connection.rollback()
            raise SqlCtxError(
                "DATABASE_OPERATION_FAILED",
                f"Read-only {self.engine.value} metadata operation failed.",
                retryable=False,
                status_code=503,
            ) from exc
        finally:
            with self._cursor_lock:
                self._active_cursor = None
            try:
                cursor.close()
            finally:
                connection.close()

    def _execute_write(
        self, profile: ResolvedConnectionProfile, query: str, parameters: Any = None
    ) -> int:
        """Execute one reviewed adapter-owned mutation and commit it atomically."""
        connection = self.connection_factory(profile)
        cursor = connection.cursor()
        with self._cursor_lock:
            self._active_cursor = cursor
        try:
            if hasattr(cursor, "timeout"):
                cursor.timeout = self.statement_timeout_seconds
            cursor.execute(query, parameters or ())
            rowcount = int(getattr(cursor, "rowcount", -1))
            connection.commit()
            return rowcount
        except SqlCtxError:
            with suppress(Exception):
                connection.rollback()
            raise
        except Exception as exc:
            with suppress(Exception):
                connection.rollback()
            raise SqlCtxError(
                "DATABASE_WRITE_FAILED",
                f"Reviewed {self.engine.value} write operation failed.",
                status_code=503,
            ) from exc
        finally:
            with self._cursor_lock:
                self._active_cursor = None
            try:
                cursor.close()
            finally:
                connection.close()

    def _execute_write_returning(
        self, profile: ResolvedConnectionProfile, query: str, parameters: Any = None
    ) -> list[dict[str, Any]]:
        """Execute one reviewed mutation whose fixed statement returns safe metadata rows."""
        connection = self.connection_factory(profile)
        cursor = connection.cursor()
        with self._cursor_lock:
            self._active_cursor = cursor
        try:
            if hasattr(cursor, "timeout"):
                cursor.timeout = self.statement_timeout_seconds
            cursor.execute(query, parameters or ())
            rows = cursor.fetchall()
            columns = [str(item[0]).lower() for item in (cursor.description or [])]
            connection.commit()
            return [dict(zip(columns, row, strict=False)) for row in rows]
        except SqlCtxError:
            with suppress(Exception):
                connection.rollback()
            raise
        except Exception as exc:
            with suppress(Exception):
                connection.rollback()
            raise SqlCtxError(
                "DATABASE_WRITE_FAILED",
                f"Reviewed {self.engine.value} write operation failed.",
                status_code=503,
            ) from exc
        finally:
            with self._cursor_lock:
                self._active_cursor = None
            try:
                cursor.close()
            finally:
                connection.close()

    def test_connection(self, profile: ResolvedConnectionProfile) -> None:
        self.get_server_info(profile)

    def get_server_info(self, profile: ResolvedConnectionProfile) -> Mapping[str, Any]:
        rows = self._execute(profile, self.queries.server_info)
        return rows[0] if rows else {}

    def list_schemas(self, profile: ResolvedConnectionProfile) -> list[str]:
        return [
            schema
            for schema in self.list_visible_schemas(profile)
            if schema in profile.allowed_schemas
        ]

    def list_visible_schemas(self, profile: ResolvedConnectionProfile) -> list[str]:
        """List metadata-visible schema names for owner scope review."""
        rows = self._execute(profile, self.queries.schemas)
        return [str(row["schema_name"]) for row in rows]

    def discover_objects(self, profile: ResolvedConnectionProfile) -> Iterable[ObjectRef]:
        result: list[ObjectRef] = []
        allowed_types = set(profile.allowed_object_types)
        for schema in profile.allowed_schemas:
            for row in self._execute(profile, self.queries.objects, self._parameters(schema)):
                object_type = self._object_type(str(row["object_type"]))
                if object_type not in allowed_types:
                    continue
                name = str(row["object_name"])
                if any(
                    fnmatchcase(name.lower(), pattern.lower())
                    for pattern in profile.excluded_object_patterns
                ):
                    continue
                ref = ObjectRef(
                    object_id=f"{object_type.value}:{schema}.{name}",
                    engine=self.engine,
                    schema_name=schema,
                    object_name=name,
                    object_type=object_type,
                )
                self._catalog[ref.object_id] = ref
                result.append(ref)
        return result

    def schema_fingerprint(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> str:
        """Return a safe metadata-only cache validator for requested schema scope."""
        requested_schemas = set(schemas)
        requested_types = set(object_types)
        payload = sorted(
            (ref.schema_name, ref.object_type.value, ref.object_name)
            for ref in self.discover_objects(profile)
            if ref.schema_name in requested_schemas and ref.object_type in requested_types
        )
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def object_fingerprints(
        self,
        profile: ResolvedConnectionProfile,
        schemas: list[str],
        object_types: list[ObjectType],
    ) -> dict[str, str]:
        """Return safe per-object validators when the engine can detect definition changes."""
        return {}

    @staticmethod
    def _object_type(raw: str) -> ObjectType:
        normalized = raw.lower()
        if normalized in {"table", "base table", "u"}:
            return ObjectType.TABLE
        if normalized in {"procedure", "stored procedure", "p"}:
            return ObjectType.PROCEDURE
        if normalized in {"function", "fn", "if", "tf"}:
            return ObjectType.FUNCTION
        raise SqlCtxError("UNSUPPORTED_OBJECT_TYPE", "Adapter returned an unsupported object type.")

    def get_table_columns(
        self, profile: ResolvedConnectionProfile, ref: ObjectRef
    ) -> list[ColumnMetadata]:
        self._assert_allowed(profile, ref)
        rows = self._execute(
            profile, self.queries.columns, self._parameters(ref.schema_name, ref.object_name)
        )
        return [
            ColumnMetadata(
                name=str(row["column_name"]),
                data_type=str(row["data_type"]),
                nullable=self._as_bool(row.get("is_nullable", True)),
                ordinal=int(row["ordinal_position"]),
            )
            for row in rows
        ]

    def get_constraints(
        self, profile: ResolvedConnectionProfile, ref: ObjectRef
    ) -> list[ConstraintMetadata]:
        self._assert_allowed(profile, ref)
        rows = self._execute(
            profile, self.queries.constraints, self._parameters(ref.schema_name, ref.object_name)
        )
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (str(row["constraint_name"]), str(row["constraint_type"]).lower())
            item = grouped.setdefault(key, {"columns": [], "expression": None})
            if row.get("column_name") is not None:
                item["columns"].append(str(row["column_name"]))
            if row.get("expression") is not None:
                item["expression"] = str(row["expression"])
        mapping = {
            "primary key": ConstraintType.PRIMARY_KEY,
            "p": ConstraintType.PRIMARY_KEY,
            "unique": ConstraintType.UNIQUE,
            "u": ConstraintType.UNIQUE,
            "check": ConstraintType.CHECK,
            "c": ConstraintType.CHECK,
            "foreign key": ConstraintType.FOREIGN_KEY,
            "r": ConstraintType.FOREIGN_KEY,
        }
        return [
            ConstraintMetadata(
                name=name,
                constraint_type=mapping.get(kind, ConstraintType.CHECK),
                columns=value["columns"],
                expression=value["expression"],
            )
            for (name, kind), value in grouped.items()
        ]

    def get_table_comment(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str | None:
        self._assert_allowed(profile, ref)
        if not self.queries.table_comment:
            return None
        rows = self._execute(
            profile, self.queries.table_comment, self._parameters(ref.schema_name, ref.object_name)
        )
        value = rows[0].get("description") if rows else None
        return str(value) if value not in {None, ""} else None

    def get_indexes(
        self, profile: ResolvedConnectionProfile, ref: ObjectRef
    ) -> list[IndexMetadata]:
        self._assert_allowed(profile, ref)
        if not self.queries.indexes:
            return []
        rows = self._execute(
            profile, self.queries.indexes, self._parameters(ref.schema_name, ref.object_name)
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.get("index_name") in {None, ""}:
                continue
            name = str(row["index_name"])
            item = grouped.setdefault(
                name,
                {
                    "unique": self._as_bool(row.get("is_unique", False)),
                    "primary": self._as_bool(row.get("is_primary", False)),
                    "keys": [],
                    "included": [],
                },
            )
            column = row.get("column_name")
            if column is None:
                continue
            target = (
                item["included"] if self._as_bool(row.get("is_included", False)) else item["keys"]
            )
            target.append((int(row.get("column_order") or len(target) + 1), str(column)))
        return [
            IndexMetadata(
                name=name,
                unique=value["unique"],
                primary=value["primary"],
                key_columns=[column for _, column in sorted(value["keys"])],
                included_columns=[column for _, column in sorted(value["included"])],
            )
            for name, value in grouped.items()
        ]

    def get_foreign_keys(
        self, profile: ResolvedConnectionProfile, ref: ObjectRef
    ) -> list[ForeignKeyMetadata]:
        self._assert_allowed(profile, ref)
        rows = self._execute(
            profile, self.queries.foreign_keys, self._parameters(ref.schema_name, ref.object_name)
        )
        return [
            ForeignKeyMetadata(
                name=str(row["constraint_name"]),
                source_object_id=ref.object_id,
                source_columns=[str(row["source_column"])],
                target_object_id=f"table:{row['target_schema']}.{row['target_table']}",
                target_columns=[str(row["target_column"])],
            )
            for row in rows
        ]

    def get_table_definition(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str:
        self._assert_allowed(profile, ref)
        if self.queries.table_definition:
            rows = self._execute(
                profile,
                self.queries.table_definition,
                self._parameters(ref.schema_name, ref.object_name),
            )
            if rows and rows[0].get("definition"):
                return str(rows[0]["definition"])
        columns = self.get_table_columns(profile, ref)
        body = ",\n".join(
            f"    {self.quote_identifier(column.name)} {column.data_type}"
            f"{' NULL' if column.nullable else ' NOT NULL'}"
            for column in columns
        )
        return (
            f"CREATE TABLE {self.quote_identifier(ref.schema_name)}."
            f"{self.quote_identifier(ref.object_name)} (\n{body}\n);"
        )

    def get_procedure_definition(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str:
        self._assert_allowed(profile, ref)
        rows = self._execute(
            profile,
            self.queries.procedure_definition,
            self._parameters(ref.schema_name, ref.object_name),
        )
        if not rows or not rows[0].get("definition"):
            raise SqlCtxError(
                "DEFINITION_UNAVAILABLE", "Stored procedure definition is unavailable."
            )
        return str(rows[0]["definition"])

    def get_function_definition(self, profile: ResolvedConnectionProfile, ref: ObjectRef) -> str:
        self._assert_allowed(profile, ref)
        if not self.queries.function_definition:
            raise SqlCtxError(
                "DEFINITION_UNAVAILABLE",
                "This engine adapter cannot read stored function definitions.",
            )
        rows = self._execute(
            profile,
            self.queries.function_definition,
            self._parameters(ref.schema_name, ref.object_name),
        )
        if not rows or not rows[0].get("definition"):
            raise SqlCtxError(
                "DEFINITION_UNAVAILABLE", "Stored function definition is unavailable."
            )
        return str(rows[0]["definition"])

    def get_sample_rows(
        self,
        profile: ResolvedConnectionProfile,
        ref: ObjectRef,
        requested: int,
        *,
        columns: list[ColumnMetadata] | None = None,
        constraints: list[ConstraintMetadata] | None = None,
    ) -> SamplePage:
        self._assert_allowed(profile, ref)
        if requested < 10:
            raise SqlCtxError("SAMPLE_TARGET_TOO_LOW", "At least 10 sample rows must be requested.")
        if requested > 20:
            raise SqlCtxError("SAMPLE_TARGET_TOO_HIGH", "Requested rows exceed the maximum of 20.")
        columns = columns if columns is not None else self.get_table_columns(profile, ref)
        constraints = constraints if constraints is not None else self.get_constraints(profile, ref)
        primary = next(
            (
                item.columns
                for item in constraints
                if item.constraint_type == ConstraintType.PRIMARY_KEY
            ),
            None,
        )
        unique = next(
            (item.columns for item in constraints if item.constraint_type == ConstraintType.UNIQUE),
            None,
        )
        order = primary or unique or ([columns[0].name] if columns else [])
        deterministic = bool(primary or unique)
        query = self.sample_query(ref, order, requested)
        rows = self._execute(profile, query)
        values = [
            [
                self._bounded_value(
                    row.get(column.name.lower(), row.get(column.name)),
                    column_name=column.name,
                    data_type=column.data_type,
                )
                for column in columns
            ]
            for row in rows[:requested]
        ]
        actual = len(values)
        return SamplePage(
            object_id=ref.object_id,
            columns=[column.name for column in columns],
            rows=values,
            requested_count=requested,
            actual_count=actual,
            shortage_reason=("source_returned_fewer_rows" if actual < requested else None),
            deterministic=deterministic,
            sampling_order=order,
        )

    def get_all_rows(
        self,
        profile: ResolvedConnectionProfile,
        ref: ObjectRef,
        *,
        columns: list[ColumnMetadata] | None = None,
        constraints: list[ConstraintMetadata] | None = None,
        page_size: int = 250,
    ) -> SamplePage:
        self._assert_allowed(profile, ref)
        columns = columns if columns is not None else self.get_table_columns(profile, ref)
        constraints = constraints if constraints is not None else self.get_constraints(profile, ref)
        primary = next(
            (
                item.columns
                for item in constraints
                if item.constraint_type == ConstraintType.PRIMARY_KEY
            ),
            None,
        )
        unique = next(
            (item.columns for item in constraints if item.constraint_type == ConstraintType.UNIQUE),
            None,
        )
        order = primary or unique or ([columns[0].name] if columns else [])
        values: list[list[Any]] = []
        offset = 0
        pages = 0
        while True:
            rows = self._execute(profile, self.sample_page_query(ref, order, page_size, offset))
            pages += 1
            values.extend(
                [
                    [
                        self._bounded_value(
                            row.get(column.name.lower(), row.get(column.name)),
                            column_name=column.name,
                            data_type=column.data_type,
                        )
                        for column in columns
                    ]
                    for row in rows
                ]
            )
            if len(rows) < page_size:
                break
            offset += page_size
        return SamplePage(
            object_id=ref.object_id,
            columns=[column.name for column in columns],
            rows=values,
            requested_count=len(values),
            actual_count=len(values),
            deterministic=bool(primary or unique),
            sampling_order=order,
            all_rows=True,
            complete=True,
            page_count=pages,
        )

    def sample_query(self, ref: ObjectRef, order: list[str], requested: int) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "1"
        return f"SELECT * FROM {qualified} ORDER BY {order_sql} LIMIT {requested}"

    def sample_page_query(
        self, ref: ObjectRef, order: list[str], page_size: int, offset: int
    ) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "1"
        return f"SELECT * FROM {qualified} ORDER BY {order_sql} LIMIT {page_size} OFFSET {offset}"

    def get_routine_dependencies(
        self, profile: ResolvedConnectionProfile, ref: ObjectRef
    ) -> list[DependencyEdge]:
        self._assert_allowed(profile, ref)
        rows = self._execute(
            profile,
            self.queries.routine_dependencies,
            self._parameters(ref.schema_name, ref.object_name),
        )
        return [
            DependencyEdge(
                source_object_id=ref.object_id,
                target_object_id=str(row["target_object_id"]),
                edge_type=EdgeType(str(row.get("edge_type", EdgeType.ROUTINE_READ.value))),
                evidence=["native_catalog"],
            )
            for row in rows
        ]

    def extract_object(
        self, profile: ResolvedConnectionProfile, object_ref: ObjectRef
    ) -> DatabaseObject:
        self._assert_allowed(profile, object_ref)
        if object_ref.object_type == ObjectType.TABLE:
            columns = self.get_table_columns(profile, object_ref)
            constraints = self.get_constraints(profile, object_ref)
            foreign_keys = self.get_foreign_keys(profile, object_ref)
            indexes = self.get_indexes(profile, object_ref)
            comment = self.get_table_comment(profile, object_ref)
            if self.queries.table_definition:
                definition = self.get_table_definition(profile, object_ref)
            else:
                definition = self._render_table_definition(
                    object_object=object_ref,
                    columns=columns,
                    constraints=constraints,
                    foreign_keys=foreign_keys,
                    indexes=indexes,
                    comment=comment,
                )
        else:
            definition = (
                self.get_function_definition(profile, object_ref)
                if object_ref.object_type == ObjectType.FUNCTION
                else self.get_procedure_definition(profile, object_ref)
            )
            columns, constraints, foreign_keys = [], [], []
            indexes, comment = [], None
        return DatabaseObject(
            ref=object_ref,
            columns=columns,
            constraints=constraints,
            foreign_keys=foreign_keys,
            sanitized_definition=definition,
            native_comment=comment,
            indexes=indexes,
        )

    def _render_table_definition(
        self,
        *,
        object_object: ObjectRef,
        columns: list[ColumnMetadata],
        constraints: list[ConstraintMetadata],
        foreign_keys: list[ForeignKeyMetadata],
        indexes: list[IndexMetadata],
        comment: str | None,
    ) -> str:
        entries = [
            f"    {self.quote_identifier(column.name)} {column.data_type}"
            f"{' NULL' if column.nullable else ' NOT NULL'}"
            for column in columns
        ]
        for constraint in constraints:
            quoted = ", ".join(self.quote_identifier(column) for column in constraint.columns)
            if constraint.constraint_type == ConstraintType.PRIMARY_KEY:
                entries.append(
                    f"    CONSTRAINT {self.quote_identifier(constraint.name)} PRIMARY KEY ({quoted})"
                )
            elif constraint.constraint_type == ConstraintType.UNIQUE:
                entries.append(
                    f"    CONSTRAINT {self.quote_identifier(constraint.name)} UNIQUE ({quoted})"
                )
            elif constraint.constraint_type == ConstraintType.CHECK and constraint.expression:
                entries.append(
                    f"    CONSTRAINT {self.quote_identifier(constraint.name)} CHECK ({constraint.expression})"
                )
        for foreign_key in foreign_keys:
            source = ", ".join(
                self.quote_identifier(column) for column in foreign_key.source_columns
            )
            target_schema_table = foreign_key.target_object_id.removeprefix("table:").split(".", 1)
            if len(target_schema_table) != 2:
                continue
            target = ", ".join(
                self.quote_identifier(column) for column in foreign_key.target_columns
            )
            entries.append(
                f"    CONSTRAINT {self.quote_identifier(foreign_key.name)} FOREIGN KEY ({source}) "
                f"REFERENCES {self.quote_identifier(target_schema_table[0])}."
                f"{self.quote_identifier(target_schema_table[1])} ({target})"
            )
        header = f"-- Description: {comment}\n" if comment else ""
        create = (
            f"{header}CREATE TABLE {self.quote_identifier(object_object.schema_name)}."
            f"{self.quote_identifier(object_object.object_name)} (\n" + ",\n".join(entries) + "\n);"
        )
        index_sql = []
        for index in indexes:
            if index.primary or not index.key_columns:
                continue
            unique = "UNIQUE " if index.unique else ""
            keys = ", ".join(self.quote_identifier(column) for column in index.key_columns)
            index_sql.append(
                f"CREATE {unique}INDEX {self.quote_identifier(index.name)} ON "
                f"{self.quote_identifier(object_object.schema_name)}."
                f"{self.quote_identifier(object_object.object_name)} ({keys});"
            )
        return create + (("\n\n" + "\n".join(index_sql)) if index_sql else "")

    def cancel(self) -> bool:
        with self._cursor_lock:
            cursor = self._active_cursor
        cancel = getattr(cursor, "cancel", None) if cursor else None
        if callable(cancel):
            cancel()
            return True
        return False

    def _parameters(self, *values: Any) -> Any:
        return values

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.lower() in {"yes", "y", "true", "1"}
        return bool(value)

    @staticmethod
    def _bounded_value(value: Any, *, column_name: str = "", data_type: str = "") -> Any:
        from sqlctx.query_data.values import bounded_text_value

        return bounded_text_value(value, column_name=column_name, data_type=data_type)
