# Pharmtech POS

Pharmtech POS is a Django-based pharmacy point-of-sale and stock management system.  
It includes multi-branch inventory, cashier shifts, sales processing, reporting, and automated low-stock reorder workflows via Celery.

## Tech Stack
- Django 4.2
- SQLite (development) / PostgreSQL (production)
- HTMX + Alpine.js templates
- Celery + Redis (background jobs)

## Project Structure
```text
Pharmtech-POS/
├── pos_system/        # Django project config (settings, urls, celery)
├── accounts/          # Authentication and user/role management
├── branches/          # Branch management
├── products/          # Products, stock, suppliers, auto-reorder logic
├── sales/             # POS checkout and cashier shifts
├── reports/           # Dashboard and operational reports
├── templates/         # Server-rendered templates
├── static/            # CSS/JS/vendor assets
├── manage.py
└── requirements.txt
```

## Prerequisites
- Python 3.10+
- `pip`
- Redis (required for Celery worker/beat)
- PostgreSQL (only when running `DJANGO_ENV=prod`)

## Setup
```bash
# 1) Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) Configure environment variables
cp .env.example .env  # if available, otherwise create .env manually

# 4) Run database migrations
python manage.py migrate

# 5) Create an admin user (optional but recommended)
python manage.py createsuperuser
```

## Environment Configuration
`manage.py` and Celery both read `DJANGO_ENV`:
- `DJANGO_ENV=dev` (default) uses SQLite (`pos_system/settings/dev.py`)
- `DJANGO_ENV=prod` uses PostgreSQL (`pos_system/settings/prod.py`)

Common `.env` values:
```env
DJANGO_ENV=dev
DEBUG=True
SECRET_KEY=change-me
ALLOWED_HOSTS=localhost,127.0.0.1

# Production DB setting (used when DJANGO_ENV=prod)
DATABASE_URL=postgres://postgres:password@localhost:5432/pos_db

# Celery / Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Brevo (used by Anymail in production)
BREVO_API_KEY=your_brevo_api_key
ADMIN_EMAIL=admin@example.com
ADMIN_PHONE=254700000001

# Automated reorder settings
SITE_BASE_URL=http://localhost:8000
AUTO_ORDER_CHECK_INTERVAL_MINUTES=15
AUTO_ORDER_LINK_EXPIRY_SECONDS=3600
AUTO_ORDER_EMAIL_FROM=noreply@pharmtech.local

# SMS Leopard (for supplier/admin SMS alerts)
SMS_LEOPARD_API_KEY=your_sms_leopard_api_key
SMS_LEOPARD_API_SECRET=your_sms_leopard_api_secret
SMS_LEOPARD_SOURCE=SMS_Leopard
```

Optional email settings for real supplier notifications:
```env
DEFAULT_FROM_EMAIL=noreply@pharmtech.local
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EMAIL_HOST=localhost
EMAIL_PORT=25
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=False
EMAIL_USE_SSL=False
```

## Running the App
```bash
python manage.py runserver
```

Open:
- `http://localhost:8000/` dashboard
- `http://localhost:8000/admin/` Django admin

## Running Background Jobs (Celery)
Start Redis, then run:

```bash
# Terminal 1: Celery worker
celery -A pos_system worker -l info

# Terminal 2: Celery beat scheduler
celery -A pos_system beat -l info
```

Scheduled task:
- `products.tasks.scan_low_stock_and_trigger_reorders` runs every `AUTO_ORDER_CHECK_INTERVAL_MINUTES`.

## Core URL Areas
- `/accounts/` login, profile, users
- `/products/` products, stock, suppliers, category management
- `/sales/` POS checkout and shift actions
- `/reports/` sales/supplier/shift reports
- `/branches/` branch management
- `/admin/` Django admin

## Notes
- Timezone is set to `Africa/Nairobi`.
- Static files are served from `static/` in development.
- Supplier reorder response links are tokenized (`/products/reorder/respond/<uuid>/`).
