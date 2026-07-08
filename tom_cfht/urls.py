from django.urls import path

from .views import ProfileUpdateView


app_name = 'tom_cfht'

urlpatterns = [
    path('users/<int:pk>/update/', ProfileUpdateView.as_view(), name='cfht-profile-update'),
]
