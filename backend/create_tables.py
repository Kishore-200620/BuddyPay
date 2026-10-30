from app.core.database import Base, engine
from app.models import User

def create_tables():
    Base.metadata.create_all(bind=engine)

    print("Database tables created successfully!")

    print("Registered tables:")
    for table in Base.metadata.sorted_tables:
        print(f"- {table.name}")


if __name__ == "__main__":
    create_tables()