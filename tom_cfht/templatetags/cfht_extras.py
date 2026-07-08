from django import template
from django.forms.models import model_to_dict

from tom_cfht.models import CFHTProfile

register = template.Library()


@register.inclusion_tag('tom_cfht/partials/profile_cfht.html')
def cfht_profile_data(user):
    """
    Returns the app specific user information as a dictionary to be used in the context of the above partial.
    """

    # cfht_access_token is rendered separately via tom_common's revealable_password_input
    # partial, so exclude it from the auto-iteration loop. model_to_dict goes
    # through EncryptedModelField.value_from_object, which returns the REDACTED
    # placeholder string for security; the partial needs the actual plaintext,
    # which only direct attribute access provides.
    exclude_fields = ['user', 'id', 'cfht_access_token']
    try:
        cfht_profile_dict = model_to_dict(user.cfhtprofile, exclude=exclude_fields)
        profile_data = {
            'user': user,
            'cfht_profile': user.cfhtprofile,
            'cfht_profile_data': cfht_profile_dict,
            'cfht_access_token': user.cfhtprofile.cfht_access_token,  # direct access → plaintext
        }
        return profile_data
    except CFHTProfile.DoesNotExist:
        CFHTProfile.objects.create(user=user)
        profile_data = {'user': user}
        return profile_data
