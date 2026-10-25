"""Create and drop an isolated PostgreSQL database for the frontend E2E suite."""
import sys
from urllib.parse import urlsplit, urlunsplit
import psycopg
from psycopg import sql


def base36(value: str) -> int:
    result = 0
    for character in value:
        result = result * 36 + "0123456789abcdefghijklmnopqrstuvwxyz".index(character.lower())
    return result


def main(action: str, admin_url: str, database: str) -> None:
    normalized = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    parsed = urlsplit(normalized)
    admin = urlunsplit((parsed.scheme, parsed.netloc, "/postgres", parsed.query, parsed.fragment))
    with psycopg.connect(admin, autocommit=True) as connection:
        if action == "create":
            connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        elif action == "drop":
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database))
            )
        elif action == "close-sessions":
            if not database.startswith("yla_e2e_"):
                raise ValueError("refusing to terminate sessions outside an isolated E2E database")
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid() "
                "AND state = 'idle in transaction'",
                (database,),
            )
        elif action == "cleanup":
            cutoff_ms = int(database)
            rows = connection.execute(
                "SELECT datname FROM pg_database WHERE left(datname, 8) = 'yla_e2e_'"
            ).fetchall()
            for (name,) in rows:
                try:
                    created_ms = base36(name.rsplit("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if created_ms > cutoff_ms:
                    continue
                count = connection.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (name,)
                ).fetchone()[0]
                if count == 0:
                    connection.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                    )
        else:
            raise ValueError(action)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
