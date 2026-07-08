from django.apps import AppConfig
from django.urls import path, include


class TomCFHTConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tom_cfht'  # python path to the application, like 'django.contrib.admin'

    def include_url_paths(self):
        """
        Integration point for adding URL patterns to the Tom Common URL configuration.
        This method should return a list of URL patterns to be included in the main URL configuration.
        """
        urlpatterns = [
            path(f'{self.label}/', include(f'{self.name}.urls', namespace=f'{self.label}'))
        ]
        return urlpatterns

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


