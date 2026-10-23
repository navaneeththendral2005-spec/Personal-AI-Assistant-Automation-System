"""Keep the test suite independent of a developer's local PostgreSQL config."""
import os

# Tests create their own isolated SQLite engines. This value only lets modules
# import the shared SQLAlchemy metadata during collection; it never points tests
# at a real or production database.
os.environ["DATABASE_URL"] = "sqlite://"
