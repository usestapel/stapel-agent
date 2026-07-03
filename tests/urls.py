from django.urls import include, path

urlpatterns = [
    path("agent/", include("stapel_agent.urls")),
]
