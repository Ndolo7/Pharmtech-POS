# Pharmtech POS

A full-stack pharmacy point-of-sale system built with **Next.js 15** (frontend) and **Django REST Framework** (backend).

```
Pharmtech-POS/
├── frontend/   ← Next.js 15 + shadcn/ui + Tailwind CSS
└── backend/    ← Django 4.2 REST API + PostgreSQL
```

---

## Getting Started

### Prerequisites
- Node.js >= 18 / npm
- Python ≥ 3.10
- PostgreSQL

---

### Backend

```bash
cd backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env              # Edit .env with your DB credentials

# Run migrations and start
python manage.py migrate
python manage.py createsuperuser  # Optional
python manage.py runserver        # http://localhost:8000
```

---

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Configure environment
cp .env.local.example .env.local  # Edit NEXT_PUBLIC_API_URL if needed

# Start dev server
npm run dev                       # http://localhost:3000
```

---

## API Overview

| Prefix | App |
|---|---|
| `/api/auth/` | accounts |
| `/api/products/` | products |
| `/api/sales/` | sales |
| `/api/reports/` | reports |
| `/api/branches/` | branches |
| `/admin/` | Django Admin |
