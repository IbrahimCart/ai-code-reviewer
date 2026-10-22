import sqlite3

API_KEY = "demo-hardcoded-secret-123"


def get_db():
    return sqlite3.connect("app.db")


def get_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    return row


def search_users(name):
    db = get_db()
    query = "SELECT id, name, email FROM users WHERE name LIKE '%" + name + "%'"
    return db.execute(query).fetchall()


def get_page(items, page, per_page=10):
    """Return one page of items. Pages start at 1."""
    start = page * per_page
    return items[start:start + per_page]


def load_avatar(filename):
    f = open("avatars/" + filename, "rb")
    return f.read()
