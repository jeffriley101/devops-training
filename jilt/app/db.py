import os
import psycopg

from app.config import DB_HOST, DB_NAME, DB_USER


def get_connection():
    return psycopg.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=os.environ["JILT_DB_PASSWORD"],
    )
