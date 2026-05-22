from services.sheets import get_sheet

print("Импорт успешен")
sheet = get_sheet("Лист1")
print("Подключение успешно")
sheet.append_row(["Тест", "соединение", "работает!"])
print("Строка добавлена!")