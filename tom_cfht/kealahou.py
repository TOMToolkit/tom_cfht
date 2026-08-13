"""Service layer between the aeonlib Kealahou client and the tom_cfht views.

Terminology: Kealahou identifies entities by a field it calls ``token`` -- an immutable
unique identifier, unrelated to the API *access* token. This module always qualifies the
word: ``program_token`` (e.g. '25BE25') and ``kealahou_target_token``
(e.g. '25BE25-1758314224958', client-generated).

Each CFHT program is mirrored in the TOM by a user-chosen Target Grouping (``TargetList``);
the mapping lives in ``KealahouProgramAssociation`` and callers resolve it before invoking
the sync functions here.

The functions here are deliberately free of view/HTTP concerns so they can be unit tested
with a mocked aeonlib facade.
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field

import httpx
import pydantic
from django.db import transaction

from aeonlib.cfht.facility import CFHTFacility as AeonCFHTFacility
from aeonlib.cfht.models import (
    DoubleValue,
    FixedTargetProperMotion,
    Instrument,
    SkyCoordinate,
    TargetData,
    TargetDataFixedTarget,
    TargetDataMagnitude,
)

from tom_targets.models import Target, TargetList

from tom_cfht.models import KealahouTargetLink

logger = logging.getLogger(__name__)

# Kealahou requires one specific magnitude band per instrument (workshop README).
REQUIRED_MAGNITUDE_BAND_BY_INSTRUMENT: dict[Instrument, str] = {
    Instrument.spirou: 'h',
    Instrument.espadons: 'v',
    Instrument.megacam: 'ab',
}
# The spectrographs additionally require an effective temperature; SPIRou also wants
# an estimated radial velocity. (Not used for MegaCam.)
INSTRUMENTS_REQUIRING_TEMPERATURE = frozenset({Instrument.spirou, Instrument.espadons})
INSTRUMENTS_REQUIRING_RADIAL_VELOCITY = frozenset({Instrument.spirou})

# Kealahou target-name rules: the name becomes the FITS OBJECT keyword.
TARGET_NAME_MAX_LENGTH = 39
_TARGET_NAME_ALLOWED = re.compile(r'^[A-Za-z0-9 !@#$%^&*()_\-+.,?/\[\]<>]+$')

# Tolerance for the sync-state discrepancy display. The Kealahou web UI round-trips RA
# through sexagesimal at 0.1-second-of-time precision (measured on staging: an untouched
# RA came back 0.60 arcsec different; the rounding quantum is 1.5 arcsec, so errors up to
# 0.75 arcsec are pure representation noise). 1.0 arcsec absorbs that noise while still
# flagging genuinely different positions. Proper motions compare at 0.01 mas/yr.
COORDINATE_TOLERANCE_ARCSEC = 1.0
_COORDINATE_TOLERANCE_DEGREES = COORDINATE_TOLERANCE_ARCSEC / 3600.0
PROPER_MOTION_TOLERANCE_MAS = 0.01

# Kealahou requires every target to reference a pointing offset. '00AZ00-PO+<INSTRUMENT>+1'
# is the system-owned "no offset" default (offset {}, user_token SYSTEM), observed on the
# staging programs and hardcoded the same way in the Kealahou workshop examples.
DEFAULT_POINTING_OFFSET_TOKEN_TEMPLATE = '00AZ00-PO+{instrument}+1'


@dataclass
class FieldDiscrepancy:
    """One field whose value differs between the TOM target and its Kealahou counterpart."""
    field_name: str
    tom_value: object
    kealahou_value: object


@dataclass
class LinkedTargetPair:
    """A TOM target and its linked Kealahou counterpart, with any field discrepancies."""
    link: KealahouTargetLink
    target: Target
    target_data: TargetData
    discrepancies: list[FieldDiscrepancy] = field(default_factory=list)
    # Kealahou-side values of the instrument's required fields, for display
    required_field_values: dict = field(default_factory=dict)


@dataclass
class ImportCandidate:
    """A Kealahou target not yet linked to the TOM.

    If a TOM target with the same name already exists, importing will link it rather
    than create a duplicate; ``existing_target`` carries that target for UI display.
    """
    target_data: TargetData
    existing_target: Target | None = None
    # Kealahou-side values of the instrument's required fields, for display
    required_field_values: dict = field(default_factory=dict)


@dataclass
class SyncState:
    """The four sync buckets displayed by the target-sync panel."""
    tom_only: list[Target] = field(default_factory=list)
    kealahou_only: list[ImportCandidate] = field(default_factory=list)
    linked_in_sync: list[LinkedTargetPair] = field(default_factory=list)
    linked_discrepant: list[LinkedTargetPair] = field(default_factory=list)


@dataclass
class UploadRequest:
    """One TOM target to upload to Kealahou, with the instrument's required extra fields."""
    target: Target
    magnitude: float | None = None
    temperature_effective: float | None = None
    radial_velocity_kmps: float | None = None


