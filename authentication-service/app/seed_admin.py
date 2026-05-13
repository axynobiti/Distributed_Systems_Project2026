import os

from passlib.context import CryptContext

from app.database import SessionLocal, engine
from app.models import Base, User

# Password hashing context.
# bcrypt is used so the admin password is stored as a hash, not as plain text.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Create the database tables if they do not already exist.
# This makes the script useful on a fresh local database.
Base.metadata.create_all(bind=engine)

# Open a database session.
db = SessionLocal()

# Default administrator account for local development/testing.
# Kubernetes can override these through the auth-seed-admin Secret.
admin_username = os.getenv("SEED_ADMIN_USERNAME", "admin")
admin_email = os.getenv("SEED_ADMIN_EMAIL", "admin@example.com")
admin_password = os.getenv("SEED_ADMIN_PASSWORD", "admin123")

# Check whether the initial admin user already exists.
existing_admin = db.query(User).filter(User.username == admin_username).first()

if existing_admin:
    print("Admin user already exists.")
else:
    # Hash the password before storing it in the database.
    hashed_password = pwd_context.hash(admin_password)

    # Create the first admin user.
    admin_user = User(
        username=admin_username,
        email=admin_email,
        role="admin",
        password_hash=hashed_password
    )

    # Store the admin user in the database.
    db.add(admin_user)
    db.commit()

    print("First admin user created.")
    print("Username:", admin_username)
    print("Password:", admin_password)

# Close the database session.
db.close()
