from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime
import psycopg2

DB_CONFIG = {
    'dbname': 'fitzone_db',
    'user': 'postgres',
    'password': '28112008DNM',
    'host': 'localhost',
    'port': '5432'
}

def get_db():
    return psycopg2.connect(**DB_CONFIG)

app = Flask(__name__)
app.secret_key = 'fitzone_secret_key'

# Автоматическое создание таблицы уведомлений и полей для персональных тренировок
def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id SERIAL PRIMARY KEY,
                client_id INTEGER REFERENCES clients(id) ON DELETE CASCADE,
                message TEXT NOT NULL,
                is_read BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("ALTER TABLE bookings ALTER COLUMN workout_id DROP NOT NULL;")
        cursor.execute("ALTER TABLE bookings ADD COLUMN IF NOT EXISTS custom_info TEXT;")
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print("DB init note:", e)

init_db()

# --- СТРАНИЦЫ ---
@app.route('/')
def index():
    notifications = []
    unread_count = 0
    if 'user_id' in session:
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, message, is_read, created_at FROM notifications WHERE client_id = %s ORDER BY created_at DESC LIMIT 20",
                (session['user_id'],)
            )
            rows = cursor.fetchall()
            for r in rows:
                notifications.append({
                    'id': r[0],
                    'message': r[1],
                    'is_read': r[2],
                    'created_at': r[3].strftime("%d.%m.%Y %H:%M") if r[3] else ""
                })
                if not r[2]:
                    unread_count += 1
            cursor.close()
            conn.close()
        except Exception as e:
            print("Notif load error:", e)
    return render_template('index.html', notifications=notifications, unread_count=unread_count)

@app.route('/manager')
def manager_panel():
    if session.get('role') != 'manager':
        return redirect(url_for('index'))
    return render_template('manager.html')

@app.route('/admin')
def admin_panel():
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    return render_template('admin.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


# --- API АВТОРИЗАЦИИ И РЕГИСТРАЦИИ ---
@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, fullname, role FROM clients WHERE phone = %s AND password = %s", (data.get('login'), data.get('password')))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user:
            session['user_id'] = user[0]
            session['user_name'] = user[1]
            session['role'] = user[2]
            return jsonify({'status': 'success', 'role': user[2]}), 200
        else:
            return jsonify({'status': 'error', 'message': 'Неверный телефон или пароль'}), 401
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/register', methods=['POST'])
def register():
    data = request.get_json()
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO clients (fullname, phone, tariff, trainer_note, password, role) VALUES (%s, %s, %s, %s, %s, 'user')",
            (data.get('fullname'), data.get('phone'), data.get('tariff'), data.get('trainer_note'), data.get('password'))
        )
        new_user = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        # Автоматический вход сразу после регистрации
        if new_user:
            session['user_id'] = new_user[0]
            session['user_name'] = data.get('fullname')
            session['role'] = 'user'

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500



# --- API РАСПИСАНИЯ ---
@app.route('/api/schedule', methods=['GET', 'POST', 'DELETE'])
def api_schedule():
    conn = get_db()
    cursor = conn.cursor()

    if request.method == 'GET':
        cursor.execute("SELECT id, title, trainer, room, event_time, free_seats FROM schedule ORDER BY event_time ASC")
        rows = cursor.fetchall()
        schedule_list = []
        for r in rows:
            formatted_time = r[4].strftime("%d.%m.%Y %H:%M") if r[4] else ""
            schedule_list.append({
                'id': r[0],
                'title': r[1],
                'trainer': r[2],
                'room': r[3],
                'event_time': formatted_time,
                'free_seats': r[5]
            })
        cursor.close()
        conn.close()
        return jsonify(schedule_list)

    elif request.method == 'POST':
        data = request.json
        title = data.get('title')
        trainer = data.get('trainer')
        room = data.get('room')
        event_time = data.get('event_time')
        free_seats = data.get('free_seats', 15)

        try:
            event_time = datetime.strptime(event_time, "%d.%m.%Y %H:%M")
        except ValueError:
            pass

        cursor.execute(
            "INSERT INTO schedule (title, trainer, room, event_time, free_seats) VALUES (%s, %s, %s, %s, %s)",
            (title, trainer, room, event_time, free_seats)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'success'})

    elif request.method == 'DELETE':
        workout_id = request.args.get('id')
        cursor.execute("DELETE FROM schedule WHERE id = %s", (workout_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'success'})


