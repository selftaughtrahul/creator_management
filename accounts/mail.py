from django.core.mail.backends.base import BaseEmailBackend


class DatabaseEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        from accounts.models import DevEmail

        sent = 0
        for message in email_messages:
            DevEmail.objects.create(
                to_email=", ".join(message.to),
                subject=message.subject,
                body=message.body or "",
            )
            sent += 1
        return sent
