from flask import Flask, render_template, request, redirect, url_for, g, send_file, session, flash
import sqlite3
import os
import pandas as pd
from datetime import datetime
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'  # Измените на сложный ключ в продакшене
DATABASE = 'finance.db'


def get_db():
    if not hasattr(g, 'sqlite_db'):
        g.sqlite_db = sqlite3.connect(DATABASE)
        g.sqlite_db.row_factory = sqlite3.Row
    return g.sqlite_db


def init_db():
    with app.app_context():
        db = get_db()
        # Таблица пользователей
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Обновляем таблицу транзакций, добавляя user_id
        db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                date TEXT NOT NULL DEFAULT (CURRENT_DATE),
                description TEXT,
                balance_after REAL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        db.commit()


def update_all_balances(user_id):
    """Обновляет баланс для всех операций пользователя"""
    db = get_db()

    transactions = db.execute('''
        SELECT * FROM transactions 
        WHERE user_id = ?
        ORDER BY date ASC, id ASC
    ''', (user_id,)).fetchall()

    current_balance = 0

    for trans in transactions:
        if trans['type'] == 'income':
            current_balance += trans['amount']
        else:
            current_balance -= trans['amount']

        db.execute('''
            UPDATE transactions 
            SET balance_after = ?
            WHERE id = ?
        ''', (current_balance, trans['id']))

    db.commit()


def login_required(f):
    """Декоратор для проверки авторизации"""
    from functools import wraps

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Для доступа к этой странице необходимо войти в систему', 'error')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)

    return decorated_function


@app.teardown_appcontext
def close_db(error):
    if hasattr(g, 'sqlite_db'):
        g.sqlite_db.close()


# Маршруты аутентификации
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if password != confirm_password:
            flash('Пароли не совпадают', 'error')
            return redirect(url_for('register'))

        if len(password) < 4:
            flash('Пароль должен содержать минимум 4 символа', 'error')
            return redirect(url_for('register'))

        db = get_db()

        # Проверяем, существует ли пользователь
        existing_user = db.execute(
            'SELECT id FROM users WHERE username = ?', (username,)
        ).fetchone()

        if existing_user:
            flash('Пользователь с таким именем уже существует', 'error')
            return redirect(url_for('register'))

        # Сохраняем пользователя
        db.execute('''
            INSERT INTO users (username, password)
            VALUES (?, ?)
        ''', (username, password))  # В реальном приложении пароль нужно хэшировать!
        db.commit()

        flash('Регистрация успешна! Теперь вы можете войти.', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        db = get_db()
        user = db.execute(
            'SELECT * FROM users WHERE username = ? AND password = ?',
            (username, password)
        ).fetchone()

        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            flash('Вы успешно вошли в систему!', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'error')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))


# Обновляем все маршруты, добавляя декоратор @login_required
@app.route('/')
@login_required
def index():
    db = get_db()

    balance_row = db.execute('''
        SELECT 
            COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END), 0) as total_income,
            COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END), 0) as total_expense
        FROM transactions
        WHERE user_id = ?
    ''', (session['user_id'],)).fetchone()

    total_income = balance_row['total_income'] or 0
    total_expense = balance_row['total_expense'] or 0
    balance = total_income - total_expense

    chart_data = db.execute('''
        SELECT category, SUM(amount) as total 
        FROM transactions 
        WHERE type='expense' AND user_id = ?
        GROUP BY category
    ''', (session['user_id'],)).fetchall()

    return render_template('index.html',
                           balance=balance,
                           income=total_income,
                           expense=total_expense,
                           chart_data=chart_data)


