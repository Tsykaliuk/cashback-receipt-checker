# Кешбек за чек

Хмарні обчислення, завдання 2 · Максим Цикалюк, ТТП-41

Вебзастосунок для акцій «купи товар, завантаж чек і отримай гроші назад». Учасник завантажує фото чека, а система сама знаходить акційний товар, перевіряє умови й не дає взяти кешбек двічі.

```
Фото чека → Gemini API (читає чек) → правила акції → Amazon S3 + DynamoDB
```

## Запуск

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # ключ Gemini з aistudio.google.com і ключі AWS
python -m scripts.setup_aws   # один раз: створює S3-бакет і таблицю DynamoDB
streamlit run app.py
```

Права для IAM-користувача AWS описано в `iam-policy.json`.

## Тести

```bash
pip install -r requirements-dev.txt && pytest
```
