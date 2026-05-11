import yaml
from pathlib import Path
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent

def load_roles():

    path = BASE_DIR / "config" / "roles.yaml"

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("roles", {})

def get_current_role():

    return st.session_state.get("role")

def get_role_config():

    role = get_current_role()

    roles = load_roles()

    return roles.get(role, {})

def can_access_system(system_type):

    role_cfg = get_role_config()

    return system_type in role_cfg.get("systems", [])

def can_access_page(page_name):

    role_cfg = get_role_config()

    return page_name in role_cfg.get("pages", [])

def can_access_log(log_group):

    role_cfg = get_role_config()

    return log_group in role_cfg.get("logs", [])