@app.route('/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        db = get_db()
        amount = float(request.form['amount'])
        type_ = request.form['type']

        balance_row = db.execute('''
            SELECT balance_after 
            FROM transactions 
            WHERE user_id = ?
            ORDER BY id DESC 
            LIMIT 1
        ''', (session['user_id'],)).fetchone()

        current_balance = balance_row['balance_after'] if balance_row else 0

        if type_ == 'income':
            new_balance = current_balance + amount
        else:
            new_balance = current_balance - amount

        db.execute('''
            INSERT INTO transactions (user_id, type, category, amount, description, balance_after)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            session['user_id'],
            type_,
            request.form['category'],
            amount,
            request.form.get('description', ''),
            new_balance
        ))
        db.commit()
        return redirect(url_for('index'))

    return render_template('add.html')


@app.route('/history')
@login_required
def history():
    db = get_db()
    transactions = db.execute('''
        SELECT *, 
               CASE 
                   WHEN type='income' THEN '+' 
                   ELSE '-' 
               END as sign
        FROM transactions 
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
    ''', (session['user_id'],)).fetchall()
    return render_template('history.html', transactions=transactions)


@app.route('/delete/<int:transaction_id>')
@login_required
def delete_transaction(transaction_id):
    db = get_db()

    # Проверяем, что транзакция принадлежит текущему пользователю
    transaction = db.execute('SELECT user_id FROM transactions WHERE id = ?',
                             (transaction_id,)).fetchone()

    if transaction and transaction['user_id'] == session['user_id']:
        db.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))
        db.commit()
        update_all_balances(session['user_id'])
        flash('Операция удалена', 'success')
    else:
        flash('Ошибка удаления операции', 'error')

    return redirect(url_for('history'))


@app.route('/export')
@login_required
def export_excel():
    """Экспорт всех операций в Excel"""
    db = get_db()

    transactions = db.execute('''
        SELECT date, type, category, amount, description, balance_after 
        FROM transactions 
        WHERE user_id = ?
        ORDER BY date ASC, id ASC
    ''', (session['user_id'],)).fetchall()

    export_data = []
    for t in transactions:
        try:
            date_obj = datetime.strptime(t['date'], '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d.%m.%Y')
        except:
            formatted_date = t['date']

        type_russian = 'Доход' if t['type'] == 'income' else 'Расход'
        amount_value = t['amount']

        if t['type'] == 'expense':
            amount_display = f"-{amount_value:.2f}"
        else:
            amount_display = f"{amount_value:.2f}"

        export_data.append({
            'Дата': formatted_date,
            'Тип': type_russian,
            'Категория': t['category'],
            'Сумма': amount_display,
            'Описание': t['description'] or '',
            'Баланс': f"{t['balance_after']:.2f}"
        })

    df = pd.DataFrame(export_data)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Операции', index=False)

        total_income = sum(t['amount'] for t in transactions if t['type'] == 'income')
        total_expense = sum(t['amount'] for t in transactions if t['type'] == 'expense')
        balance = total_income - total_expense

        summary_df = pd.DataFrame([{
            'Общий доход': f"{total_income:.2f}",
            'Общие расходы': f"{total_expense:.2f}",
            'Итоговый баланс': f"{balance:.2f}"
        }])
        summary_df.to_excel(writer, sheet_name='Сводка', index=False)

    output.seek(0)

    filename = f'финансы_{session["username"]}_{datetime.now().strftime("%d%m%Y_%H%M%S")}.xlsx'
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/import_export', methods=['GET', 'POST'])
@login_required
def import_export():
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)

        if file and file.filename.endswith(('.xlsx', '.xls')):
            try:
                df = pd.read_excel(file)

                required_columns = ['Дата', 'Тип', 'Категория', 'Сумма']
                if not all(col in df.columns for col in required_columns):
                    return render_template('import_export.html',
                                           error="Файл должен содержать колонки: Дата, Тип, Категория, Сумма")

                db = get_db()
                added_count = 0
                errors = []

                for index, row in df.iterrows():
                    if pd.isna(row['Дата']) or pd.isna(row['Сумма']):
                        continue

                    try:
                        # Обработка даты
                        date_value = row['Дата']
                        if isinstance(date_value, str):
                            for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                                try:
                                    date_obj = datetime.strptime(str(date_value).strip(), fmt)
                                    date_str = date_obj.strftime('%Y-%m-%d')
                                    break
                                except:
                                    continue
                            else:
                                errors.append(f"Строка {index + 2}: Неверный формат даты")
                                continue
                        elif isinstance(date_value, pd.Timestamp):
                            date_str = date_value.strftime('%Y-%m-%d')
                        else:
                            errors.append(f"Строка {index + 2}: Неверный тип даты")
                            continue

                        # Обработка типа
                        type_value = str(row['Тип']).strip().lower()
                        if type_value in ['доход', 'income', 'д']:
                            type_db = 'income'
                        elif type_value in ['расход', 'expense', 'р']:
                            type_db = 'expense'
                        else:
                            errors.append(f"Строка {index + 2}: Неверный тип операции")
                            continue

                        # Обработка суммы
                        amount_str = str(row['Сумма']).replace(',', '.')
                        amount_str_clean = amount_str.replace('-', '').replace(' ', '')
                        try:
                            amount = float(amount_str_clean)
                        except:
                            errors.append(f"Строка {index + 2}: Неверный формат суммы")
                            continue

                        # Категория
                        category = str(row['Категория']).strip()

                        # Описание
                        description = row.get('Описание', '')
                        if pd.isna(description):
                            description = ''
                        else:
                            description = str(description).strip()

                        # Вставляем с нулевым балансом
                        db.execute('''
                            INSERT INTO transactions (user_id, date, type, category, amount, description, balance_after)
                            VALUES (?, ?, ?, ?, ?, ?, 0)
                        ''', (
                            session['user_id'],
                            date_str,
                            type_db,
                            category,
                            amount,
                            description
                        ))
                        added_count += 1

                    except Exception as e:
                        errors.append(f"Строка {index + 2}: {str(e)}")
                        continue

                db.commit()
                update_all_balances(session['user_id'])

                return render_template('import_export.html',
                                       success=True,
                                       count=added_count,
                                       errors=errors if errors else None)

            except Exception as e:
                return render_template('import_export.html',
                                       error=f"Ошибка при импорте: {str(e)}")

    return render_template('import_export.html')


@app.route('/download_template')
@login_required
def download_template():
    sample_data = [
        {
            'Дата': '15.01.2025',
            'Тип': 'Доход',
            'Категория': 'Зарплата',
            'Сумма': '50000.00',
            'Описание': 'Зарплата за январь'
        },
        {
            'Дата': '16.01.2025',
            'Тип': 'Расход',
            'Категория': 'Продукты',
            'Сумма': '-2500.50',
            'Описание': 'Покупка продуктов'
        }
    ]

    df = pd.DataFrame(sample_data)

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Операции', index=False)

        instructions_df = pd.DataFrame([{
            'Поле': 'Дата',
            'Формат': 'ДД.ММ.ГГГГ (например: 15.01.2025)',
            'Обязательно': 'Да'
        }, {
            'Поле': 'Тип',
            'Формат': 'Доход или Расход',
            'Обязательно': 'Да'
        }])
        instructions_df.to_excel(writer, sheet_name='Инструкция', index=False)

    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name='шаблон_для_импорта.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


def init_and_update_balances():
    """Инициализация базы данных и обновление балансов"""
    with app.app_context():
        init_db()
        # Для каждого пользователя обновляем балансы
        db = get_db()
        users = db.execute('SELECT id FROM users').fetchall()
        for user in users:
            update_all_balances(user['id'])


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    init_and_update_balances()
    app.run(host='0.0.0.0', port=port, debug=True)