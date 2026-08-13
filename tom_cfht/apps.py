from django.apps import AppConfig
from django.urls import path, include


class TomCFHTConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_cfht'  # python path to the application, like 'django.contrib.admin'
    # route_prefix is a TOMToolkit convention, not a Django AppConfig attribute:
    # it is read only by include_url_paths() below.
    route_prefix = 'cfht'  # prefixes every route in urls.py: pages live at HOST:PORT/cfht/...

    def include_url_paths(self):
        """
        Integration point for adding URL patterns to the Tom Common URL configuration.
        This method should return a list of URL patterns to be included in the main URL configuration.

        Note: route_prefix only affects the URL path. The namespace in reverses like
        'tom_cfht:facility-detail' comes from app_name in urls.py (derived from self.name).
        """
        urlpatterns = [
            path(f'{self.route_prefix}/', include(f'{self.name}.urls'))
        ]
        return urlpatterns

    def observation_facilities(self):
        """
        Integration point for including this app's observation facilities in the TOM.

        Returns ``{'class': <dot separated path to a Facility class>}`` dicts, consumed by
        ``tom_observations.facility.get_service_classes()``.
        """
        return [{'class': f'{self.name}.cfht.CFHTFacility'}]

    def profile_details(self):
        """
        Integration point for adding items to the user profile page.

        This method should return a list of dictionaries that include a `partial` key pointing to the path of the html
        profile partial. The `context` key should point to the dot separated string path to the templatetag that will
        return a dictionary containing new context for the accompanying partial.
        Typically, this partial will be a bootstrap card displaying some app specific user data.
        """
        return [{'partial': f'{self.name}/partials/profile_cfht.html',
                 'context': f'{self.name}.templatetags.cfht_extras.cfht_profile_data'}]
