import re

from django.db import models


class DevEmail(models.Model):
    """Local mailbox used while DEBUG is on and SMTP is not configured."""

    to_email = models.CharField(max_length=500)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} → {self.to_email}"

    @property
    def links(self):
        found = re.findall(r"https?://[^\s<>\"]+", self.body)
        return [link.rstrip(".,)") for link in found]
