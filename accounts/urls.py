from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("", views.home, name="home"),
    path("privacy/", views.privacy, name="privacy"),
    path("data-deletion/", views.data_deletion, name="data_deletion"),
    path("employees/join/", views.employee_join, name="employee_join"),
    path("profile/", views.profile, name="profile"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read-all/", views.mark_all_notifications_read, name="notifications_read_all"),
    path("notifications/<int:pk>/read/", views.mark_notification_read, name="notification_read"),
    path("studio/people/", views.people, name="people"),
    path("studio/people/<int:pk>/", views.person_detail, name="person"),
]
