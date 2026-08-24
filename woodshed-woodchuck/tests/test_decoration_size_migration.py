from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_placement_remaps_to_new_medium_without_losing_coordinates(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "legacy-decoration-size.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "c8d9e0f1a2b3")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO woodchuck_profiles
              (id, woodchuck_id, display_name, pin_hash, instrument, level, goal,
               created_at, updated_at)
            VALUES
              (1, 'WC-LEGACY-SIZE', 'Legacy Size', 'private', 'Flute',
               'Beginner', 'Practice', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        connection.execute(text("""
            INSERT INTO owned_item_copies
              (id, profile_id, item_key, acquisition_source, acquisition_key,
               purchase_price, placement_x, placement_y, placement_size,
               acquired_at, created_at, updated_at)
            VALUES
              (1, 1, 'candle', 'store', NULL, 25, 0.3, 0.4, 'medium',
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT placement_size FROM owned_item_copies WHERE id = 1"
        )) == "medium"
        assert connection.execute(text(
            "SELECT placement_x, placement_y FROM owned_item_copies WHERE id = 1"
        )).one() == (0.3, 0.4)
    default = next(
        column["default"]
        for column in inspect(engine).get_columns("owned_item_copies")
        if column["name"] == "placement_size"
    )
    assert "medium" in str(default)


def test_new_size_migration_remaps_small_and_downgrades_xlarge_safely(
    tmp_path, monkeypatch,
) -> None:
    database = tmp_path / "m-l-xl-decoration-size.db"
    url = f"sqlite:///{database}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "f1a2b3c4d5e6")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO woodchuck_profiles
              (id, woodchuck_id, display_name, pin_hash, instrument, level, goal,
               created_at, updated_at)
            VALUES
              (1, 'WC-NEW-SIZES', 'New Sizes', 'private', 'Flute',
               'Beginner', 'Practice', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """))
        for item_id, size in enumerate(("small", "medium", "large"), start=1):
            connection.execute(text("""
                INSERT INTO owned_item_copies
                  (id, profile_id, item_key, acquisition_source, acquisition_key,
                   purchase_price, placement_x, placement_y, placement_size,
                   acquired_at, created_at, updated_at)
                VALUES
                  (:id, 1, 'candle', 'store', NULL, 25, :x, 0.4, :size,
                   CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """), {"id": item_id, "x": item_id / 10, "size": size})

    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.execute(text(
            "SELECT placement_size FROM owned_item_copies ORDER BY id"
        )).scalars().all() == ["medium", "medium", "large"]
        connection.execute(text(
            "UPDATE owned_item_copies SET placement_size = 'xlarge' WHERE id = 3"
        ))

    command.downgrade(config, "f1a2b3c4d5e6")
    with engine.connect() as connection:
        assert connection.execute(text(
            "SELECT placement_size FROM owned_item_copies ORDER BY id"
        )).scalars().all() == ["medium", "medium", "large"]
