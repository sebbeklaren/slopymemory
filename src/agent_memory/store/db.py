# src/agent_memory/store/db.py
from importlib import resources
import psycopg
from agent_memory.config import settings

EXPECTED_DIM = 768  # must equal the vector(N) in schema.sql


def apply_schema(conn: psycopg.Connection) -> None:
    if settings.embed_dim != EXPECTED_DIM:
        raise ValueError(
            f"settings.embed_dim={settings.embed_dim} but schema.sql uses vector({EXPECTED_DIM})"
        )
    sql = resources.files("agent_memory.store").joinpath("schema.sql").read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
