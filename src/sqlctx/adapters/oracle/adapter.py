"""Oracle adapter with runtime privilege negotiation."""

from sqlctx.adapters.base import AdapterQueries, BaseDatabaseAdapter
from sqlctx.core.enums import DatabaseEngine
from sqlctx.core.models import DatabaseCapabilities, ObjectRef, ResolvedConnectionProfile


class OracleAdapter(BaseDatabaseAdapter):
    engine = DatabaseEngine.ORACLE
    dialect = "oracle"
    parameter_token = ":"
    supports_cancel = True
    queries = AdapterQueries(
        server_info="SELECT version FROM product_component_version WHERE product LIKE 'Oracle%' FETCH FIRST 1 ROW ONLY",
        schemas="SELECT username AS schema_name FROM all_users ORDER BY username",
        objects="""
            SELECT object_name,
                   CASE object_type
                        WHEN 'TABLE' THEN 'table'
                        WHEN 'FUNCTION' THEN 'function'
                        ELSE 'procedure'
                   END AS object_type
              FROM all_objects
             WHERE owner = :1 AND object_type IN ('TABLE', 'PROCEDURE', 'FUNCTION')
             ORDER BY object_type, object_name
        """,
        columns="""
            SELECT column_name, data_type,
                   CASE nullable WHEN 'Y' THEN 1 ELSE 0 END AS is_nullable,
                   column_id AS ordinal_position
              FROM all_tab_columns
             WHERE owner = :1 AND table_name = :2 ORDER BY column_id
        """,
        constraints="""
            SELECT c.constraint_name, c.constraint_type, cc.column_name,
                   c.search_condition_vc AS expression
              FROM all_constraints c
              LEFT JOIN all_cons_columns cc
                ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
             WHERE c.owner = :1 AND c.table_name = :2
             ORDER BY c.constraint_name, cc.position
        """,
        foreign_keys="""
            SELECT c.constraint_name, cc.column_name AS source_column,
                   rc.owner AS target_schema, rc.table_name AS target_table,
                   rcc.column_name AS target_column
              FROM all_constraints c
              JOIN all_cons_columns cc ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name
              JOIN all_constraints rc ON rc.owner = c.r_owner AND rc.constraint_name = c.r_constraint_name
              JOIN all_cons_columns rcc ON rcc.owner = rc.owner AND rcc.constraint_name = rc.constraint_name
                                      AND rcc.position = cc.position
             WHERE c.constraint_type = 'R' AND c.owner = :1 AND c.table_name = :2
        """,
        table_definition="SELECT DBMS_METADATA.GET_DDL('TABLE', :2, :1) AS definition FROM dual",
        procedure_definition="SELECT DBMS_METADATA.GET_DDL('PROCEDURE', :2, :1) AS definition FROM dual",
        function_definition="SELECT DBMS_METADATA.GET_DDL('FUNCTION', :2, :1) AS definition FROM dual",
        routine_dependencies="""
            SELECT 'table:' || referenced_owner || '.' || referenced_name AS target_object_id,
                   'routine_read' AS edge_type
              FROM all_dependencies
             WHERE owner = :1 AND name = :2 AND referenced_type = 'TABLE'
        """,
        table_comment="""
            SELECT comments AS description FROM all_tab_comments
             WHERE owner = :1 AND table_name = :2
        """,
        indexes="""
            SELECT i.index_name, CASE i.uniqueness WHEN 'UNIQUE' THEN 1 ELSE 0 END AS is_unique,
                   CASE WHEN c.constraint_type = 'P' THEN 1 ELSE 0 END AS is_primary,
                   ic.column_name, ic.column_position AS column_order, 0 AS is_included
              FROM all_indexes i
              JOIN all_ind_columns ic ON ic.index_owner = i.owner AND ic.index_name = i.index_name
              LEFT JOIN all_constraints c ON c.owner = i.owner AND c.index_name = i.index_name
             WHERE i.table_owner = :1 AND i.table_name = :2
             ORDER BY i.index_name, ic.column_position
        """,
        read_only_setup="SET TRANSACTION READ ONLY",
    )

    def discover_capabilities(self, profile: ResolvedConnectionProfile) -> DatabaseCapabilities:
        warnings: list[str] = []
        try:
            privileges = self._execute(profile, "SELECT privilege FROM session_privs")
            names = {str(item["privilege"]).upper() for item in privileges}
            if "SELECT ANY DICTIONARY" not in names:
                warnings.append("Catalog visibility may be limited to explicitly granted objects.")
        except Exception:
            warnings.append("Privilege discovery is unavailable; using least-capability mode.")
        result = self.capabilities()
        return result.model_copy(update={"warnings": warnings})

    def sample_query(self, ref: ObjectRef, order: list[str], requested: int) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "1"
        return f"SELECT * FROM {qualified} ORDER BY {order_sql} FETCH FIRST {requested} ROWS ONLY"

    def sample_page_query(
        self, ref: ObjectRef, order: list[str], page_size: int, offset: int
    ) -> str:
        qualified = (
            f"{self.quote_identifier(ref.schema_name)}.{self.quote_identifier(ref.object_name)}"
        )
        order_sql = ", ".join(self.quote_identifier(column) for column in order) or "1"
        return (
            f"SELECT * FROM {qualified} ORDER BY {order_sql} "
            f"OFFSET {offset} ROWS FETCH NEXT {page_size} ROWS ONLY"
        )
