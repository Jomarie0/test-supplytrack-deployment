# apps/purchasing/management/commands/check_overdue_payments.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from apps.purchasing.models import PurchaseOrder
from apps.purchasing.utils import send_po_email, log_po_action, create_po_notification


class Command(BaseCommand):
    help = 'Check for overdue PO payments and send notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run without sending emails or updating statuses',
        )
        parser.add_argument(
            '--days-before',
            type=int,
            default=0,
            help='Number of days before due date to send warning (default: 0 = only check overdue)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days_before = options['days_before']
        today = timezone.now().date()
        
        # Find overdue POs
        overdue_pos = PurchaseOrder.objects.filter(
            payment_method='net_30',
            payment_due_date__lt=today,
            payment_status__in=['pending', 'partial'],
            is_deleted=False
        )
        
        # Find upcoming due POs (warning)
        warning_date = today + timezone.timedelta(days=days_before)
        upcoming_pos = PurchaseOrder.objects.filter(
            payment_method='net_30',
            payment_due_date__lte=warning_date,
            payment_due_date__gte=today,
            payment_status__in=['pending', 'partial'],
            is_deleted=False
        )
        
        self.stdout.write(self.style.WARNING(f'Date: {today}'))
        self.stdout.write(self.style.WARNING(f'Found {overdue_pos.count()} overdue POs'))
        self.stdout.write(self.style.WARNING(f'Found {upcoming_pos.count()} upcoming due POs'))
        
        # Process overdue POs
        for po in overdue_pos:
            self.stdout.write(f'\n{self.style.ERROR("OVERDUE")}: PO {po.purchase_order_id}')
            self.stdout.write(f'  Supplier: {po.supplier_profile.company_name if po.supplier_profile else "N/A"}')
            self.stdout.write(f'  Due Date: {po.payment_due_date}')
            self.stdout.write(f'  Days Overdue: {po.days_overdue}')
            self.stdout.write(f'  Balance: ₱{po.balance_due}')
            
            if not dry_run:
                # Update status
                old_status = po.payment_status
                po.payment_status = 'overdue'
                po.save(update_fields=['payment_status'])
                
                # Send notification
                email_sent = send_po_email(po, 'overdue')
                
                # Create notification
                create_po_notification(po, 'marked as overdue')
                
                # Log audit
                log_po_action(
                    purchase_order=po,
                    action='payment_overdue',
                    user=None,  # System action
                    notes=f'Payment overdue by {po.days_overdue} days',
                    previous_data={'payment_status': old_status},
                    new_data={'payment_status': 'overdue'}
                )
                
                status = self.style.SUCCESS('✓ Email sent') if email_sent else self.style.ERROR('✗ Email failed')
                self.stdout.write(f'  {status}')
        
        # Process upcoming due POs (warnings)
        if days_before > 0:
            self.stdout.write(f'\n{self.style.WARNING("=" * 50)}')
            self.stdout.write(self.style.WARNING('UPCOMING DUE PAYMENTS'))
            
            for po in upcoming_pos:
                days_until = (po.payment_due_date - today).days
                self.stdout.write(f'\n{self.style.WARNING("DUE SOON")}: PO {po.purchase_order_id}')
                self.stdout.write(f'  Due in: {days_until} days')
                self.stdout.write(f'  Amount: ₱{po.balance_due}')
                
                if not dry_run:
                    # Send reminder email (you'd need to create this template)
                    # send_po_email(po, 'payment_reminder')
                    create_po_notification(po, f'payment due in {days_until} days')
        
        # Summary
        self.stdout.write('\n' + '=' * 50)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes made'))
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Processed {overdue_pos.count()} overdue POs')
            )
        
        self.stdout.write(self.style.SUCCESS('Done!'))


# Add this to your crontab or Django-cron:
# */15 * * * * cd /path/to/project && python manage.py check_overdue_payments
# Or run daily at 9 AM:
# 0 9 * * * cd /path/to/project && python manage.py check_overdue_payments --days-before=7