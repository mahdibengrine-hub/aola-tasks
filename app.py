"""AOLA Tasks — to-do list employé, mobile-first.

Deux roles via token URL :
  - admin  : /a/<ADMIN_TOKEN>     — Mehdi + Riad : creent, editent, suppriment
  - employe: /e/<EMPLOYEE_TOKEN>  — peut SEULEMENT cocher

Stack : Flask + SQLite (1 fichier dans /data ou ./data).
"""
import os
import sqlite3
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, abort, g

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────
ADMIN_TOKEN    = os.environ.get('ADMIN_TOKEN', 'admin-dev')
EMPLOYEE_TOKEN = os.environ.get('EMPLOYEE_TOKEN', 'emp-dev')
DATA_DIR       = '/data' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'data')
DB_PATH        = os.path.join(DATA_DIR, 'tasks.db')
os.makedirs(DATA_DIR, exist_ok=True)


# ── DB ────────────────────────────────────────────────────────────────────
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_e):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS recurring (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      days_mask TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
      active INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS task (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      due_date TEXT,
      done_at TEXT,
      recurring_id INTEGER,
      gen_date TEXT,
      created_at TEXT NOT NULL,
      FOREIGN KEY (recurring_id) REFERENCES recurring(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_task_due ON task(due_date);
    CREATE INDEX IF NOT EXISTS idx_task_done ON task(done_at);
    CREATE INDEX IF NOT EXISTS idx_gen ON task(recurring_id, gen_date);
    ''')
    db.commit()
    db.close()


init_db()


# ── Helpers ───────────────────────────────────────────────────────────────
DAYS_LABEL = {'1': 'Lun', '2': 'Mar', '3': 'Mer', '4': 'Jeu', '5': 'Ven', '6': 'Sam', '7': 'Dim'}


def today_iso():
    return date.today().isoformat()


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(sep=' ')


def generate_today_recurring():
    """Cree les taches du jour a partir des recurrentes actives, si pas deja generees."""
    db = get_db()
    td = today_iso()
    weekday = str(date.today().isoweekday())  # 1=Lun .. 7=Dim
    rows = db.execute('SELECT * FROM recurring WHERE active=1').fetchall()
    for r in rows:
        if weekday not in (r['days_mask'] or '').split(','):
            continue
        exists = db.execute(
            'SELECT 1 FROM task WHERE recurring_id=? AND gen_date=?',
            (r['id'], td)).fetchone()
        if exists:
            continue
        db.execute(
            'INSERT INTO task(title, due_date, recurring_id, gen_date, created_at) '
            'VALUES (?,?,?,?,?)',
            (r['title'], td, r['id'], td, now_iso()))
    db.commit()


def require_token(token, expected):
    if token != expected:
        abort(404)


# ── Routes employe ────────────────────────────────────────────────────────
@app.route('/e/<token>/')
def employee_view(token):
    require_token(token, EMPLOYEE_TOKEN)
    generate_today_recurring()
    db = get_db()
    td = today_iso()
    today_tasks = db.execute(
        'SELECT * FROM task WHERE (due_date<=? OR due_date IS NULL) AND done_at IS NULL '
        'ORDER BY due_date ASC, id ASC', (td,)).fetchall()
    done_today = db.execute(
        "SELECT * FROM task WHERE date(done_at)=? ORDER BY done_at DESC",
        (td,)).fetchall()
    return render_template('employee.html',
                           today_tasks=today_tasks, done_today=done_today,
                           token=token, today_label=date.today().strftime('%A %d %B').capitalize())


@app.route('/e/<token>/done/<int:tid>', methods=['POST'])
def employee_done(token, tid):
    require_token(token, EMPLOYEE_TOKEN)
    db = get_db()
    db.execute('UPDATE task SET done_at=? WHERE id=? AND done_at IS NULL', (now_iso(), tid))
    db.commit()
    return redirect(url_for('employee_view', token=token))


@app.route('/e/<token>/undo/<int:tid>', methods=['POST'])
def employee_undo(token, tid):
    """Petit filet de securite : undo possible le meme jour seulement."""
    require_token(token, EMPLOYEE_TOKEN)
    db = get_db()
    db.execute("UPDATE task SET done_at=NULL WHERE id=? AND date(done_at)=?",
               (tid, today_iso()))
    db.commit()
    return redirect(url_for('employee_view', token=token))


# ── Routes admin ──────────────────────────────────────────────────────────
@app.route('/a/<token>/')
def admin_view(token):
    require_token(token, ADMIN_TOKEN)
    generate_today_recurring()
    db = get_db()
    td = today_iso()
    overdue = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date<? ORDER BY due_date ASC',
        (td,)).fetchall()
    today_tasks = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date=? ORDER BY id ASC',
        (td,)).fetchall()
    upcoming = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date>? ORDER BY due_date ASC LIMIT 50',
        (td,)).fetchall()
    no_date = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date IS NULL ORDER BY id ASC').fetchall()
    recurring = db.execute('SELECT * FROM recurring ORDER BY active DESC, id ASC').fetchall()
    recent_done = db.execute(
        'SELECT * FROM task WHERE done_at IS NOT NULL ORDER BY done_at DESC LIMIT 20').fetchall()
    return render_template('admin.html',
                           overdue=overdue, today_tasks=today_tasks,
                           upcoming=upcoming, no_date=no_date,
                           recurring=recurring, recent_done=recent_done,
                           token=token, today=td,
                           days_label=DAYS_LABEL)


@app.route('/a/<token>/add', methods=['POST'])
def admin_add(token):
    require_token(token, ADMIN_TOKEN)
    title = (request.form.get('title') or '').strip()
    due = (request.form.get('due_date') or '').strip() or None
    if not title:
        return redirect(url_for('admin_view', token=token))
    db = get_db()
    db.execute('INSERT INTO task(title, due_date, created_at) VALUES (?,?,?)',
               (title, due, now_iso()))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/del/<int:tid>', methods=['POST'])
def admin_delete(token, tid):
    require_token(token, ADMIN_TOKEN)
    db = get_db()
    db.execute('DELETE FROM task WHERE id=?', (tid,))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/edit/<int:tid>', methods=['POST'])
def admin_edit(token, tid):
    require_token(token, ADMIN_TOKEN)
    title = (request.form.get('title') or '').strip()
    due = (request.form.get('due_date') or '').strip() or None
    db = get_db()
    db.execute('UPDATE task SET title=?, due_date=? WHERE id=?', (title, due, tid))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/recurring/add', methods=['POST'])
def admin_add_recurring(token):
    require_token(token, ADMIN_TOKEN)
    title = (request.form.get('title') or '').strip()
    days = request.form.getlist('days')  # liste de '1'..'7'
    if not title or not days:
        return redirect(url_for('admin_view', token=token))
    days_mask = ','.join(sorted(set(days)))
    db = get_db()
    db.execute('INSERT INTO recurring(title, days_mask, active, created_at) VALUES (?,?,1,?)',
               (title, days_mask, now_iso()))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/recurring/toggle/<int:rid>', methods=['POST'])
def admin_toggle_recurring(token, rid):
    require_token(token, ADMIN_TOKEN)
    db = get_db()
    db.execute('UPDATE recurring SET active = 1 - active WHERE id=?', (rid,))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/recurring/del/<int:rid>', methods=['POST'])
def admin_delete_recurring(token, rid):
    require_token(token, ADMIN_TOKEN)
    db = get_db()
    db.execute('DELETE FROM recurring WHERE id=?', (rid,))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/calendar')
def admin_calendar(token):
    require_token(token, ADMIN_TOKEN)
    db = get_db()
    # 6 semaines a partir de lundi de cette semaine
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end   = start + timedelta(weeks=6)
    rows = db.execute(
        "SELECT * FROM task WHERE due_date >= ? AND due_date < ? ORDER BY due_date ASC",
        (start.isoformat(), end.isoformat())).fetchall()
    by_day = {}
    for r in rows:
        by_day.setdefault(r['due_date'], []).append(dict(r))
    weeks = []
    for w in range(6):
        wk = []
        for d in range(7):
            day = start + timedelta(weeks=w, days=d)
            wk.append({
                'date': day,
                'iso': day.isoformat(),
                'tasks': by_day.get(day.isoformat(), []),
                'is_today': day == today,
                'is_past': day < today,
            })
        weeks.append(wk)
    return render_template('calendar.html', weeks=weeks, token=token, today=today)


# ── Landing ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return ('AOLA Tasks. Utilise ton lien personnel.', 200,
            {'Content-Type': 'text/plain; charset=utf-8'})


if __name__ == '__main__':
    app.run(debug=True, port=5050)
