import json
import os

SETTINGS_FILE = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_active_sheet(sheet_type: str):
    settings = load_settings()
    return settings.get(sheet_type)

def set_active_sheet(sheet_type: str, sheet_name: str):
    settings = load_settings()
    settings[sheet_type] = sheet_name
    save_settings(settings)