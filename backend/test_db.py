from sqlalchemy import text

from app.core.database import engine


def test_database_connection():
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text("SELECT current_database(), current_user")
            )

            database, user = result.fetchone()

            print("\nDATABASE CONNECTION SUCCESSFUL")
            print("Database:", database)
            print("User:", user)

    except Exception as error:
        print("\nDATABASE CONNECTION FAILED")
        print(error)

        raise


if __name__ == "__main__":
    test_database_connection()