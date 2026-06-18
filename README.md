# AOLA Tasks

Mini app to-do list pour suivre les tâches de l'employé. Mobile-first.

## Routes

- `/a/<ADMIN_TOKEN>/` — admin (Mehdi + Riad)
- `/e/<EMPLOYEE_TOKEN>/` — employé (cocher seulement)

## Local

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

→ http://127.0.0.1:5050/a/admin-dev/ et /e/emp-dev/

## Deploy Render

1. Pousser ce dossier sur un nouveau repo GitHub `aola-tasks`
2. Render → New → Web Service → connecter le repo
3. Runtime Python 3, build `pip install -r requirements.txt`, start auto via Procfile
4. Env vars : `ADMIN_TOKEN` (long aléatoire) + `EMPLOYEE_TOKEN` (long aléatoire)
5. Disque persistant `/data` 1 GB
