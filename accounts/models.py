import re

from django.conf import settings
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


class StudioProfile(models.Model):
    class Role(models.TextChoices):
        TEAM = "team", "Team"
        CREATOR = "creator", "Creator"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="studio",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.TEAM)

    def __str__(self):
        return f"{self.user} · {self.get_role_display()}"

    @property
    def is_team(self):
        return self.role == self.Role.TEAM

    @property
    def is_creator(self):
        return self.role == self.Role.CREATOR


def studio_profile(user):
    if not user or not user.is_authenticated:
        return None
    profile, _ = StudioProfile.objects.get_or_create(
        user=user,
        defaults={"role": StudioProfile.Role.TEAM},
    )
    return profile
