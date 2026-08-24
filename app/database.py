from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import event

sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

# Tambahkan timeout 30 detik agar tidak langsung melemparkan error jika DB sibuk
connect_args = {"timeout": 30}
engine = create_engine(sqlite_url, connect_args=connect_args)

# Otomatis set SQLite PRAGMA untuk WAL mode di setiap koneksi
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session