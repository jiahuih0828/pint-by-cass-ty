"""Make local password settings for the two partner accounts.

This asks for each password without showing it on screen, stores only secure
password hashes in a private .env file, and creates a random session key.
"""

from getpass import getpass
from pathlib import Path
import secrets

from werkzeug.security import generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
PARTNERS = ["Cass", "Ty"]


def ask_password(partner):
    """Ask twice so a typing mistake does not lock out the partner."""
    while True:
        first = getpass(f"Create a password for {partner} (12+ characters): ")
        if len(first) < 12:
            print("Use at least 12 characters.")
            continue
        second = getpass("Type it again to confirm: ")
        if first == second:
            return first
        print("Those did not match. Try again.")


def main():
    """Write the secret settings that the local app reads when it starts."""
    settings = {"FLASK_SECRET_KEY": secrets.token_hex(32)}
    for partner in PARTNERS:
        password = ask_password(partner)
        settings[f"{partner.upper()}_PASSWORD_HASH"] = generate_password_hash(password)

    env_path = BASE_DIR / ".env"
    lines = [f'{key}="{value}"' for key, value in settings.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved password hashes and a private session key in {env_path.name}.")
    print("Keep .env private. It is excluded from the project ZIP and source control.")
    print("For online hosting, copy these values into the host's private settings:")
    print("APP_ENV=production")
    print(f"FLASK_SECRET_KEY={settings['FLASK_SECRET_KEY']}")
    for partner in PARTNERS:
        print(f"{partner.upper()}_PASSWORD_HASH={settings[f'{partner.upper()}_PASSWORD_HASH']}")
    print("TRACKER_DATABASE=/var/data/tracker.sqlite3")


if __name__ == "__main__":
    main()
