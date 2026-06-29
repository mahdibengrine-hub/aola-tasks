"""AOLA Tasks — to-do list employé.

Deux roles via token URL :
  - admin  : /a/<ADMIN_TOKEN>     — Mehdi + Riad : creent, editent, suppriment
  - employe: /e/<EMPLOYEE_TOKEN>  — peut SEULEMENT cocher

Stack : Flask + SQLite (1 fichier dans /data ou ./data).
"""
import os
import sqlite3
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, redirect, url_for, abort, g, flash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or 'aola-tasks-flash-key-' + (os.environ.get('ADMIN_TOKEN') or 'dev')


# ── Config ────────────────────────────────────────────────────────────────
def _env(key, default):
    return (os.environ.get(key) or default).strip()


DATA_DIR = '/data' if os.path.isdir('/data') else os.path.join(os.path.dirname(__file__), 'data')
DB_PATH  = os.path.join(DATA_DIR, 'tasks.db')
os.makedirs(DATA_DIR, exist_ok=True)

LOCATIONS  = ['Garden', 'Boutique', 'Oran', 'Bureau', 'Autre']
FR_DAYS    = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
FR_DAYS_S  = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
FR_MONTHS  = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin',
              'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
DAYS_LABEL = {str(i+1): FR_DAYS_S[i] for i in range(7)}


def fr_long(d):
    return f"{FR_DAYS[d.weekday()]} {d.day} {FR_MONTHS[d.month-1]}"


def fr_short(d):
    return f"{d.day} {FR_MONTHS[d.month-1]}"


def fr_short_y(d):
    return f"{d.day} {FR_MONTHS[d.month-1]} {d.year}"