@dataclass
class UploadResult:
    """Outcome of one target upload attempt."""
    target: Target
    success: bool
    message: str = ''


@dataclass
class ResultMessage:
    """A user-facing outcome message with a severity, for the sync panels.

    Failures render in a bootstrap danger alert; successes/notices in an info alert.
    """
    text: str
    success: bool = True


def kealahou_api_error_messages(error: httpx.HTTPStatusError) -> list[str]:
    """Extract Kealahou's human-readable error messages from an HTTP error response.

    Kealahou 4xx responses carry ``{"error": {"messages": [...]}}`` explaining exactly
    what was rejected (e.g. a value out of range); surfacing them beats httpx's generic
    status-code message.
    """
    try:
        payload = error.response.json()
    except ValueError:  # not JSON (e.g. an HTML error page from a proxy)
        return []
    error_info = payload.get('error') or {}
    return error_info.get('messages') or []


def required_field_values(target_data: TargetData, instrument: Instrument | None) -> dict:
    """Return the Kealahou-side values of the instrument's required target fields.

    Used for display in the sync tables (the TOM stores none of these fields, so the
    Kealahou copy is the only source). Values may be None when Kealahou has no value.
    """
    magnitude_band = REQUIRED_MAGNITUDE_BAND_BY_INSTRUMENT.get(instrument)
    magnitude = None
    if magnitude_band is not None and target_data.magnitude is not None:
        band_value = getattr(target_data.magnitude, magnitude_band, None)
        magnitude = band_value.value if band_value is not None else None
    radial_velocity_kmps = None
    if target_data.fixed_target is not None and target_data.fixed_target.estimated_radial_velocity_kmps is not None:
        radial_velocity_kmps = target_data.fixed_target.estimated_radial_velocity_kmps.value
    return {
        'magnitude': magnitude,
        'temperature_effective': target_data.temperature_effective,
        'radial_velocity_kmps': radial_velocity_kmps,
    }


def default_target_list_name(program_token: str, instrument: Instrument | None) -> str:
    """Return the suggested name for a new Target Grouping mirroring a CFHT program.

    Includes the instrument so users see more than a bare runid, e.g. 'CFHT-MEGACAM-25BE25'.
    """
    instrument_name = instrument.value if instrument is not None else 'UNKNOWN'
    return f'CFHT-{instrument_name}-{program_token}'


def validate_kealahou_target_name(name: str) -> None:
    """Raise ValueError unless ``name`` satisfies Kealahou's target-name rules.

    The name becomes the FITS OBJECT keyword: at most 39 characters; letters, digits,
    spaces, or ``!@#$%^&*()_-+.,?/[]<>``.
    """
    if len(name) > TARGET_NAME_MAX_LENGTH:
        raise ValueError(f'Target name {name!r} exceeds Kealahou limit of {TARGET_NAME_MAX_LENGTH} characters.')
    if not _TARGET_NAME_ALLOWED.match(name):
        raise ValueError(f'Target name {name!r} contains characters Kealahou does not allow '
                         '(allowed: letters, digits, spaces, and !@#$%^&*()_-+.,?/[]<>).')


def generate_kealahou_target_token(program_token: str) -> str:
    """Return a new client-generated kealahou_target_token: ``<program_token>-<10 digits>``."""
    return f'{program_token}-{random.randint(1_000_000_000, 9_999_999_999)}'


