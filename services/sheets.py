# services/sheets.py
import asyncio
import gspread
from google.oauth2.service_account import Credentials
from functools import wraps

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def run_sync(func):
    """Декоратор для выполнения синхронных gspread операций в отдельном потоке"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)
    return wrapper

class AsyncSheetsClient:
    def __init__(self, credentials_file: str = "credentials.json"):
        self.creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        self.client = None

    @run_sync
    def _get_client(self):
        if self.client is None:
            self.client = gspread.authorize(self.creds)
        return self.client

    async def open_by_key(self, key: str):
        client = await self._get_client()
        return AsyncSpreadsheet(client.open_by_key(key), self)

class AsyncSpreadsheet:
    def __init__(self, spreadsheet, parent_client):
        self._spreadsheet = spreadsheet
        self._parent = parent_client

    @run_sync
    def worksheet(self, title: str):
        return self._spreadsheet.worksheet(title)

    @run_sync
    def worksheets(self):
        return self._spreadsheet.worksheets()

    @run_sync
    def add_worksheet(self, title: str, rows: int, cols: int):
        return self._spreadsheet.add_worksheet(title, rows, cols)

# Глобальный экземпляр
_async_sheets = None

def get_async_sheets():
    global _async_sheets
    if _async_sheets is None:
        _async_sheets = AsyncSheetsClient()
    return _async_sheets