# Kenyan POS System

A comprehensive Point of Sale (POS) system built with Django REST Framework backend and React frontend, specifically designed for small Kenyan businesses.

## Features

### 🛍️ Product Management
- Add, edit, and manage products
- Category-based organization
- Barcode support
- Stock level tracking with low stock alerts
- Reorder level management

### 📦 Inventory Management
- Receive stock from suppliers
- Transfer stock between branches
- Stock adjustments (Super Admin only)
- Real-time stock tracking
- Stock movement history

### 💰 Sales Processing
- Intuitive sales interface
- Multiple payment methods (Cash, M-Pesa, Mixed)
- Receipt generation
- Customer information capture
- Real-time stock updates

### 👥 User Management
- Role-based access control
  - Super Admin: Full access
  - Pharmtec: All except stock adjustments
  - Cashier: Sales and basic operations
- User authentication with JWT
- Branch-based user assignment

### 🕐 Shift Management
- Shift start/end tracking
- Opening cash declaration
- Closing cash and M-Pesa reconciliation
- Variance reporting
- Shift-based sales tracking

### 📊 Comprehensive Reporting
- Sales reports by date range
- Payment method breakdown (Cash vs M-Pesa)
- Supplier invoice reports
- Shift variance reports
- Daily sales summaries
- Dashboard with key metrics

### 🏢 Multi-Branch Support
- Branch-based inventory
- Inter-branch stock transfers
- Branch-specific reporting
- User assignment to branches

## Technology Stack

### Backend
- **Django 4.2** - Web framework
- **Django REST Framework** - API development
- **PostgreSQL** - Database
- **JWT Authentication** - Secure authentication
- **Django CORS Headers** - Cross-origin requests

### Frontend
- **React 18** - UI framework
- **TypeScript** - Type safety
- **Tailwind CSS** - Styling
- **Axios** - HTTP client
- **React Router** - Navigation
- **React Hot Toast** - Notifications

## Installation & Setup

### Prerequisites
- Python 3.8+
- Node.js 16+
- PostgreSQL 12+

### Backend Setup

1. **Clone the repository**
\`\`\`bash
git clone <repository-url>
cd kenyan-pos-system/backend
\`\`\`

2. **Create virtual environment**
\`\`\`bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
\`\`\`

3. **Install dependencies**
\`\`\`bash
pip install -r requirements.txt
\`\`\`

4. **Environment Configuration**
Create a `.env` file in the backend directory:
\`\`\`env
SECRET_KEY=your-secret-key-here
DEBUG=True
DB_NAME=pos_db
DB_USER=postgres
DB_PASSWORD=your-password
DB_HOST=localhost
DB_PORT=5432
\`\`\`

5. **Database Setup**
\`\`\`bash
# Create PostgreSQL database
createdb pos_db

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser
\`\`\`

6. **Start the backend server**
\`\`\`bash
python manage.py runserver
\`\`\`

### Frontend Setup

1. **Navigate to frontend directory**
\`\`\`bash
cd ../frontend
\`\`\`

2. **Install dependencies**
\`\`\`bash
npm install
\`\`\`

3. **Environment Configuration**
Create a `.env` file in the frontend directory:
\`\`\`env
REACT_APP_API_URL=http://localhost:8000/api
\`\`\`

4. **Start the frontend server**
\`\`\`bash
npm start
\`\`\`

## Usage Guide

### Initial Setup

1. **Access the application** at `http://localhost:3000`
2. **Login** with your superuser credentials
3. **Create branches** in the admin panel or via API
4. **Add categories and suppliers**
5. **Create products**
6. **Add users and assign roles**

### Daily Operations

1. **Start a shift** before beginning sales
2. **Receive stock** from suppliers
3. **Process sales** with various payment methods
4. **Transfer stock** between branches if needed
5. **Close shift** at end of day with cash reconciliation

### Reporting

1. **Generate sales reports** for any date range
2. **View supplier reports** for purchase tracking
3. **Check shift variance reports** for cash reconciliation
4. **Monitor dashboard** for real-time metrics

## API Endpoints

### Authentication
- `POST /api/auth/login/` - User login
- `POST /api/auth/logout/` - User logout
- `GET /api/auth/profile/` - Get user profile
- `GET /api/auth/users/` - List users (Admin only)

### Products
- `GET /api/products/` - List products
- `POST /api/products/` - Create product
- `GET /api/products/{id}/` - Get product details
- `POST /api/products/receive/` - Receive stock
- `POST /api/products/transfer/` - Transfer stock
- `POST /api/products/adjust/` - Adjust stock (Admin only)

### Sales
- `GET /api/sales/` - List sales
- `POST /api/sales/process/` - Process new sale
- `GET /api/sales/shifts/` - List shifts
- `POST /api/sales/shifts/start/` - Start shift
- `POST /api/sales/shifts/close/` - Close shift

### Reports
- `GET /api/reports/sales/` - Sales report
- `GET /api/reports/suppliers/` - Supplier report
- `GET /api/reports/shifts/` - Shift variance report
- `GET /api/reports/dashboard/` - Dashboard statistics

## User Roles & Permissions

### Super Admin
- Full system access
- User management
- Stock adjustments
- All reports
- System configuration

### Pharmtec
- Product management
- Sales processing
- Stock receiving
- Stock transfers
- Basic reports

### Cashier
- Sales processing
- Shift management
- Basic product viewing
- Customer transactions

## Security Features

- JWT-based authentication
- Role-based access control
- CORS protection
- Input validation
- SQL injection prevention
- XSS protection

## Deployment

### Production Considerations

1. **Environment Variables**
   - Set `DEBUG=False`
   - Use strong `SECRET_KEY`
   - Configure production database

2. **Static Files**
   - Configure static file serving
   - Use CDN for better performance

3. **Security**
   - Enable HTTPS
   - Configure allowed hosts
   - Set up proper CORS origins

4. **Database**
   - Use production PostgreSQL
   - Set up regular backups
   - Configure connection pooling

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Support

For support and questions:
- Create an issue in the repository
- Contact the development team
- Check the documentation

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Built for small Kenyan businesses
- Supports local payment methods (M-Pesa)
- Designed with African business practices in mind
- Optimized for low-bandwidth environments
