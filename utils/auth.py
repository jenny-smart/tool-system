import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def load_users():

    path = BASE_DIR / "config" / "users.yaml"

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("users", {})

def authenticate(username, password):

    users = load_users()

    if username not in users:
        return None

    user = users[username]

    if user["password"] != password:
        return None

    return {
        "username": username,
        "role": user["role"]
    }
