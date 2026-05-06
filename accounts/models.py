from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = [
        ('super_admin', 'Super Admin'),
        ('pharmtec', 'Pharmtec'),
        ('cashier', 'Cashier'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    phone_number = models.CharField(max_length=15, blank=True)
    branch = models.ForeignKey(
        'branches.Branch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='staff',
    )
    is_active_shift = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    groups = models.ManyToManyField(
        'auth.Group',
        blank=True,
        related_name='pos_users',
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        blank=True,
        related_name='pos_users',
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

    def __str__(self):
        return f"{self.username} - {self.get_role_display()}"

    def can_adjust_stock(self):
        return self.is_superuser or self.role == 'super_admin'

    def can_manage_users(self):
        return self.is_superuser or self.role == 'super_admin'

    def can_manage_branches(self):
        return self.is_superuser or self.role == 'super_admin'