@app.template_filter('fr_date')
def _fr_date_filter(iso_str):
    """Convertit '2026-06-19' en '19 juin 2026'. Robuste a tout cas."""
    if not iso_str:
        return ''
    try:
        d = date.fromisoformat(str(iso_str)[:10])
        return f"{d.day} {FR_MONTHS[d.month-1]} {d.year}"
    except Exception:
        return str(iso_str)


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
    CREATE TABLE IF NOT EXISTS recurring_skip (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      recurring_id INTEGER NOT NULL,
      skip_date TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(recurring_id, skip_date)
    );
    CREATE INDEX IF NOT EXISTS idx_task_due ON task(due_date);
    CREATE INDEX IF NOT EXISTS idx_task_done ON task(done_at);
    CREATE INDEX IF NOT EXISTS idx_gen ON task(recurring_id, gen_date);
    ''')

    def colset(table):
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}

    task_cols = colset('task')
    if 'priority' not in task_cols:
        db.execute('ALTER TABLE task ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
    if 'location' not in task_cols:
        db.execute('ALTER TABLE task ADD COLUMN location TEXT')
    if 'original_due_date' not in task_cols:
        db.execute('ALTER TABLE task ADD COLUMN original_due_date TEXT')
        # Pour les taches existantes : on recupere due_date comme original
        db.execute(
            'UPDATE task SET original_due_date = due_date '
            'WHERE original_due_date IS NULL AND due_date IS NOT NULL')
    if 'is_transfer' not in task_cols:
        db.execute('ALTER TABLE task ADD COLUMN is_transfer INTEGER NOT NULL DEFAULT 0')
    if 'transfer_number' not in task_cols:
        db.execute('ALTER TABLE task ADD COLUMN transfer_number TEXT')

    rec_cols = colset('recurring')
    if 'priority' not in rec_cols:
        db.execute('ALTER TABLE recurring ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
    if 'location' not in rec_cols:
        db.execute('ALTER TABLE recurring ADD COLUMN location TEXT')

    db.commit()
    db.close()


init_db()


# ── Helpers ───────────────────────────────────────────────────────────────
def today_iso():
    return date.today().isoformat()


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat(sep=' ')


def require_token(token, env_key):
    expected = _env(env_key, '')
    if (token or '').strip() != expected:
        abort(404)


def purge_old_history():
    """Supprime les taches faites il y a plus de 2 jours pour alleger la DB.
    Hier et avant-hier sont conserves (utilises dans Suivi)."""
    db = get_db()
    cutoff = (date.today() - timedelta(days=2)).isoformat()
    db.execute(
        "DELETE FROM task WHERE done_at IS NOT NULL AND date(done_at) < ?",
        (cutoff,))
    db.commit()


def generate_today_recurring():
    """Crée les tâches du jour à partir des récurrentes actives,
    sauf si elles sont marquées 'skip' pour ce jour."""
    purge_old_history()
    db = get_db()
    td = today_iso()
    weekday = str(date.today().isoweekday())
    rows = db.execute('SELECT * FROM recurring WHERE active=1').fetchall()
    skipped = {r['recurring_id'] for r in db.execute(
        'SELECT recurring_id FROM recurring_skip WHERE skip_date=?', (td,)).fetchall()}
    for r in rows:
        if r['id'] in skipped:
            continue
        if weekday not in (r['days_mask'] or '').split(','):
            continue
        exists = db.execute(
            'SELECT 1 FROM task WHERE recurring_id=? AND gen_date=?',
            (r['id'], td)).fetchone()
        if exists:
            continue
        db.execute(
            'INSERT INTO task(title, due_date, original_due_date, recurring_id, gen_date, '
            'priority, location, created_at) VALUES (?,?,?,?,?,?,?,?)',
            (r['title'], td, td, r['id'], td,
             r['priority'] or 0, r['location'], now_iso()))
    db.commit()


def normalize_location(loc):
    if loc in LOCATIONS:
        return loc
    return None


def compute_delay_days(t):
    """Renvoie le nombre de jours de retard depuis original_due_date jusqu'a aujourd'hui.
    0 si pas de retard ou pas de date d'origine."""
    orig = t['original_due_date'] if 'original_due_date' in t.keys() else None
    orig = orig or t['due_date']
    if not orig:
        return 0
    try:
        o = date.fromisoformat(str(orig)[:10])
        delta = (date.today() - o).days
        return delta if delta > 0 else 0
    except Exception:
        return 0


def group_by_location(tasks):
    """Renvoie [(loc_name, [tasks])] ordonné par LOCATIONS puis 'Sans lieu'."""
    buckets = {loc: [] for loc in LOCATIONS}
    no_loc = []
    for t in tasks:
        loc = t['location'] if t['location'] in LOCATIONS else None
        if loc:
            buckets[loc].append(t)
        else:
            no_loc.append(t)
    result = [(loc, buckets[loc]) for loc in LOCATIONS if buckets[loc]]
    if no_loc:
        result.append(('Sans lieu', no_loc))
    return result


# ── Routes employé ────────────────────────────────────────────────────────
@app.route('/e/<token>/')
def employee_view(token):
    require_token(token, 'EMPLOYEE_TOKEN')
    generate_today_recurring()
    db = get_db()
    td = today_iso()

    open_rows = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND (due_date<=? OR due_date IS NULL) '
        'ORDER BY priority DESC, due_date ASC, id ASC', (td,)).fetchall()
    transfers = []
    tasks = []
    for t in open_rows:
        d = dict(t)
        d['is_waiting']  = bool(t['due_date'] and t['due_date'] < td)
        d['is_priority'] = bool(t['priority'])
        d['delay_days']  = compute_delay_days(t)
        if t['is_transfer']:
            transfers.append(d)
        else:
            tasks.append(d)
    # priorité d'abord, en attente, puis le reste — tri stable
    tasks.sort(key=lambda t: (not t['is_priority'], not t['is_waiting']))
    grouped = group_by_location(tasks)

    done_today = db.execute(
        "SELECT * FROM task WHERE date(done_at)=? ORDER BY done_at DESC",
        (td,)).fetchall()
    return render_template('employee.html',
                           transfers=transfers,
                           grouped=grouped,
                           done_today=done_today,
                           token=token,
                           today_label=fr_long(date.today()))


@app.route('/e/<token>/done/<int:tid>', methods=['POST'])
def employee_done(token, tid):
    require_token(token, 'EMPLOYEE_TOKEN')
    db = get_db()
    db.execute('UPDATE task SET done_at=? WHERE id=? AND done_at IS NULL', (now_iso(), tid))
    db.commit()
    return redirect(url_for('employee_view', token=token))


@app.route('/e/<token>/undo/<int:tid>', methods=['POST'])
def employee_undo(token, tid):
    require_token(token, 'EMPLOYEE_TOKEN')
    db = get_db()
    db.execute("UPDATE task SET done_at=NULL WHERE id=? AND date(done_at)=?",
               (tid, today_iso()))
    db.commit()
    return redirect(url_for('employee_view', token=token))


@app.route('/e/<token>/calendar')
def employee_calendar(token):
    require_token(token, 'EMPLOYEE_TOKEN')
    days, today = _next_days(3)
    return render_template('calendar.html',
                           days=days, token=token, today=today,
                           is_admin=False)


# ── Helpers calendrier ────────────────────────────────────────────────────
def _next_days(n_days):
    """Renvoie les n prochains jours (à partir d'aujourd'hui) avec leurs tâches.
    Exclut les tâches récurrentes (recurring_id IS NOT NULL)."""
    db = get_db()
    today = date.today()
    end = today + timedelta(days=n_days)
    rows = db.execute(
        "SELECT * FROM task WHERE due_date >= ? AND due_date < ? "
        "AND recurring_id IS NULL "
        "ORDER BY priority DESC, due_date ASC, id ASC",
        (today.isoformat(), end.isoformat())).fetchall()
    by_day = {}
    for r in rows:
        by_day.setdefault(r['due_date'], []).append(dict(r))
    days = []
    for i in range(n_days):
        d = today + timedelta(days=i)
        days.append({
            'date':     d,
            'iso':      d.isoformat(),
            'label':    fr_long(d),
            'short':    fr_short(d),
            'tasks':    by_day.get(d.isoformat(), []),
            'is_today': i == 0,
        })
    return days, today


# ── Routes admin ──────────────────────────────────────────────────────────
def _enrich(rows):
    """Convertit des sqlite Rows en dicts avec is_waiting/is_priority/delay_days."""
    td = today_iso()
    out = []
    for t in rows:
        d = dict(t)
        d['is_waiting']  = bool(t['due_date'] and t['due_date'] < td)
        d['is_priority'] = bool(t['priority'])
        d['delay_days']  = compute_delay_days(t)
        out.append(d)
    return out


@app.route('/a/<token>/')
def admin_view(token):
    require_token(token, 'ADMIN_TOKEN')
    generate_today_recurring()
    db = get_db()
    td = today_iso()
    overdue = _enrich(db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date<? '
        'ORDER BY priority DESC, due_date ASC', (td,)).fetchall())
    today_tasks = _enrich(db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date=? '
        'ORDER BY priority DESC, id ASC', (td,)).fetchall())
    upcoming = _enrich(db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND due_date>? '
        'ORDER BY due_date ASC LIMIT 50', (td,)).fetchall())
    return render_template('admin.html',
                           overdue=overdue,
                           today_tasks=today_tasks,
                           upcoming=upcoming,
                           token=token,
                           today=td,
                           today_label=fr_long(date.today()),
                           locations=LOCATIONS,
                           fr_short=fr_short)


@app.route('/a/<token>/add', methods=['POST'])
def admin_add(token):
    require_token(token, 'ADMIN_TOKEN')
    title       = (request.form.get('title') or '').strip()
    due         = (request.form.get('due_date') or '').strip()
    priority    = 1 if request.form.get('priority') else 0
    location    = normalize_location(request.form.get('location'))
    is_transfer = 1 if request.form.get('is_transfer') else 0
    transfer_no = (request.form.get('transfer_number') or '').strip() or None
    # Si transfert : titre auto-genere depuis le n°, le titre saisi est ignore
    if is_transfer:
        if not transfer_no:
            flash("Le numéro du transfert est obligatoire.", 'error')
            return redirect(url_for('admin_view', token=token))
        title = f"Transfert #{transfer_no.lstrip('#')}"
    if not title or not due or not location:
        flash("Tous les champs sont obligatoires.", 'error')
        return redirect(url_for('admin_view', token=token))
    db = get_db()
    db.execute(
        'INSERT INTO task(title, due_date, original_due_date, priority, location, '
        'is_transfer, transfer_number, created_at) '
        'VALUES (?,?,?,?,?,?,?,?)',
        (title, due, due, priority, location, is_transfer, transfer_no, now_iso()))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/postpone/<int:tid>', methods=['POST'])
def admin_postpone(token, tid):
    """Reporte une tache a une nouvelle date sans toucher original_due_date.
    Permet d'afficher 'X jours de retard' meme apres report."""
    require_token(token, 'ADMIN_TOKEN')
    new_date = (request.form.get('new_date') or '').strip()
    if not new_date:
        flash("Choisis une date avant de reporter.", 'error')
        return redirect(request.referrer or url_for('admin_view', token=token))
    db = get_db()
    existing = db.execute('SELECT title, due_date FROM task WHERE id=?', (tid,)).fetchone()
    if not existing:
        flash("Tâche introuvable.", 'error')
        return redirect(request.referrer or url_for('admin_view', token=token))
    db.execute(
        'UPDATE task SET '
        '  original_due_date = COALESCE(original_due_date, due_date), '
        '  due_date = ? '
        'WHERE id=?', (new_date, tid))
    db.commit()
    try:
        d_fr = fr_long(date.fromisoformat(new_date))
        flash(f'« {existing["title"]} » reportée au {d_fr}.', 'success')
    except Exception:
        flash('Tâche reportée.', 'success')
    return redirect(request.referrer or url_for('admin_view', token=token))


@app.route('/a/<token>/clear-today', methods=['POST'])
def admin_clear_today(token):
    """Supprime toutes les tâches du jour, qu'elles soient récurrentes ou ad-hoc.
    Concerne uniquement les tâches non-encore-faites pour le jour courant."""
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    td = today_iso()
    db.execute(
        'DELETE FROM task WHERE due_date=? AND done_at IS NULL', (td,))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/clear-overdue', methods=['POST'])
def admin_clear_overdue(token):
    """Supprime toutes les tâches en retard non-faites (due_date < today)."""
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    td = today_iso()
    db.execute(
        'DELETE FROM task WHERE due_date<? AND done_at IS NULL', (td,))
    db.commit()
    return redirect(url_for('admin_view', token=token))


@app.route('/a/<token>/del/<int:tid>', methods=['POST'])
def admin_delete(token, tid):
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    db.execute('DELETE FROM task WHERE id=?', (tid,))
    db.commit()
    return redirect(request.referrer or url_for('admin_view', token=token))


@app.route('/a/<token>/edit/<int:tid>', methods=['POST'])
def admin_edit(token, tid):
    require_token(token, 'ADMIN_TOKEN')
    title    = (request.form.get('title') or '').strip()
    due      = (request.form.get('due_date') or '').strip() or None
    priority = 1 if request.form.get('priority') else 0
    location = normalize_location(request.form.get('location'))
    is_transfer = 1 if request.form.get('is_transfer') else 0
    transfer_no = (request.form.get('transfer_number') or '').strip() or None
    db = get_db()
    existing = db.execute(
        'SELECT title, due_date, location, transfer_number FROM task WHERE id=?',
        (tid,)).fetchone()
    if not existing:
        flash("Tâche introuvable.", 'error')
        return redirect(request.referrer or url_for('admin_view', token=token))
    # Conserve les valeurs existantes si le champ n'a pas été fourni
    if not due:   due   = existing['due_date']
    if not location: location = existing['location']
    if is_transfer:
        if not transfer_no:
            transfer_no = existing['transfer_number']
        if transfer_no:
            title = f"Transfert #{transfer_no.lstrip('#')}"
        elif not title:
            title = existing['title']
    else:
        if not title: title = existing['title']
        transfer_no = None
    db.execute(
        'UPDATE task SET title=?, due_date=?, priority=?, location=?, '
        'is_transfer=?, transfer_number=? WHERE id=?',
        (title, due, priority, location, is_transfer, transfer_no, tid))
    db.commit()
    flash('Tâche modifiée.', 'success')
    return redirect(request.referrer or url_for('admin_view', token=token))


# ── Récurrentes (onglet dédié) ────────────────────────────────────────────
@app.route('/a/<token>/recurring')
def admin_recurring(token):
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    td = today_iso()
    recurring = db.execute(
        'SELECT * FROM recurring ORDER BY active DESC, id ASC').fetchall()
    # quel skip est posé pour aujourd'hui ?
    skips_today = {r['recurring_id'] for r in db.execute(
        'SELECT recurring_id FROM recurring_skip WHERE skip_date=?', (td,)).fetchall()}
    return render_template('recurring.html',
                           recurring=recurring,
                           skips_today=skips_today,
                           token=token,
                           today=td,
                           days_label=DAYS_LABEL,
                           locations=LOCATIONS)


@app.route('/a/<token>/recurring/add', methods=['POST'])
def admin_recurring_add(token):
    require_token(token, 'ADMIN_TOKEN')
    title    = (request.form.get('title') or '').strip()
    days     = request.form.getlist('days')
    priority = 1 if request.form.get('priority') else 0
    location = normalize_location(request.form.get('location'))
    if not title or not days or not location:
        return redirect(url_for('admin_recurring', token=token))
    days_mask = ','.join(sorted(set(days)))
    db = get_db()
    db.execute(
        'INSERT INTO recurring(title, days_mask, active, priority, location, created_at) '
        'VALUES (?,?,1,?,?,?)',
        (title, days_mask, priority, location, now_iso()))
    db.commit()
    return redirect(url_for('admin_recurring', token=token))


@app.route('/a/<token>/recurring/edit/<int:rid>', methods=['POST'])
def admin_recurring_edit(token, rid):
    require_token(token, 'ADMIN_TOKEN')
    title    = (request.form.get('title') or '').strip()
    days     = request.form.getlist('days')
    priority = 1 if request.form.get('priority') else 0
    location = normalize_location(request.form.get('location'))
    if not title or not days or not location:
        return redirect(url_for('admin_recurring', token=token))
    days_mask = ','.join(sorted(set(days)))
    db = get_db()
    db.execute(
        'UPDATE recurring SET title=?, days_mask=?, priority=?, location=? WHERE id=?',
        (title, days_mask, priority, location, rid))
    db.commit()
    return redirect(url_for('admin_recurring', token=token))


@app.route('/a/<token>/recurring/toggle/<int:rid>', methods=['POST'])
def admin_recurring_toggle(token, rid):
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    db.execute('UPDATE recurring SET active = 1 - active WHERE id=?', (rid,))
    db.commit()
    return redirect(url_for('admin_recurring', token=token))


@app.route('/a/<token>/recurring/del/<int:rid>', methods=['POST'])
def admin_recurring_delete(token, rid):
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    db.execute('DELETE FROM recurring WHERE id=?', (rid,))
    db.execute('DELETE FROM recurring_skip WHERE recurring_id=?', (rid,))
    db.commit()
    return redirect(url_for('admin_recurring', token=token))


@app.route('/a/<token>/recurring/skip', methods=['POST'])
def admin_recurring_skip(token):
    """Pose un 'skip' pour 1 jour donné (ex. employé en congés).
    Si la récurrente est déjà skippée pour ce jour, on lève le skip."""
    require_token(token, 'ADMIN_TOKEN')
    rid       = int(request.form.get('rid'))
    skip_date = (request.form.get('skip_date') or '').strip()
    if not skip_date:
        return redirect(url_for('admin_recurring', token=token))
    db = get_db()
    existing = db.execute(
        'SELECT id FROM recurring_skip WHERE recurring_id=? AND skip_date=?',
        (rid, skip_date)).fetchone()
    if existing:
        db.execute('DELETE FROM recurring_skip WHERE id=?', (existing['id'],))
        # supprime aussi la tâche déjà générée pour ce jour, si elle existe et n'est pas faite
        db.execute(
            'DELETE FROM task WHERE recurring_id=? AND gen_date=? AND done_at IS NULL',
            (rid, skip_date))
    else:
        db.execute(
            'INSERT INTO recurring_skip(recurring_id, skip_date, created_at) VALUES (?,?,?)',
            (rid, skip_date, now_iso()))
        db.execute(
            'DELETE FROM task WHERE recurring_id=? AND gen_date=? AND done_at IS NULL',
            (rid, skip_date))
    db.commit()
    return redirect(url_for('admin_recurring', token=token))


# ── Calendrier admin ──────────────────────────────────────────────────────
@app.route('/a/<token>/calendar')
def admin_calendar(token):
    require_token(token, 'ADMIN_TOKEN')
    days, today = _next_days(3)
    return render_template('calendar.html',
                           days=days, token=token, today=today,
                           is_admin=True)


# ── Suivi ─────────────────────────────────────────────────────────────────
@app.route('/a/<token>/follow')
def admin_follow(token):
    require_token(token, 'ADMIN_TOKEN')
    db = get_db()
    today = date.today()
    td = today.isoformat()
    yesterday  = today - timedelta(days=1)
    day_before = today - timedelta(days=2)

    has_overdue = bool(db.execute(
        'SELECT 1 FROM task WHERE done_at IS NULL AND due_date<? LIMIT 1',
        (td,)).fetchone())

    open_today_rows = db.execute(
        'SELECT * FROM task WHERE done_at IS NULL AND (due_date<=? OR due_date IS NULL) '
        'ORDER BY priority DESC, due_date ASC, id ASC', (td,)).fetchall()
    open_view = []
    for t in open_today_rows:
        d = dict(t)
        d['is_waiting']  = bool(t['due_date'] and t['due_date'] < td)
        d['is_priority'] = bool(t['priority'])
        d['delay_days']  = compute_delay_days(t)
        open_view.append(d)

    done_today = db.execute(
        "SELECT * FROM task WHERE date(done_at)=? ORDER BY done_at ASC",
        (td,)).fetchall()
    done_yesterday = db.execute(
        "SELECT * FROM task WHERE date(done_at)=? ORDER BY done_at ASC",
        (yesterday.isoformat(),)).fetchall()
    done_day_before = db.execute(
        "SELECT * FROM task WHERE date(done_at)=? ORDER BY done_at ASC",
        (day_before.isoformat(),)).fetchall()

    total = len(open_today_rows) + len(done_today)
    pct_done = round(100 * len(done_today) / total) if total else 0
    return render_template('follow.html',
                           open_today=open_view,
                           done_today=done_today,
                           done_yesterday=done_yesterday,
                           done_day_before=done_day_before,
                           yesterday_label=fr_long(yesterday).capitalize(),
                           day_before_label=fr_long(day_before).capitalize(),
                           total=total,
                           pct_done=pct_done,
                           has_overdue=has_overdue,
                           has_today=bool(open_today_rows),
                           token=token,
                           today=td,
                           today_label=fr_long(today).capitalize())


# ── Landing ───────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return ('AOLA Tasks. Utilise ton lien personnel.', 200,
            {'Content-Type': 'text/plain; charset=utf-8'})


if __name__ == '__main__':
    app.run(debug=True, port=5050)
