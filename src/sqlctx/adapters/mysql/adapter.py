"""MySQL read-only catalog adapter."""

from sqlctx.adapters.base import AdapterQueries, BaseDatabaseAdapter
from sqlctx.core.enums import DatabaseEngine

MYSQL_QUERIES = AdapterQueries(
    server_info="SELECT VERSION() AS version",
    schemas="SELECT schema_name FROM information_schema.schemata ORDER BY schema_name",
    objects="""
        WITH target AS (SELECT %s AS schema_name)
        SELECT table_name AS object_name, 'table' AS object_type
          FROM information_schema.tables JOIN target ON table_schema = target.schema_name
         WHERE table_type = 'BASE TABLE'
        UNION ALL
        SELECT routine_name AS object_name, 'procedure' AS object_type
          FROM information_schema.routines JOIN target ON routine_schema = target.schema_name
         WHERE routine_type = 'PROCEDURE'
        UNION ALL
        SELECT routine_name AS object_name, 'function' AS object_type
          FROM information_schema.routines JOIN target ON routine_schema = target.schema_name
         WHERE routine_type = 'FUNCTION'
        ORDER BY object_type, object_name
    """,
    columns="""
        SELECT column_name, data_type, is_nullable, ordinal_position
          FROM information_schema.columns
         WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position
    """,
    constraints="""
        SELECT tc.constraint_name, tc.constraint_type, kcu.column_name,
               cc.check_clause AS expression
          FROM information_schema.table_constraints tc
          LEFT JOIN information_schema.key_column_usage kcu
            ON tc.constraint_schema = kcu.constraint_schema
           AND tc.table_name = kcu.table_name
           AND tc.constraint_name = kcu.constraint_name
          LEFT JOIN information_schema.check_constraints cc
            ON tc.constraint_schema = cc.constraint_schema
           AND tc.constraint_name = cc.constraint_name
         WHERE tc.table_schema = %s AND tc.table_name = %s
         ORDER BY tc.constraint_name, kcu.ordinal_position
    """,
    foreign_keys="""
        SELECT constraint_name, column_name AS source_column,
               referenced_table_schema AS target_schema,
               referenced_table_name AS target_table,
               referenced_column_name AS target_column
          FROM information_schema.key_column_usage
         WHERE table_schema = %s AND table_name = %s
           AND referenced_table_name IS NOT NULL
    """,
    table_definition=None,
    procedure_definition="""
        SELECT routine_definition AS definition
          FROM information_schema.routines
         WHERE routine_schema = %s AND routine_name = %s AND routine_type = 'PROCEDURE'
    """,
    function_definition="""
        SELECT routine_definition AS definition
          FROM information_schema.routines
         WHERE routine_schema = %s AND routine_name = %s AND routine_type = 'FUNCTION'
    """,
    routine_dependencies="""
        SELECT CONCAT('table:', table_schema, '.', table_name) AS target_object_id,
               'routine_read' AS edge_type
          FROM information_schema.routine_table_usage
         WHERE routine_schema = %s AND routine_name = %s
    """,
    table_comment="""
        SELECT table_comment AS description
          FROM information_schema.tables
         WHERE table_schema = %s AND table_name = %s
    """,
    indexes="""
        SELECT index_name, CASE WHEN non_unique = 0 THEN 1 ELSE 0 END AS is_unique,
               CASE WHEN index_name = 'PRIMARY' THEN 1 ELSE 0 END AS is_primary,
               column_name, seq_in_index AS column_order, 0 AS is_included
          FROM information_schema.statistics
         WHERE table_schema = %s AND table_name = %s
         ORDER BY index_name, seq_in_index
    """,
    read_only_setup="SET TRANSACTION READ ONLY",
)


class MySqlAdapter(BaseDatabaseAdapter):
    engine = DatabaseEngine.MYSQL
    dialect = "mysql"
    parameter_token = "%s"
    quote_left = "`"
    quote_right = "`"
    queries = MYSQL_QUERIES