def target_data_from_target(target: Target, program_token: str, instrument: Instrument,
                            magnitude: float | None = None,
                            temperature_effective: float | None = None,
                            radial_velocity_kmps: float | None = None,
                            kealahou_target_token: str | None = None,
                            version: int | None = None) -> TargetData:
    """Build an aeonlib ``TargetData`` from a (sidereal) TOM target for upload to Kealahou.

    Args:
        target: the TOM target (must be SIDEREAL; moving targets are not yet supported).
        program_token: Kealahou program identifier the target is being uploaded to.
        instrument: the program's instrument; selects the required magnitude band.
        magnitude: value for the instrument's required band (H/V/AB).
        temperature_effective: Kelvin; required by Kealahou for SPIRou/ESPaDOnS.
        radial_velocity_kmps: km/s; used for SPIRou.
        kealahou_target_token: reuse an existing identifier (updates); generated if None.
        version: Kealahou lock version when updating an existing Kealahou target; None creates.

    Returns:
        A populated ``TargetData`` ready for ``create_or_update_target()``.

    Raises:
        ValueError: if the target is not sidereal, or its name violates Kealahou's rules,
            or a field required by ``instrument`` is missing.
    """
    if target.type != Target.SIDEREAL:
        raise ValueError(f'Target {target.name!r} is not sidereal; only sidereal targets can be uploaded (for now).')
    validate_kealahou_target_name(target.name)

    magnitude_band = REQUIRED_MAGNITUDE_BAND_BY_INSTRUMENT.get(instrument)
    if magnitude_band is not None and magnitude is None:
        raise ValueError(f'{instrument.value} requires a {magnitude_band.upper()}-band magnitude.')
    if instrument in INSTRUMENTS_REQUIRING_TEMPERATURE and temperature_effective is None:
        raise ValueError(f'{instrument.value} requires an effective temperature (Kelvin).')
    if instrument in INSTRUMENTS_REQUIRING_RADIAL_VELOCITY and radial_velocity_kmps is None:
        raise ValueError(f'{instrument.value} requires an estimated radial velocity (km/s).')

    proper_motion = None
    if target.pm_ra is not None or target.pm_dec is not None:
        # TOM and Kealahou both use milliarcseconds/year
        proper_motion = FixedTargetProperMotion(ra_mas=target.pm_ra, dec_mas=target.pm_dec)

    target_magnitude = None
    if magnitude_band is not None:
        target_magnitude = TargetDataMagnitude()
        setattr(target_magnitude, magnitude_band, DoubleValue(value=magnitude))

    return TargetData(
        token=kealahou_target_token or generate_kealahou_target_token(program_token),
        name=target.name,
        version=version,
        fixed_target=TargetDataFixedTarget(
            coordinate=SkyCoordinate(ra=target.ra, dec=target.dec),
            proper_motion=proper_motion,
            estimated_radial_velocity_kmps=(
                DoubleValue(value=radial_velocity_kmps) if radial_velocity_kmps is not None else None),
        ),
        magnitude=target_magnitude,
        temperature_effective=temperature_effective,
        standard_star=False,
        pointing_offset_token=DEFAULT_POINTING_OFFSET_TOKEN_TEMPLATE.format(instrument=instrument.value),
    )


def target_from_target_data(target_data: TargetData) -> Target:
    """Build an (unsaved) sidereal TOM ``Target`` from a Kealahou fixed target for import.

    Raises:
        ValueError: for moving targets (not yet supported) or targets without coordinates.
    """
    if target_data.fixed_target is None or target_data.fixed_target.coordinate is None:
        raise ValueError(f'Kealahou target {target_data.name!r} has no fixed coordinate; '
                         'moving targets are not yet supported.')
    coordinate = target_data.fixed_target.coordinate
    proper_motion = target_data.fixed_target.proper_motion
    return Target(
        name=target_data.name,
        type=Target.SIDEREAL,
        ra=coordinate.ra,
        dec=coordinate.dec,
        pm_ra=proper_motion.ra_mas if proper_motion else None,
        pm_dec=proper_motion.dec_mas if proper_motion else None,
    )


def _floats_differ(tom_value: float | None, kealahou_value: float | None, tolerance: float) -> bool:
    if tom_value is None and kealahou_value is None:
        return False
    if tom_value is None or kealahou_value is None:
        return True
    return abs(tom_value - kealahou_value) > tolerance


