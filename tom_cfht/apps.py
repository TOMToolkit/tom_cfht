from django.apps import AppConfig
from django.urls import path, include


class TomCFHTConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_cfht'  # python path to the application, like 'django.contrib.admin'
    url_prefix = 'cfht'  # URL path prefix for this app's pages: HOST:PORT/cfht/... (see include_url_paths())

    def include_url_paths(self):
        """
        Integration point for adding URL patterns to the Tom Common URL configuration.
        This method should return a list of URL patterns to be included in the main URL configuration.

        Note: url_prefix only affects the path; the URL namespace remains self.label ('tom_cfht'),
        so reverses like 'tom_cfht:facility-index' are unaffected.
        """
        urlpatterns = [
            path(f'{self.url_prefix}/', include(f'{self.name}.urls', namespace=f'{self.label}'))
        ]
        return urlpatterns

    def observation_facilities(self):
        """
        Integration point for including this app's observation facilities in the TOM.

        This method should return a list of dictionaries, each with a `class` key giving the dot separated
        path to a Facility class (consumed by ``tom_observations.facility.get_service_classes()``, so the
        facility is available without being listed in ``settings.TOM_FACILITY_CLASSES``), and an optional
        `url` key giving the namespaced URL name of the facility's landing page (used by the navbar
        "Facilities" menu). Omit `url` for a facility with no landing page: it is still registered, but
        gets no navbar menu item.
        """
        return [{'class': f'{self.name}.cfht.CFHTFacility',
                 'url': f'{self.label}:facility-index'}]

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
