"""PostgreSQL read-only catalog adapter."""

from sqlctx.adapters.base import AdapterQueries, BaseDatabaseAdapter
from sqlctx.core.enums import DatabaseEngine


class PostgreSqlAdapter(BaseDatabaseAdapter):
    engine = DatabaseEngine.POSTGRES
    dialect = "postgres"
    parameter_token = "%s"
    supports_consistent_snapshot = True
    queries = AdapterQueries(
        server_info="SELECT version() AS version",
        schemas="SELECT schema_name FROM information_schema.schemata ORDER BY schema_name",
        objects="""
            WITH target AS (SELECT %s::text AS schema_name)
            SELECT table_name AS object_name, 'table' AS object_type
              FROM information_schema.tables, target
             WHERE table_schema = target.schema_name AND table_type = 'BASE TABLE'
            UNION ALL
            SELECT routine_name AS object_name, 'procedure' AS object_type
              FROM information_schema.routines, target
             WHERE routine_schema = target.schema_name AND routine_type = 'PROCEDURE'
            UNION ALL
            SELECT routine_name AS object_name, 'function' AS object_type
              FROM information_schema.routines, target
             WHERE routine_schema = target.schema_name AND routine_type = 'FUNCTION'
            ORDER BY object_type, object_name
        """,
        columns="""
            SELECT column_name, data_type, is_nullable, ordinal_position
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s
             ORDER BY ordinal_position
        """,
        constraints="""
            SELECT tc.constraint_name, tc.constraint_type, kcu.column_name,
                   cc.check_clause AS expression
              FROM information_schema.table_constraints tc
              LEFT JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
               AND tc.constraint_schema = kcu.constraint_schema
              LEFT JOIN information_schema.check_constraints cc
                ON tc.constraint_catalog = cc.constraint_catalog
               AND tc.constraint_schema = cc.constraint_schema
               AND tc.constraint_name = cc.constraint_name
             WHERE tc.table_schema = %s AND tc.table_name = %s
             ORDER BY tc.constraint_name, kcu.ordinal_position
        """,
        foreign_keys="""
            SELECT tc.constraint_name, kcu.column_name AS source_column,
                   ccu.table_schema AS target_schema, ccu.table_name AS target_table,
                   ccu.column_name AS target_column
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.constraint_schema = kcu.constraint_schema
              JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name AND tc.constraint_schema = ccu.constraint_schema
             WHERE tc.constraint_type = 'FOREIGN KEY'
               AND tc.table_schema = %s AND tc.table_name = %s
        """,
        table_definition=None,
        procedure_definition="""
            SELECT pg_get_functiondef(p.oid) AS definition
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = %s AND p.proname = %s
             ORDER BY p.oid LIMIT 1
        """,
        function_definition="""
            SELECT pg_get_functiondef(p.oid) AS definition
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = %s AND p.proname = %s AND p.prokind = 'f'
             ORDER BY p.oid LIMIT 1
        """,
        routine_dependencies="""
            SELECT 'table:' || tn.nspname || '.' || c.relname AS target_object_id,
                   'routine_read' AS edge_type
              FROM pg_proc p
              JOIN pg_namespace pn ON pn.oid = p.pronamespace
              JOIN pg_depend d ON d.objid = p.oid
              JOIN pg_class c ON c.oid = d.refobjid
              JOIN pg_namespace tn ON tn.oid = c.relnamespace
             WHERE pn.nspname = %s AND p.proname = %s
        """,
        table_comment="""
            SELECT obj_description(c.oid, 'pg_class') AS description
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relname = %s
        """,
        indexes="""
            SELECT ci.relname AS index_name, ix.indisunique AS is_unique,
                   ix.indisprimary AS is_primary,
                   pg_get_indexdef(ix.indexrelid, position, true) AS column_name,
                   position AS column_order,
                   CASE WHEN position > ix.indnkeyatts THEN true ELSE false END AS is_included
              FROM pg_index ix
              JOIN pg_class ct ON ct.oid = ix.indrelid
              JOIN pg_namespace n ON n.oid = ct.relnamespace
              JOIN pg_class ci ON ci.oid = ix.indexrelid
              CROSS JOIN LATERAL generate_series(1, ix.indnatts) AS position
             WHERE n.nspname = %s AND ct.relname = %s
             ORDER BY ci.relname, position
        """,
        read_only_setup="SET TRANSACTION READ ONLY",
    )
