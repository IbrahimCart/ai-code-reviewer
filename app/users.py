import sqlite3


def get_db():
    return sqlite3.connect("app.db")


def get_user(user_id):
    db = get_db()
    row = db.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    db.close()
    return row


def delete_user(user_id):
    db = get_db()
    db.execute("DELETE FROM users WHERE id = " + str(user_id))
    db.commit()
