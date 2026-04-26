import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from branches.models import Branch
from products.models import Product, Stock
from sales.models import CreditAccount, CreditTransaction, Sale, Shift


class CreditSalesFlowTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Wendani",
            code="WEN",
            address="Wendani",
            phone_number="0700000001",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="cashier_credit",
            password="pass12345",
            role="cashier",
            branch=self.branch,
        )
        self.product = Product.objects.create(
            name="Credit Test Product",
            barcode="CREDIT-TEST-001",
            unit_price=Decimal("120.00"),
            cost_price=Decimal("80.00"),
            reorder_level=5,
            max_stock=50,
            pack_quantity=1,
            is_active=True,
        )
        Stock.objects.create(product=self.product, branch=self.branch, quantity=30)
        self.shift = Shift.objects.create(cashier=self.user, branch=self.branch, opening_cash=Decimal("0.00"))
        self.client.force_login(self.user)

    def _sale_payload(self, **overrides):
        payload = {
            "payment_method": "credit",
            "cash_amount": "0.00",
            "mpesa_amount": "0.00",
            "credit_amount": "120.00",
            "customer_name": "Jane Doe",
            "customer_phone": "0712345678",
            "cart_json": json.dumps(
                [
                    {
                        "id": self.product.id,
                        "name": self.product.name,
                        "price": "120.00",
                        "quantity": 1,
                    }
                ]
            ),
        }
        payload.update(overrides)
        return payload

    def test_process_credit_sale_creates_account_and_charge_transaction(self):
        response = self.client.post(reverse("process-sale"), data=self._sale_payload())

        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get()
        self.assertEqual(sale.payment_method, "credit")
        self.assertEqual(sale.credit_amount, Decimal("120.00"))
        self.assertEqual(sale.cash_amount, Decimal("0.00"))
        self.assertEqual(sale.mpesa_amount, Decimal("0.00"))

        account = CreditAccount.objects.get(branch=self.branch)
        self.assertEqual(account.customer_name, "Jane Doe")
        self.assertEqual(account.outstanding_balance, Decimal("120.00"))

        transaction = CreditTransaction.objects.get(account=account)
        self.assertEqual(transaction.transaction_type, CreditTransaction.TYPE_CHARGE)
        self.assertEqual(transaction.amount, Decimal("120.00"))
        self.assertEqual(transaction.sale_id, sale.id)

    def test_mixed_payment_with_credit_requires_customer_details(self):
        response = self.client.post(
            reverse("process-sale"),
            data=self._sale_payload(
                payment_method="mixed",
                cash_amount="20.00",
                mpesa_amount="30.00",
                credit_amount="70.00",
                customer_name="",
                customer_phone="",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required for credit sales")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(CreditAccount.objects.count(), 0)

    def test_record_credit_repayment_reduces_outstanding_balance(self):
        account = CreditAccount.objects.create(
            branch=self.branch,
            customer_name="Jane Doe",
            customer_phone="+254712345678",
            outstanding_balance=Decimal("300.00"),
        )

        response = self.client.post(
            reverse("credit-repayment", kwargs={"account_id": account.id}),
            data={
                "account_id": str(account.id),
                "branch_id": str(self.branch.id),
                "amount": "120.00",
                "payment_method": "cash",
                "notes": "Part payment",
            },
        )

        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.outstanding_balance, Decimal("180.00"))

        repayment = CreditTransaction.objects.get(account=account, transaction_type=CreditTransaction.TYPE_REPAYMENT)
        self.assertEqual(repayment.amount, Decimal("120.00"))
        self.assertEqual(repayment.payment_method, CreditTransaction.PAYMENT_METHOD_CASH)

    def test_record_credit_repayment_rejects_overpayment(self):
        account = CreditAccount.objects.create(
            branch=self.branch,
            customer_name="John Credit",
            customer_phone="+254700000111",
            outstanding_balance=Decimal("80.00"),
        )

        response = self.client.post(
            reverse("credit-repayment", kwargs={"account_id": account.id}),
            data={
                "account_id": str(account.id),
                "branch_id": str(self.branch.id),
                "amount": "100.00",
                "payment_method": "mpesa",
                "notes": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot exceed outstanding balance")
        account.refresh_from_db()
        self.assertEqual(account.outstanding_balance, Decimal("80.00"))
        self.assertFalse(
            CreditTransaction.objects.filter(
                account=account,
                transaction_type=CreditTransaction.TYPE_REPAYMENT,
            ).exists()
        )