def compare_target_fields(target: Target, target_data: TargetData) -> list[FieldDiscrepancy]:
    """Compare the fields shared by a TOM target and its Kealahou counterpart.

    Compared fields: name, ra, dec, pm_ra, pm_dec (with float tolerances). Fields that
    exist on only one side (magnitudes, Teff, ...) are not compared.
    """
    discrepancies = []
    if target.name != target_data.name:
        discrepancies.append(FieldDiscrepancy('name', target.name, target_data.name))

    coordinate = target_data.fixed_target.coordinate if target_data.fixed_target else None
    kealahou_ra = coordinate.ra if coordinate else None
    kealahou_dec = coordinate.dec if coordinate else None
    if _floats_differ(target.ra, kealahou_ra, _COORDINATE_TOLERANCE_DEGREES):
        discrepancies.append(FieldDiscrepancy('ra', target.ra, kealahou_ra))
    if _floats_differ(target.dec, kealahou_dec, _COORDINATE_TOLERANCE_DEGREES):
        discrepancies.append(FieldDiscrepancy('dec', target.dec, kealahou_dec))

    proper_motion = target_data.fixed_target.proper_motion if target_data.fixed_target else None
    kealahou_pm_ra = proper_motion.ra_mas if proper_motion else None
    kealahou_pm_dec = proper_motion.dec_mas if proper_motion else None
    if _floats_differ(target.pm_ra, kealahou_pm_ra, PROPER_MOTION_TOLERANCE_MAS):
        discrepancies.append(FieldDiscrepancy('pm_ra', target.pm_ra, kealahou_pm_ra))
    if _floats_differ(target.pm_dec, kealahou_pm_dec, PROPER_MOTION_TOLERANCE_MAS):
        discrepancies.append(FieldDiscrepancy('pm_dec', target.pm_dec, kealahou_pm_dec))

    return discrepancies


def compute_sync_state(program_token: str, kealahou_targets: list[TargetData],
                       target_list: TargetList, instrument: Instrument | None = None) -> SyncState:
    """Partition targets into the four sync buckets for one program.

    Args:
        program_token: the program being synced.
        kealahou_targets: the program's targets as fetched from the Kealahou API.
        target_list: the Target Grouping associated with the program (from
            ``KealahouProgramAssociation``; callers resolve it -- the association is a
            prerequisite for syncing).
        instrument: the program's instrument; selects which required-field values
            (magnitude band, Teff, RV) are extracted for display in the sync tables.

    Membership in the associated Target Grouping is the TOM-side participation flag, so
    the buckets are a pure function of (in the Target Grouping?) x (live in Kealahou?):
      - kealahou_only: live Kealahou targets whose TOM counterpart is not a member of the
        Target Grouping (never downloaded, or removed from the Target Grouping to withdraw
        it from TOM-side syncing). Downloading (re-)adds the membership.
      - tom_only: Target Grouping members with no *live* Kealahou counterpart (never
        uploaded, or their Kealahou target has vanished -- staging data resets can do that);
      - linked_in_sync / linked_discrepant: a Target Grouping member linked to a live
        Kealahou target, split by field comparison.
    """
    kealahou_by_token = {target_data.token: target_data for target_data in kealahou_targets}
    links = (KealahouTargetLink.objects.filter(program_token=program_token)
             .select_related('target'))
    links_by_token = {link.kealahou_target_token: link for link in links}
    member_target_ids = set(target_list.targets.values_list('id', flat=True))

    state = SyncState()

    linked_target_ids = set()
    for kealahou_target_token, target_data in kealahou_by_token.items():
        link = links_by_token.get(kealahou_target_token)
        if link is not None and link.target_id in member_target_ids:
            linked_target_ids.add(link.target_id)
            pair = LinkedTargetPair(link=link, target=link.target, target_data=target_data,
                                    discrepancies=compare_target_fields(link.target, target_data),
                                    required_field_values=required_field_values(target_data, instrument))
            if pair.discrepancies:
                state.linked_discrepant.append(pair)
            else:
                state.linked_in_sync.append(pair)
        else:
            # not participating on the TOM side: the previously-linked target (if any)
            # takes precedence over a name match for the "already in TOM" display
            existing_target = link.target if link is not None else Target.objects.filter(
                name=target_data.name).first()
            state.kealahou_only.append(ImportCandidate(
                target_data=target_data, existing_target=existing_target,
                required_field_values=required_field_values(target_data, instrument)))

    state.tom_only = list(target_list.targets.exclude(id__in=linked_target_ids))

    return state


