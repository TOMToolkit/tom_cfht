from __future__ import annotations

from crispy_forms.layout import Layout
from django import forms

from tom_observations.facility import BaseRoboticObservationFacility, BaseRoboticObservationForm


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
    observation_types: list[tuple[str, str]] = [
        ('OBSERVATION', 'Custom Observation')
    ]

    observation_forms: dict[str, type[BaseRoboticObservationForm]] = {
        'OBSERVATION': CFHTFacilityForm,
    }

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
