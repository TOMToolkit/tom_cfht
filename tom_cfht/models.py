import logging

from django.contrib.auth.models import User
from django.db import models

from tom_common.encryption import EncryptedModelField
from tom_targets.base_models import BaseTarget
from tom_targets.models import TargetList


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CFHTProfile(models.Model):
    """User Profile for the TOMToolkit CFHT Facility
    """
    # connect this Profile to it's User
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    cfht_access_token = EncryptedModelField(null=True, blank=True)


class KealahouProgramAssociation(models.Model):
    """Associates a CFHT observing program with the TOM Target Grouping (``TargetList``)
    that mirrors it.

    The association is user-controlled: until one exists for a program, target-sync
    features for that program present a select-or-create Target Grouping form (the
    "gate"). CASCADE deletion means removing the Target Grouping dissolves the
    association and the gate simply reappears. One Target Grouping may serve several
    programs; each program has exactly one Target Grouping.
    """
    program_token = models.CharField(
        max_length=32, unique=True,
        help_text="Kealahou's unique identifier for the program (runid-shaped, e.g. '25BE25').")
    target_list = models.ForeignKey(
        TargetList, on_delete=models.CASCADE, related_name='kealahou_program_associations',
        help_text='The Target Grouping whose membership mirrors this program in the TOM.')

    def __str__(self) -> str:
        return f'{self.program_token} <-> {self.target_list.name}'


class KealahouTargetLink(models.Model):
    """Links a TOM ``Target`` to its Kealahou counterpart within one CFHT program.

    Kealahou identifies entities by a field it calls ``token`` (an immutable unique
    identifier -- unrelated to the API *access* token). A TOM target may be shared with
    several CFHT programs, and each program keeps its own copy with its own
    ``kealahou_target_token``, so the link is per-(target, program).
    """
    target = models.ForeignKey(BaseTarget, on_delete=models.CASCADE, related_name='kealahou_links')
    program_token = models.CharField(
        max_length=32,
        help_text="Kealahou's unique identifier for the program (runid-shaped, e.g. '25BE25').")
    kealahou_target_token = models.CharField(
        max_length=64, unique=True,
        help_text="Kealahou's unique identifier for the target within its program, "
                  "e.g. '25BE25-1758314224958'. Client-generated at upload.")
    version = models.IntegerField(
        null=True, blank=True,
        help_text="Kealahou's optimistic-lock version, as returned by the API after the last sync. "
                  'Sent back as lock_version on updates to prevent overwriting concurrent edits.')
    synced_at = models.DateTimeField(
        auto_now=True, help_text='When this link was last created or refreshed by a sync operation.')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['target', 'program_token'], name='unique_target_per_program'),
        ]

    def __str__(self) -> str:
        return f'{self.target} <-> {self.kealahou_target_token}'
