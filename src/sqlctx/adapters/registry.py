"""Lazy optional-driver adapter registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from sqlctx.adapters.base import BaseDatabaseAdapter, ConnectionLike
from sqlctx.adapters.mariadb import MariaDbAdapter
from sqlctx.adapters.mysql import MySqlAdapter
from sqlctx.adapters.oracle import OracleAdapter
from sqlctx.adapters.postgres import PostgreSqlAdapter
from sqlctx.adapters.sqlserver import SqlServerAdapter
from sqlctx.core.enums import DatabaseEngine
from sqlctx.core.errors import ToolingUnavailable
from sqlctx.core.models import ResolvedConnectionProfile

SQLSERVER_ODBC_PREFERENCE = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
)


def select_sqlserver_odbc_driver(available: list[str]) -> str:
    """Choose a supported installed 64-bit driver without requiring a DSN."""
    for candidate in SQLSERVER_ODBC_PREFERENCE:
        if candidate in available:
            return candidate
    installed = ", ".join(sorted(available)) or "none detected"
    raise ToolingUnavailable(
        "A supported SQL Server ODBC driver is required. "
        f"Expected Driver 18 or 17; installed drivers: {installed}.",
        code="SQLSERVER_ODBC_DRIVER_UNAVAILABLE",
    )


def _sqlserver_connection_error(exc: Exception, driver: str) -> ToolingUnavailable:
    arguments = getattr(exc, "args", ())
    sqlstate = str(arguments[0]) if arguments else "unknown"
    detail = " ".join(str(item) for item in arguments).lower()
    if sqlstate == "IM002":
        return ToolingUnavailable(
            f"The selected installed driver {driver!r} could not be loaded; "
            "SQL Context Pack does not use a DSN.",
            code="SQLSERVER_ODBC_DRIVER_LOAD_FAILED",
        )
    if "certificate" in detail:
        return ToolingUnavailable(
            "SQL Server TLS certificate validation failed. Install/trust the issuing CA; "
            "the product will not silently disable certificate verification.",
            code="DATABASE_TLS_CERTIFICATE_UNTRUSTED",
        )
    if sqlstate == "08001":
        return ToolingUnavailable(
            "SQL Server host/instance is not reachable on the configured port. "
            "Verify the host or instance name, TCP port, SQL Server TCP/IP, firewall, and DNS.",
            code="DATABASE_HOST_UNREACHABLE",
        )
    if sqlstate == "28000" or "login failed" in detail:
        return ToolingUnavailable(
            "SQL Server rejected the configured read-only login.",
            code="DATABASE_LOGIN_FAILED",
        )
    return ToolingUnavailable(
        f"SQL Server connection failed with sanitized SQLSTATE {sqlstate}.",
        code="DATABASE_CONNECTION_FAILED",
    )


def _sqlserver_endpoint(host: str, port: int) -> str:
    """Preserve explicit named-instance or host,port endpoints without corrupting them."""
    normalized = host.strip()
    if "\\" in normalized or "," in normalized:
        return normalized
    return f"{normalized},{port}"


def _connection_factory(
    engine: DatabaseEngine,
) -> Callable[[ResolvedConnectionProfile], ConnectionLike]:
    def connect(profile: ResolvedConnectionProfile) -> ConnectionLike:
        host, port, database, username, password = profile.connection_values()
        try:
            if engine == DatabaseEngine.POSTGRES:
                import psycopg

                connection = psycopg.connect(
                    host=host,
                    port=port,
                    dbname=database,
                    user=username,
                    password=password,
                    connect_timeout=10,
                )
                connection.autocommit = False
                return cast(ConnectionLike, connection)
            if engine == DatabaseEngine.MYSQL:
                import pymysql

                return cast(
                    ConnectionLike,
                    pymysql.connect(
                        host=host,
                        port=port,
                        database=database,
                        user=username,
                        password=password,
                        connect_timeout=10,
                        read_timeout=30,
                    ),
                )
            if engine == DatabaseEngine.MARIADB:
                import mariadb

                return cast(
                    ConnectionLike,
                    mariadb.connect(
                        host=host,
                        port=port,
                        database=database,
                        user=username,
                        password=password,
                    ),
                )
            if engine == DatabaseEngine.ORACLE:
                import oracledb

                return cast(
                    ConnectionLike,
                    oracledb.connect(
                        user=username,
                        password=password,
                        dsn=f"{host}:{port}/{database}",
                    ),
                )
            if engine == DatabaseEngine.SQLSERVER:
                import pyodbc

                driver = select_sqlserver_odbc_driver(list(pyodbc.drivers()))
                endpoint = _sqlserver_endpoint(host, port)
                trust_certificate = "yes" if profile.trust_server_certificate else "no"
                connection_string = (
                    f"DRIVER={{{driver}}};"
                    f"SERVER={endpoint};DATABASE={database};UID={username};PWD={password};"
                    f"Encrypt=yes;TrustServerCertificate={trust_certificate};Connection Timeout=10;"
                )
                try:
                    return cast(ConnectionLike, pyodbc.connect(connection_string, autocommit=False))
                except pyodbc.Error as exc:
                    raise _sqlserver_connection_error(exc, driver) from exc
        except ImportError as exc:
            raise ToolingUnavailable(
                f"The optional {engine.value} driver is not installed in the selected host Python.",
                code="DATABASE_DRIVER_UNAVAILABLE",
            ) from exc
        raise ToolingUnavailable("Unsupported database engine.", code="UNSUPPORTED_ENGINE")

    return connect


ADAPTER_TYPES: dict[DatabaseEngine, type[BaseDatabaseAdapter]] = {
    DatabaseEngine.POSTGRES: PostgreSqlAdapter,
    DatabaseEngine.MYSQL: MySqlAdapter,
    DatabaseEngine.MARIADB: MariaDbAdapter,
    DatabaseEngine.ORACLE: OracleAdapter,
    DatabaseEngine.SQLSERVER: SqlServerAdapter,
}


def create_adapter(engine: DatabaseEngine) -> BaseDatabaseAdapter:
    adapter_type = ADAPTER_TYPES.get(engine)
    if adapter_type is None:
        raise ToolingUnavailable("Unsupported database engine.", code="UNSUPPORTED_ENGINE")
    return adapter_type(_connection_factory(engine))


def dialect_map() -> dict[str, str]:
    return {engine.value: adapter.dialect for engine, adapter in ADAPTER_TYPES.items()}
