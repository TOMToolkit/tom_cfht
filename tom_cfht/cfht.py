from __future__ import annotations

from crispy_forms.layout import Layout
from django import forms
from django.core.exceptions import ImproperlyConfigured

from aeonlib.cfht.facility import CFHTFacility as AeonCFHTFacility
from aeonlib.cfht.models import ProgramInfo
from aeonlib.conf import Settings

from tom_observations.facility import BaseRoboticObservationFacility, BaseRoboticObservationForm, CredentialStatus

from tom_cfht.apps import TomCFHTConfig
from tom_cfht.models import CFHTProfile


class CFHTFacilityForm(BaseRoboticObservationForm):
    exposure_time = forms.IntegerField()
    exposure_count = forms.IntegerField()

    def layout(self):
        return Layout(
            'exposure_time',
            'exposure_count'
        )


class CFHTFacility(BaseRoboticObservationFacility):
    name = 'CFHT'
    # Detail page linked from the navbar "Facilities" menu. The AppConfig's name is the
    # single source of truth for the namespace; guarded by test_detail_url_name_resolves.
    detail_url_name = f'{TomCFHTConfig.name}:facility-detail'  # 'tom_cfht:facility-detail'
    # Facility-specific observation form page: adds the Kealahou target status/upload panel.
    # ObservationCreateView.get_template_names() tries this template first.
    template_name = 'tom_cfht/observation_form.html'
    observation_types: list[tuple[str, str]] = [
        ('OBSERVATION', 'Custom Observation')
    ]

    observation_forms: dict[str, type[BaseRoboticObservationForm]] = {
        'OBSERVATION': CFHTFacilityForm,
    }

    def get_access_token(self) -> str:
        """Return the Kealahou API access token for the current user.

        The per-user token from the ``CFHTProfile`` (set via ``set_user()``) takes
        precedence; falls back to the TOM-wide default in
        ``settings.FACILITIES['CFHT']['CFHT_ACCESS_TOKEN']``. Tracks the outcome in
        ``self.credential_status``.

        Note: this is the Bearer credential for API authentication -- unrelated to
        Kealahou's entity identifiers, which are unfortunately also called "tokens".

        Raises:
            ImproperlyConfigured: if neither source provides a token.
        """
        if self.user is not None and self.user.is_authenticated:
            try:
                profile_access_token = str(self.user.cfhtprofile.cfht_access_token or '').strip()
            except CFHTProfile.DoesNotExist:
                profile_access_token = ''
            if profile_access_token:
                self.credential_status = CredentialStatus.USING_USER_CREDS
                return profile_access_token

        # fall back to the TOM-wide default from settings.FACILITIES
        setting_credentials = self._get_setting_credentials('CFHT', ['CFHT_ACCESS_TOKEN'])
        default_access_token = str(setting_credentials['CFHT_ACCESS_TOKEN'] or '').strip()
        if self._is_credential_empty(default_access_token):
            self.credential_status = CredentialStatus.PROFILE_EMPTY
            raise ImproperlyConfigured(
                'No CFHT access token found. Generate one on the Kealahou "Manage Tokens" page and '
                "save it in your CFHT user profile (or in settings.FACILITIES['CFHT'])."
            )
        self.credential_status = CredentialStatus.USING_DEFAULTS
        return default_access_token

    def get_aeon_facility(self) -> AeonCFHTFacility:
        """Return an aeonlib Kealahou client authenticated as the current user."""
        cfht_settings = Settings()
        cfht_settings.cfht_access_token = self.get_access_token()
        return AeonCFHTFacility(cfht_settings)

    def get_observing_programs(self) -> list[ProgramInfo]:
        """Return the current user's CFHT observing programs from the Kealahou API."""
        return self.get_aeon_facility().programs()

    def data_products(self):
        pass

    def get_form(self, observation_type: str | None) -> type[BaseRoboticObservationForm]:
        """Return the observation form class for ``observation_type``.
        """
        if observation_type is None:
            return CFHTFacilityForm
        return self.observation_forms.get(observation_type, CFHTFacilityForm)

    def get_observation_status(self):
        pass

    def get_observation_url(self):
        pass

    def get_observing_sites(self) -> dict[str, dict]:
        """Return the facility's observing site(s) for the visibility and airmass planner.

        From CFHT Observatory Manual: The telescope itself is of 3.58 meters aperture.
        It is located on Mauna Kea at an altitude (declination axis) of 4204 m (13,793 feet),
        at latitude +19o 49' 41.86" and longitude 155o 28' 18.00".
        """
        cfht_location_params = {
            'Mauna Kea': {
                'sitecode': 'cfht',
                'latitude': 19.8283,
                'longitude': -155.4716,
                'elevation': 4204,
            }
        }
        return cfht_location_params

    def get_terminal_observing_states(self):
        pass

    def submit_observation(self):
        pass

    def validate_observation(self):
        pass
