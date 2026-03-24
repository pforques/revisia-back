from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Q, Sum
from django.utils import timezone

from accounts.models import Document, Lesson, StripePayment, User


class Command(BaseCommand):
    help = "Envoie un email de métriques hebdo (inscrits, générations, paiements)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            dest="to",
            help="Destinataire(s) email séparés par des virgules. Fallback: WEEKLY_METRICS_REPORT_TO",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Fenêtre en jours (défaut: 7)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche le rapport sans envoyer l'email",
        )

    def handle(self, *args, **options):
        recipients_raw = options.get("to") or os.getenv("WEEKLY_METRICS_REPORT_TO", "")
        recipients = [e.strip() for e in recipients_raw.split(",") if e.strip()]
        days = max(1, int(options.get("days", 7)))

        if not recipients and not options.get("dry_run"):
            raise CommandError(
                "Aucun destinataire trouvé. Passe --to ou configure WEEKLY_METRICS_REPORT_TO"
            )

        now = timezone.now()
        window_start = now - timedelta(days=days)

        # Utilisateurs
        new_users = User.objects.filter(created_at__gte=window_start).count()
        total_users = User.objects.count()

        # Générations (proxy usage): nouvelles leçons/documents sur la période
        weekly_generations = Lesson.objects.filter(created_at__gte=window_start).count()
        total_generations = Lesson.objects.count()

        weekly_documents = Document.objects.filter(created_at__gte=window_start).count()
        total_documents = Document.objects.count()

        active_users_week = (
            User.objects.filter(
                Q(lessons__created_at__gte=window_start) | Q(documents__created_at__gte=window_start)
            )
            .distinct()
            .count()
        )

        # Paiements Stripe
        payments_week = StripePayment.objects.filter(created_at__gte=window_start)
        successful_payments = payments_week.filter(status="succeeded")
        failed_or_other_payments = payments_week.exclude(status="succeeded")

        successful_count = successful_payments.count()
        failed_or_other_count = failed_or_other_payments.count()
        attempts_count = payments_week.count()

        weekly_revenue_cents = successful_payments.aggregate(total=Sum("amount"))["total"] or 0
        weekly_revenue_eur = Decimal(weekly_revenue_cents) / Decimal(100)

        conversion_rate = (successful_count / attempts_count * 100) if attempts_count > 0 else 0.0

        by_status = dict(
            payments_week.values("status").annotate(count=Count("id")).values_list("status", "count")
        )

        subject = f"[Revisia] Weekly metrics ({window_start.date()} → {now.date()})"

        text_body = (
            f"Résumé hebdo Revisia ({window_start.date()} → {now.date()})\n\n"
            f"👥 Inscrits\n"
            f"- Nouveaux inscrits: {new_users}\n"
            f"- Total inscrits: {total_users}\n\n"
            f"⚙️ Usage / Générations\n"
            f"- Générations (lessons) semaine: {weekly_generations}\n"
            f"- Générations (lessons) total: {total_generations}\n"
            f"- Documents semaine: {weekly_documents}\n"
            f"- Documents total: {total_documents}\n"
            f"- Utilisateurs actifs semaine: {active_users_week}\n\n"
            f"💳 Paiements\n"
            f"- Tentatives: {attempts_count}\n"
            f"- Succès: {successful_count}\n"
            f"- Échecs/autres statuts: {failed_or_other_count}\n"
            f"- Conversion tentative → succès: {conversion_rate:.1f}%\n"
            f"- CA semaine (paiements réussis): {weekly_revenue_eur:.2f} €\n"
            f"- Détail par statut: {by_status}\n"
        )

        html_body = f"""
        <h2>Résumé hebdo Revisia</h2>
        <p><strong>Période :</strong> {window_start.date()} → {now.date()}</p>

        <h3>👥 Inscrits</h3>
        <ul>
          <li>Nouveaux inscrits : <strong>{new_users}</strong></li>
          <li>Total inscrits : <strong>{total_users}</strong></li>
        </ul>

        <h3>⚙️ Usage / Générations</h3>
        <ul>
          <li>Générations (lessons) semaine : <strong>{weekly_generations}</strong></li>
          <li>Générations (lessons) total : <strong>{total_generations}</strong></li>
          <li>Documents semaine : <strong>{weekly_documents}</strong></li>
          <li>Documents total : <strong>{total_documents}</strong></li>
          <li>Utilisateurs actifs semaine : <strong>{active_users_week}</strong></li>
        </ul>

        <h3>💳 Paiements</h3>
        <ul>
          <li>Tentatives : <strong>{attempts_count}</strong></li>
          <li>Succès : <strong>{successful_count}</strong></li>
          <li>Échecs/autres statuts : <strong>{failed_or_other_count}</strong></li>
          <li>Conversion tentative → succès : <strong>{conversion_rate:.1f}%</strong></li>
          <li>CA semaine (paiements réussis) : <strong>{weekly_revenue_eur:.2f} €</strong></li>
          <li>Détail par statut : <code>{by_status}</code></li>
        </ul>
        """

        if options.get("dry_run"):
            self.stdout.write(self.style.SUCCESS("[DRY-RUN] Rapport généré:"))
            self.stdout.write(text_body)
            return

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=os.getenv("MAILGUN_FROM_EMAIL", "noreply@mail.revisia-app.fr"),
            to=recipients,
        )
        email.attach_alternative(html_body, "text/html")
        email.send()

        self.stdout.write(
            self.style.SUCCESS(
                f"Rapport hebdo envoyé à {', '.join(recipients)} | users+{new_users} | gen={weekly_generations} | pay_ok={successful_count}/{attempts_count}"
            )
        )
