import os
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

# --- Database Path ---
basedir = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(basedir, 'citations.db')
db_uri = f'sqlite:///{db_path}'

# --- FTS5 Setup ---
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
create_triggers_sql = [
    """
    CREATE TRIGGER IF NOT EXISTS citation_ai AFTER INSERT ON citation BEGIN
    INSERT INTO citation_fts(rowid, description, keywords, parties, court, sections, journal)
    VALUES (new.id, new.description, new.keywords, new.parties, new.court, new.sections, new.journal);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS citation_ad AFTER DELETE ON citation BEGIN
    INSERT INTO citation_fts(citation_fts, rowid, description, keywords, parties, court, sections, journal)
    VALUES ('delete', old.id, old.description, old.keywords, old.parties, old.court, old.sections, old.journal);
    END;
    """,
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
backfill_data_sql = """
INSERT INTO citation_fts (rowid, description, keywords, parties, court, sections, journal)
SELECT id, description, keywords, parties, court, sections, journal FROM citation;
"""

def setup_fts():
    """
    Connects to the database and executes all necessary SQL statements
    to set up the FTS5 table, triggers, and backfill existing data.
    """
    # Create a standalone engine and session
    engine = create_engine(db_uri)
    
    # Set pragma for foreign keys
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        print("Step 1: Creating FTS5 virtual table 'citation_fts'...")
        session.execute(text(create_fts_table_sql))
        print("Done.")

        print("\nStep 2: Creating triggers to keep FTS table synchronized...")
        for i, trigger_sql in enumerate(create_triggers_sql):
            print(f" - Creating trigger {i+1}/{len(create_triggers_sql)}...")
            session.execute(text(trigger_sql))
        print("Done.")

        print("\nStep 3: Backfilling existing data into the FTS table...")
        # Check if FTS has data before backfilling
        existing_count = session.execute(text("SELECT COUNT(*) FROM citation_fts;")).scalar()
        if existing_count == 0:
            session.execute(text(backfill_data_sql))
        print("Done.")

        session.commit()
        print("\n--- FTS5 setup completed successfully! ---")
    except Exception as e:
        print(f"An error occurred: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    print("Starting FTS5 setup process...")
    setup_fts()