def import_targets(program_token: str, selected_kealahou_target_tokens: list[str],
                   kealahou_targets: list[TargetData], target_list: TargetList) -> list[ResultMessage]:
    """Import the selected Kealahou targets into the TOM.

    Creates a TOM Target per selection (or links an existing same-name target), adds it
    to the program's associated Target Grouping, and records the ``KealahouTargetLink``.

    Returns:
        Per-target ``ResultMessage``s for display in the sync panel.
    """
    kealahou_by_token = {target_data.token: target_data for target_data in kealahou_targets}
    messages = []
    with transaction.atomic():
        for kealahou_target_token in selected_kealahou_target_tokens:
            target_data = kealahou_by_token.get(kealahou_target_token)
            if target_data is None:
                messages.append(ResultMessage(
                    f'{kealahou_target_token}: no longer present in Kealahou; skipped.', success=False))
                continue
            # a previously-linked target takes precedence (survives TOM-side renames);
            # then a same-name target; only then create a new one
            existing_link = KealahouTargetLink.objects.filter(
                kealahou_target_token=kealahou_target_token).select_related('target').first()
            existing_target = (existing_link.target if existing_link is not None
                               else Target.objects.filter(name=target_data.name).first())
            if existing_target is not None:
                target = existing_target
                messages.append(ResultMessage(
                    f'{target_data.name}: added existing TOM target to {target_list.name}.'))
            else:
                try:
                    target = target_from_target_data(target_data)
                except ValueError as e:
                    messages.append(ResultMessage(str(e), success=False))
                    continue
                target.save()
                messages.append(ResultMessage(f'{target_data.name}: created TOM target.'))
            target_list.targets.add(target)
            KealahouTargetLink.objects.update_or_create(
                kealahou_target_token=kealahou_target_token,
                defaults={'target': target, 'program_token': program_token, 'version': target_data.version},
            )
    return messages


def upload_targets(aeon_facility: AeonCFHTFacility, program_token: str, instrument: Instrument,
                   upload_requests: list[UploadRequest], target_list: TargetList) -> list[UploadResult]:
    """Upload TOM targets to Kealahou, one PUT per target.

    Rows fail independently: a validation or API error on one target is reported in its
    ``UploadResult`` and does not stop the rest. Successful uploads record/update the
    ``KealahouTargetLink`` (with Kealahou's returned lock version) and ensure membership
    in the program's associated Target Grouping.
    """
    results = []
    for upload_request in upload_requests:
        target = upload_request.target
        existing_link = KealahouTargetLink.objects.filter(target=target, program_token=program_token).first()
        try:
            target_data = target_data_from_target(
                target, program_token, instrument,
                magnitude=upload_request.magnitude,
                temperature_effective=upload_request.temperature_effective,
                radial_velocity_kmps=upload_request.radial_velocity_kmps,
                kealahou_target_token=existing_link.kealahou_target_token if existing_link else None,
                version=existing_link.version if existing_link else None,
            )
            returned = aeon_facility.create_or_update_target(program_token, target_data, instrument)
        except ValueError as e:
            results.append(UploadResult(target=target, success=False, message=str(e)))
            continue
        except pydantic.ValidationError as e:
            results.append(UploadResult(target=target, success=False, message=f'Invalid target data: {e}'))
            continue
        except httpx.HTTPStatusError as e:
            # Kealahou explains rejections (e.g. out-of-range values) in the response body
            api_messages = kealahou_api_error_messages(e)
            reason = '; '.join(api_messages) if api_messages else str(e)
            logger.warning(f'Kealahou rejected target {target.name!r} for program {program_token}: {reason}')
            results.append(UploadResult(target=target, success=False,
                                        message=f'Kealahou rejected the upload: {reason}'))
            continue
        except httpx.HTTPError as e:  # network-level failure (timeout, DNS, connection)
            logger.warning(f'Kealahou upload failed for target {target.name!r} in program {program_token}: {e}')
            results.append(UploadResult(target=target, success=False, message=f'Kealahou API error: {e}'))
            continue

        with transaction.atomic():
            target_list.targets.add(target)
            KealahouTargetLink.objects.update_or_create(
                kealahou_target_token=returned.token,
                defaults={'target': target, 'program_token': program_token, 'version': returned.version},
            )
        results.append(UploadResult(target=target, success=True,
                                    message=f'Uploaded to Kealahou (version {returned.version}).'))
    return results
