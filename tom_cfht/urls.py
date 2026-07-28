from django.urls import path

from .views import (
    CFHTFacilityIndexView,
    ProfileUpdateView,
    add_target_to_target_grouping,
    associate_target_grouping,
    kealahou_target_status,
    observing_programs,
    program_panel,
    sync_selected_targets,
    targets_section,
    upload_single_target,
)


app_name = 'tom_cfht'

urlpatterns = [
    # facility landing page, linked from the navbar "Facilities" menu
    # (see TomCFHTConfig.observation_facilities())
    path('', CFHTFacilityIndexView.as_view(), name='facility-index'),

    # htmx partials for the facility page (program tabs and their sections)
    path('observing-programs/', observing_programs, name='observing-programs'),
    path('programs/<str:program_token>/panel/', program_panel, name='program-panel'),
    path('programs/<str:program_token>/targets-section/', targets_section, name='targets-section'),
    path('programs/<str:program_token>/associate-target-grouping/', associate_target_grouping,
         name='associate-target-grouping'),
    path('programs/<str:program_token>/sync-selected-targets/', sync_selected_targets,
         name='sync-selected-targets'),

    # htmx partials for the CFHT observation form (Kealahou state chain per program)
    path('kealahou-target-status/', kealahou_target_status, name='kealahou-target-status'),
    path('upload-target/', upload_single_target, name='upload-single-target'),
    path('add-target-to-target-grouping/', add_target_to_target_grouping,
         name='add-target-to-target-grouping'),

    path('users/<int:pk>/update/', ProfileUpdateView.as_view(), name='cfht-profile-update'),
]
