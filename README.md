# Cine Reserve 🎬

Cine Reserve is a Django-based cinema ticket reservation system that allows users to browse movies and reserve cinema tickets.

## Tech Stack

* Python
* Django
* SQLite / Database configured in Django
* HTML, CSS, JavaScript
* Django Templates

## Project Structure

```text
cine-reserve/
├── config/
├── core/
├── manage.py
└── .gitignore
```

## Getting Started

Requires **Python 3.14+** (the reservation IDs use `uuid.uuid7`). There is no Redis,
Celery or any other service to run: just Django and SQLite.

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then add your Khalti key (see below)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ (admin at `/admin/`).

## Payments (Khalti)

1. Create a sandbox merchant at https://test-admin.khalti.com and copy the **secret key**.
2. Put it in `.env` as `KHALTI_API_SECRET_KEY=...`.
3. Book seats. You'll be redirected to Khalti. In the sandbox you can pay with
   test mobile `9800000000`–`9800000005`, MPIN `1111`, OTP `987654`.
4. For real payments use your live key and set `KHALTI_LIVE=True`.

How it works: choosing seats holds them for `RESERVATION_WINDOW_TIME` minutes
(`config/settings.py`) and opens a Khalti payment. When Khalti sends the customer
back, the server asks Khalti whether the payment really completed before
confirming the seats. Unpaid seats are released automatically the next time
someone opens a seat map or books. All of this lives in `core/services.py`.

Receipts (with QR code) are printed in the terminal until you set
`EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` in `.env`.

## Development Commands

Create migrations after changing models:

```bash
python manage.py makemigrations
```

Apply migrations:

```bash
python manage.py migrate
```

Run the development server:

```bash
python manage.py runserver
```

Create a Django superuser:

```bash
python manage.py createsuperuser
```

Run tests:

```bash
python manage.py test
```

Open the Django shell:

```bash
python manage.py shell
```

## Deactivate Virtual Environment

When you are finished working on the project:

```bash
deactivate
```

## Project

**Cine Reserve** — A cinema ticket reservation system built with Django.

Repository: https://github.com/prakashtaz0091/cine-reserve
