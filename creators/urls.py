from django.urls import path

from creators import views

app_name = "creators"

urlpatterns = [
    path("creators/join/", views.join, name="join"),
    path("creators/me/", views.me, name="me"),
    path("creators/me/documents/", views.documents, name="documents"),
    path("creators/campaigns/", views.my_campaigns, name="my_campaigns"),
    path("creators/campaigns/<int:pk>/", views.my_campaign, name="my_campaign"),
    path("creators/invitations/", views.invitations, name="invitations"),
    path("creators/invitations/<int:pk>/respond/", views.respond_invitation, name="respond_invitation"),
    path("creators/offers/<int:pk>/respond/", views.respond_offer, name="respond_offer"),
    path("studio/creators/", views.creator_directory, name="directory"),
    path("studio/creators/chat/", views.discover_chat, name="discover_chat"),
    path("studio/creators/<int:pk>/", views.creator_detail, name="detail"),
    path("studio/creators/<int:pk>/classify/", views.classify_now, name="classify"),
    path("studio/creators/<int:pk>/review/", views.review_classification, name="review"),
    path("studio/creators/<int:pk>/enrich/", views.request_enrichment, name="enrich"),
    path("studio/classifications/", views.classification_queue, name="queue"),
    path("studio/jobs/", views.jobs, name="jobs"),
    path("studio/jobs/run/", views.run_jobs, name="run_jobs"),
    path("studio/jobs/<int:pk>/retry/", views.retry_enrichment, name="retry_job"),
]