# --- API ЗАПИСИ НА ЗАНЯТИЕ (УМЕНЬШЕНИЕ МЕСТ) ---
@app.route('/api/book', methods=['POST'])
def book_workout():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Требуется авторизация'}), 401
    
    data = request.get_json()
    workout_id = data.get('workout_id')
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT free_seats FROM schedule WHERE id = %s", (workout_id,))
        workout = cursor.fetchone()
        
        if not workout or workout[0] <= 0:
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'Нет свободных мест'}), 400
  
        cursor.execute(
            "SELECT id FROM bookings WHERE client_id = %s AND workout_id = %s AND status != 'Отклонено'",
            (user_id, workout_id)
        )
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'Вы уже записаны на эту тренировку!'}), 400
   
        # В функции book_workout:
        cursor.execute("UPDATE schedule SET free_seats = free_seats - 1 WHERE id = %s", (workout_id,))
        cursor.execute(
            "INSERT INTO bookings (client_id, workout_id, status) VALUES (%s, %s, 'Ожидает')",
            (user_id, workout_id)
        )

        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- API ЗАПИСИ НА ПЕРСОНАЛЬНЫЙ ТРЕНИНГ ---
@app.route('/api/book-personal', methods=['POST'])
def book_personal():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Требуется авторизация'}), 401

    data = request.get_json()
    trainer = data.get('trainer')
    dt_raw = data.get('datetime', '').replace('T', ' ')
    goal = data.get('goal', '').strip()
    info = f"Персональная: {trainer} ({dt_raw})" + (f" — {goal}" if goal else "")

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bookings (client_id, workout_id, status, custom_info) VALUES (%s, NULL, 'Ожидает', %s)",
            (session['user_id'], info)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# --- API ОТМЕТКИ УВЕДОМЛЕНИЙ КАК ПРОЧИТАННЫХ ---
@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user_id' in session:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET is_read = TRUE WHERE client_id = %s", (session['user_id'],))
        conn.commit()
        cursor.close()
        conn.close()
    return jsonify({'status': 'success'}), 200

# --- API ЗАПИСЕЙ ТЕКУЩЕГО ПОЛЬЗОВАТЕЛЯ ("МОИ ТРЕНИРОВКИ") ---
@app.route('/api/my-bookings')
def api_my_bookings():
    if 'user_id' not in session:
        return jsonify([]), 401
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT b.id,
               COALESCE(s.title, b.custom_info, 'Тренировка') AS title,
               COALESCE(TO_CHAR(s.event_time, 'DD.MM.YYYY HH24:MI'), 'По согласованию') AS event_time,
               COALESCE(s.trainer, 'Персональный тренер') AS trainer,
               b.status
        FROM bookings b
        LEFT JOIN schedule s ON b.workout_id = s.id
        WHERE b.client_id = %s
        ORDER BY b.id DESC
    """, (session['user_id'],))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify([
        {'id': r[0], 'title': r[1], 'time': r[2], 'trainer': r[3], 'status': r[4]}
        for r in rows
    ])


# --- API АДМИНИСТРАТОРА ---
@app.route('/api/admin/bookings')
def api_admin_bookings():
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT b.id, c.fullname, c.phone, c.tariff, c.trainer_note, s.title, b.status 
            FROM bookings b 
            JOIN clients c ON b.client_id = c.id 
            LEFT JOIN schedule s ON b.workout_id = s.id
        """)

    except Exception:
        conn.rollback()
        cursor.execute("""
            SELECT id, 'Клиент', '-', '-', '-', '-', 'Ожидает' FROM bookings
        """)
    rows = cursor.fetchall()
    bookings_list = []
    for r in rows:
        bookings_list.append({
            'id': r[0],
            'client_name': r[1],
            'phone': r[2],
            'tariff': r[3],
            'trainer_note': r[4],
            'workout_title': r[5],
            'status': r[6] if len(r) > 6 else 'Ожидает'
        })
    cursor.close()
    conn.close()
    return jsonify(bookings_list)

@app.route('/api/admin/booking/<int:booking_id>', methods=['POST'])
def update_booking_status(booking_id):
    if session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Доступ запрещен'}), 403
        
    data = request.get_json()
    action = data.get('action')
    
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT b.client_id, b.workout_id, COALESCE(s.title, b.custom_info, 'Тренировка')
        FROM bookings b
        LEFT JOIN schedule s ON b.workout_id = s.id
        WHERE b.id = %s
    """, (booking_id,))
    booking_info = cursor.fetchone()
    
    if booking_info:
        client_id, workout_id, workout_title = booking_info
        if action == 'confirm':
            cursor.execute("UPDATE bookings SET status = 'Подтверждено' WHERE id = %s", (booking_id,))
            notif_text = f"✅ Ваша запись «{workout_title}» подтверждена администратором!"
            cursor.execute("INSERT INTO notifications (client_id, message) VALUES (%s, %s)", (client_id, notif_text))
        elif action == 'reject':
            if workout_id:
                cursor.execute("UPDATE schedule SET free_seats = free_seats + 1 WHERE id = %s", (workout_id,))
            cursor.execute("UPDATE bookings SET status = 'Отклонено' WHERE id = %s", (booking_id,))
            notif_text = f"❌ К сожалению, ваша запись «{workout_title}» была отклонена."
            cursor.execute("INSERT INTO notifications (client_id, message) VALUES (%s, %s)", (client_id, notif_text))
        
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'status': 'success'}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
