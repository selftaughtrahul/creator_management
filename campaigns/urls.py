from django.urls import path

from campaigns import views

app_name = "campaigns"

urlpatterns = [
    path("studio/", views.dashboard, name="dashboard"),
    path("studio/sample/load/", views.load_sample_data, name="load_sample"),
    path("studio/sample/delete/", views.delete_sample_data, name="delete_sample"),
    path("studio/campaigns/", views.campaign_list, name="list"),
    path("studio/campaigns/new/", views.campaign_create, name="create"),
    path("studio/campaigns/<int:pk>/", views.campaign_detail, name="detail"),
    path("studio/campaigns/<int:pk>/edit/", views.campaign_edit, name="edit"),
    path("studio/campaigns/<int:pk>/export/", views.export_shortlist, name="export"),
    path("studio/campaigns/<int:pk>/discover/", views.discover, name="campaign_discover"),
    path("studio/discover/", views.discover, name="discover"),
    path("studio/discover/enrich/", views.request_search_enrichment, name="search_enrich"),
    path("studio/campaigns/<int:pk>/shortlist/<int:creator_id>/", views.shortlist_creator, name="shortlist"),
    path("studio/campaigns/<int:pk>/assign/<int:creator_id>/", views.assign_creator, name="assign"),
    path("studio/campaigns/<int:pk>/cost/<int:creator_id>/", views.update_cost, name="cost"),
    path("studio/campaigns/<int:pk>/feedback/<int:creator_id>/", views.submit_feedback, name="feedback_submit"),
    path("studio/feedback/", views.feedback_list, name="feedback"),
    path("studio/templates/", views.template_list, name="templates"),
    path("studio/templates/<int:pk>/edit/", views.template_edit, name="template_edit"),
    path("studio/campaigns/<int:pk>/outreach/", views.prepare_outreach, name="outreach"),
    path("studio/campaigns/<int:pk>/outreach/confirm/", views.confirm_outreach, name="confirm_outreach"),
    path("studio/invitations/<int:pk>/outcome/", views.invitation_outcome, name="invitation_outcome"),
    path("studio/outreach/", views.outreach_history, name="outreach_history"),
    path("studio/campaigns/<int:pk>/offers/<int:creator_id>/", views.create_offer, name="offer"),
    path("studio/offers/<int:pk>/decide/", views.decide_offer, name="decide_offer"),
]
