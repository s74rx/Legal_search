import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from app import app, db

# --- Database Path ---
# Construct the absolute path to the database file
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'citations.db')
db_uri = f'sqlite:///{db_path}'

# --- FTS5 Setup ---
# The schema for the FTS5 virtual table.
# It mirrors the text-based columns from the 'citation' table.
# The 'content' option tells FTS5 to use the 'citation' table as its content source.
# The 'content_rowid' option links the FTS5 table's rowid to the 'citation' table's 'id'.
create_fts_table_sql = """
CREATE VIRTUAL TABLE IF NOT EXISTS citation_fts
USING fts5(
    description,
    keywords,
    parties,
    court,
    sections,
    journal,
    content='citation',
    content_rowid='id'
);
"""

# --- Triggers ---
# These triggers automatically update the FTS index whenever the 'citation' table is modified.
# This ensures the search index is always in sync with the main data.
create_triggers_sql = [
    # After a new citation is INSERTed, its content is inserted into the FTS table.
    """
    CREATE TRIGGER IF NOT EXISTS citation_ai AFTER INSERT ON citation BEGIN
    INSERT INTO citation_fts(rowid, description, keywords, parties, court, sections, journal)
    VALUES (new.id, new.description, new.keywords, new.parties, new.court, new.sections, new.journal);
    END;
    """,
    # Before a citation is DELETEd, the corresponding FTS entry is also deleted.
    """
    CREATE TRIGGER IF NOT EXISTS citation_ad AFTER DELETE ON citation BEGIN
    INSERT INTO citation_fts(citation_fts, rowid, description, keywords, parties, court, sections, journal)
    VALUES ('delete', old.id, old.description, old.keywords, old.parties, old.court, old.sections, old.journal);
    END;
    """,
    # After a citation is UPDATEd, the corresponding FTS entry is also updated.
    """
    CREATE TRIGGER IF NOT EXISTS citation_au AFTER UPDATE ON citation BEGIN
    INSERT INTO citation_fts(citation_fts, rowid, description, keywords, parties, court, sections, journal)
    VALUES ('delete', old.id, old.description, old.keywords, old.parties, old.court, old.sections, old.journal);
    INSERT INTO citation_fts(rowid, description, keywords, parties, court, sections, journal)
    VALUES (new.id, new.description, new.keywords, new.parties, new.court, new.sections, new.journal);
    END;
    """
]

# --- Backfill Data ---
# This query populates the FTS table with all existing data from the 'citation' table.
# It's crucial for when you first set up FTS on a database with existing records.
backfill_data_sql = """
INSERT INTO citation_fts (rowid, description, keywords, parties, court, sections, journal)
SELECT id, description, keywords, parties, court, sections, journal FROM citation;
"""

def setup_fts():
    """
    Connects to the database and executes all necessary SQL statements
    to set up the FTS5 table, triggers, and backfill existing data.
    """
    with app.app_context():
        # Set a pragma to enable foreign key support, which is good practice.
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            print("Step 1: Creating FTS5 virtual table 'citation_fts'...")
            db.session.execute(text(create_fts_table_sql))
            print("Done.")

            print("\nStep 2: Creating triggers to keep FTS table synchronized...")
            for i, trigger_sql in enumerate(create_triggers_sql):
                print(f" - Creating trigger {i+1}/{len(create_triggers_sql)}...")
                db.session.execute(text(trigger_sql))
            print("Done.")

            print("\nStep 3: Backfilling existing data into the FTS table...")
            # Check if FTS has data before backfilling to avoid unnecessary operations
            existing_count = db.session.execute(text("SELECT COUNT(*) FROM citation_fts;")).scalar()
            if existing_count == 0:
                db.session.execute(text(backfill_data_sql))
            print("Done.")

            db.session.commit()
            print("\n--- FTS5 setup completed successfully! ---")
        except Exception as e:
            print(f"An error occurred: {e}")
            db.session.rollback()
        finally:
            db.session.close()

if __name__ == '__main__':
    # This allows the script to be run directly from the command line.
    print("Starting FTS5 setup process...")
    setup_fts()
