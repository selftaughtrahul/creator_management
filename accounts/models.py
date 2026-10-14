import re

from django.conf import settings
from django.db import models

PERMISSION_CHOICES = [
    ("studio_dashboard", "Studio dashboard"),
    ("campaigns", "Campaigns"),
    ("discover", "Discover"),
    ("feedback", "Feedback"),
    ("jobs", "Jobs"),
    ("classifications", "Classifications"),
    ("templates", "Templates"),
    ("outreach", "Outreach"),
    ("creators", "Creator profiles"),
    ("users", "People management"),
]

DEFAULT_EMPLOYEE_PERMISSIONS = [
    "studio_dashboard",
    "campaigns",
    "discover",
    "feedback",
    "jobs",
    "classifications",
    "templates",
    "outreach",
    "creators",
]


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
        ADMIN = "admin", "Admin"
        EMPLOYEE = "employee", "Employee"
        CREATOR = "creator", "Creator"
        TEAM = "team", "Team (legacy)"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="studio",
    )
    class Theme(models.TextChoices):
        TIDE = "tide", "Tide"
        IVORY = "ivory", "Ivory"
        JADE = "jade", "Jade"
        DUSK = "dusk", "Dusk"
        ROUGE = "rouge", "Rouge"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    permissions = models.JSONField(default=list, blank=True)
    theme = models.CharField(max_length=20, choices=Theme.choices, default=Theme.TIDE)

    def __str__(self):
        return f"{self.user} · {self.get_role_display()}"

    @property
    def is_admin(self):
        return self.role in {self.Role.ADMIN, self.Role.TEAM}

    @property
    def is_employee(self):
        return self.role == self.Role.EMPLOYEE

    @property
    def is_team(self):
        return self.is_admin or self.is_employee

    @property
    def is_creator(self):
        return self.role == self.Role.CREATOR


class Notification(models.Model):
    class Kind(models.TextChoices):
        INVITATION = "invitation", "Invitation"
        OFFER = "offer", "Offer"
        STATUS = "status", "Status"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.STATUS)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} · {self.title}"

    @property
    def is_unread(self):
        return self.read_at is None


def studio_profile(user):
    if not user or not user.is_authenticated:
        return None
    role = StudioProfile.Role.ADMIN if getattr(user, "is_superuser", False) else StudioProfile.Role.EMPLOYEE
    defaults = {"role": role}
    if role == StudioProfile.Role.EMPLOYEE:
        defaults["permissions"] = list(DEFAULT_EMPLOYEE_PERMISSIONS)
    profile, _ = StudioProfile.objects.get_or_create(user=user, defaults=defaults)
    return profile
