from django import template
from django.forms.models import model_to_dict

from tom_cfht.models import CFHTProfile

register = template.Library()


@register.inclusion_tag('tom_cfht/partials/profile_cfht.html')
def cfht_profile_data(user) -> dict:
    """
    Returns the app specific user information as a dictionary to be used in the context of the above partial.
    """
    # get_or_create so a user who has never saved CFHT info gets an empty profile on first
    # view, and every path returns the same fully-populated context. (A missing-profile
    # branch that returned only {'user': ...} left the partial's Edit link reversing with
    # pk='', which 500'd the profile page on its first visit.)
    cfht_profile, _ = CFHTProfile.objects.get_or_create(user=user)

    # cfht_access_token is rendered separately via tom_common's revealable_password_input
    # partial, so exclude it from the auto-iteration loop. model_to_dict goes
    # through EncryptedModelField.value_from_object, which returns the REDACTED
    # placeholder string for security; the partial needs the actual plaintext,
    # which only direct attribute access provides.
    exclude_fields = ['user', 'id', 'cfht_access_token']
    return {
        'user': user,
        'cfht_profile': cfht_profile,
        'cfht_profile_data': model_to_dict(cfht_profile, exclude=exclude_fields),
        'cfht_access_token': cfht_profile.cfht_access_token,  # direct access → plaintext
    }
