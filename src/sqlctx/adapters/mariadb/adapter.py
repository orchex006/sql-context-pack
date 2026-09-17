"""MariaDB remains a distinct product adapter while sharing reviewed metadata templates."""

from sqlctx.adapters.mysql.adapter import MYSQL_QUERIES, MySqlAdapter
from sqlctx.core.enums import DatabaseEngine


class MariaDbAdapter(MySqlAdapter):
    engine = DatabaseEngine.MARIADB
    dialect = "mariadb"
    queries = MYSQL_QUERIES
