import logging

from django.contrib.auth.models import User
from django.db import models

from tom_common.encryption import EncryptedModelField


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CFHTProfile(models.Model):
    """User Profile for the TOMToolkit CFHT Facility
    """
    # connect this Profile to it's User
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    cfht_access_token = EncryptedModelField(null=True, blank=True)

