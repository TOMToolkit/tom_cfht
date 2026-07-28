"""Tests for the tom_cfht Kealahou target-sync feature.

NOTE: to run these tests in your venv: python ./tom_cfht/tests/run_tests.py
The aeonlib Kealahou facade is mocked with unittest.mock throughout: aeonlib uses httpx,
which the `responses` library cannot intercept, and mocking at the facade keeps the tests
offline and fast.
"""
from unittest import mock

import httpx
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from aeonlib.cfht.models import (
    AllocationData,
    FixedTargetProperMotion,
    Instrument,
    ProgramData,
    ProgramInfo,
    ProgramInfoPiInfo,
    SkyCoordinate,
    TargetData,
    TargetDataFixedTarget,
)

from tom_observations.facility import CredentialStatus
from tom_targets.models import Target, TargetList

from tom_cfht import kealahou
from tom_cfht.cfht import CFHTFacility
from tom_cfht.models import CFHTProfile, KealahouProgramAssociation, KealahouTargetLink
from tom_cfht.tests.factories import NonSiderealTargetFactory, SiderealTargetFactory

PROGRAM_TOKEN = '25BE25'
CFHT_FACILITIES_SETTING = {'CFHT': {'CFHT_ACCESS_TOKEN': 'tom-wide-access-token'}}


def make_target_data(kealahou_target_token: str, name: str, ra: float = 150.0, dec: float = 30.0,
                     pm_ra: float = None, pm_dec: float = None, version: int = 1) -> TargetData:
    """Build a Kealahou fixed-target TargetData for test fixtures."""
    proper_motion = None
    if pm_ra is not None or pm_dec is not None:
        proper_motion = FixedTargetProperMotion(ra_mas=pm_ra, dec_mas=pm_dec)
    return TargetData(
        token=kealahou_target_token, name=name, version=version,
        fixed_target=TargetDataFixedTarget(coordinate=SkyCoordinate(ra=ra, dec=dec),
                                           proper_motion=proper_motion))


def make_program(program_token: str = PROGRAM_TOKEN, instrument: Instrument = Instrument.megacam) -> ProgramInfo:
    """Build a Kealahou ProgramInfo for test fixtures."""
    return ProgramInfo(
        program_data=ProgramData(token=program_token, title='Test Program',
                                 time_allocation=[AllocationData(instrument=instrument)]),
        pi_info=ProgramInfoPiInfo(first_name='AEON', last_name='Test'))


def make_association(program_token: str = PROGRAM_TOKEN,
                     target_list_name: str = 'CFHT-MEGACAM-25BE25') -> KealahouProgramAssociation:
    """Create the program's Target Grouping and its association, for tests past the gate."""
    target_list, _ = TargetList.objects.get_or_create(name=target_list_name)
    association, _ = KealahouProgramAssociation.objects.get_or_create(
        program_token=program_token, defaults={'target_list': target_list})
    return association


class TestTargetDataMapping(TestCase):
    """tom_cfht.kealahou: TOM Target <-> aeonlib TargetData mapping."""

    def test_target_data_from_target_maps_fields(self):
        target = SiderealTargetFactory.create(name='SN 2026abc', ra=150.0, dec=30.0, pm_ra=1.5, pm_dec=-2.0)
        target_data = kealahou.target_data_from_target(target, PROGRAM_TOKEN, Instrument.megacam, magnitude=20.5)
        self.assertEqual(target_data.name, 'SN 2026abc')
        self.assertRegex(target_data.token, rf'^{PROGRAM_TOKEN}-\d{{10}}$')
        self.assertEqual(target_data.fixed_target.coordinate.ra, 150.0)
        self.assertEqual(target_data.fixed_target.coordinate.dec, 30.0)
        self.assertEqual(target_data.fixed_target.proper_motion.ra_mas, 1.5)
        self.assertEqual(target_data.fixed_target.proper_motion.dec_mas, -2.0)
        self.assertEqual(target_data.magnitude.ab.value, 20.5)  # MegaCam requires AB
        self.assertIsNone(target_data.version)  # a new upload carries no lock version
        # Kealahou requires a pointing offset; we default to the system "no offset" entity
        self.assertEqual(target_data.pointing_offset_token, '00AZ00-PO+MEGACAM+1')

    def test_existing_identifiers_are_reused_for_updates(self):
        target = SiderealTargetFactory.create(name='ReUpload')
        target_data = kealahou.target_data_from_target(
            target, PROGRAM_TOKEN, Instrument.megacam, magnitude=20.0,
            kealahou_target_token=f'{PROGRAM_TOKEN}-1234567890', version=4)
        self.assertEqual(target_data.token, f'{PROGRAM_TOKEN}-1234567890')
        self.assertEqual(target_data.version, 4)

    def test_megacam_requires_magnitude(self):
        target = SiderealTargetFactory.create(name='NoMag')
        with self.assertRaises(ValueError):
            kealahou.target_data_from_target(target, PROGRAM_TOKEN, Instrument.megacam)

    def test_spirou_requires_temperature_and_radial_velocity(self):
        target = SiderealTargetFactory.create(name='SpirouTarget')
        with self.assertRaises(ValueError):  # missing Teff
            kealahou.target_data_from_target(target, PROGRAM_TOKEN, Instrument.spirou,
                                             magnitude=9.5, radial_velocity_kmps=12.0)
        with self.assertRaises(ValueError):  # missing RV
            kealahou.target_data_from_target(target, PROGRAM_TOKEN, Instrument.spirou,
                                             magnitude=9.5, temperature_effective=4800.0)
        target_data = kealahou.target_data_from_target(
            target, PROGRAM_TOKEN, Instrument.spirou,
            magnitude=9.5, temperature_effective=4800.0, radial_velocity_kmps=12.0)
        self.assertEqual(target_data.magnitude.h.value, 9.5)  # SPIRou requires H
        self.assertEqual(target_data.temperature_effective, 4800.0)
        self.assertEqual(target_data.fixed_target.estimated_radial_velocity_kmps.value, 12.0)

    def test_name_rules_enforced(self):
        too_long = SiderealTargetFactory.create(name='X' * (kealahou.TARGET_NAME_MAX_LENGTH + 1))
        with self.assertRaises(ValueError):
            kealahou.target_data_from_target(too_long, PROGRAM_TOKEN, Instrument.megacam, magnitude=20.0)
        bad_characters = SiderealTargetFactory.create(name='naïve~name')
        with self.assertRaises(ValueError):
            kealahou.target_data_from_target(bad_characters, PROGRAM_TOKEN, Instrument.megacam, magnitude=20.0)

    def test_non_sidereal_target_rejected(self):
        moving_target = NonSiderealTargetFactory.create(name='Comet')
        with self.assertRaises(ValueError):
            kealahou.target_data_from_target(moving_target, PROGRAM_TOKEN, Instrument.megacam, magnitude=20.0)

    def test_target_from_target_data(self):
        target_data = make_target_data(f'{PROGRAM_TOKEN}-1111111111', 'Imported', ra=10.0, dec=-5.0,
                                       pm_ra=3.0, pm_dec=4.0)
        target = kealahou.target_from_target_data(target_data)
        self.assertEqual(target.name, 'Imported')
        self.assertEqual(target.type, Target.SIDEREAL)
        self.assertEqual((target.ra, target.dec, target.pm_ra, target.pm_dec), (10.0, -5.0, 3.0, 4.0))

    def test_moving_target_import_rejected(self):
        moving_target_data = TargetData(token=f'{PROGRAM_TOKEN}-2222222222', name='Mover')
        with self.assertRaises(ValueError):
            kealahou.target_from_target_data(moving_target_data)

    def test_required_field_values_extracts_kealahou_side_values(self):
        target_data = kealahou.target_data_from_target(
            SiderealTargetFactory.create(name='SpirouValues'), '25BE30', Instrument.spirou,
            magnitude=9.5, temperature_effective=4800.0, radial_velocity_kmps=12.0)
        values = kealahou.required_field_values(target_data, Instrument.spirou)
        self.assertEqual(values, {'magnitude': 9.5, 'temperature_effective': 4800.0,
                                  'radial_velocity_kmps': 12.0})
        # a Kealahou target with none of the fields set yields Nones (displayed as em-dashes)
        bare_target_data = make_target_data(f'{PROGRAM_TOKEN}-3333333333', 'Bare')
        values = kealahou.required_field_values(bare_target_data, Instrument.megacam)
        self.assertEqual(values, {'magnitude': None, 'temperature_effective': None,
                                  'radial_velocity_kmps': None})

    def test_default_target_list_name_includes_instrument(self):
        self.assertEqual(kealahou.default_target_list_name(PROGRAM_TOKEN, Instrument.megacam),
                         'CFHT-MEGACAM-25BE25')
        self.assertEqual(kealahou.default_target_list_name(PROGRAM_TOKEN, None), 'CFHT-UNKNOWN-25BE25')


class TestCompareTargetFields(TestCase):
    """tom_cfht.kealahou.compare_target_fields: the discrepancy display's field diff."""

    def test_agreeing_targets_have_no_discrepancies(self):
        target = SiderealTargetFactory.create(name='Same', ra=150.0, dec=30.0, pm_ra=1.0, pm_dec=2.0)
        target_data = make_target_data('t', 'Same', ra=150.0, dec=30.0, pm_ra=1.0, pm_dec=2.0)
        self.assertEqual(kealahou.compare_target_fields(target, target_data), [])

    def test_sexagesimal_round_trip_noise_ignored(self):
        # the Kealahou web UI stores RA re-parsed from 0.1s-of-time sexagesimal, introducing
        # up to 0.75 arcsec of representation noise (0.60 arcsec measured on staging for M107)
        target = SiderealTargetFactory.create(name='M107ish', ra=248.13275, dec=-13.0537778,
                                              pm_ra=None, pm_dec=None)
        round_tripped_ra = 248.13291666666672  # = 16h32m31.9s, 0.60 arcsec away
        target_data = make_target_data('t', 'M107ish', ra=round_tripped_ra, dec=-13.053777777777778)
        self.assertEqual(kealahou.compare_target_fields(target, target_data), [])

    def test_coordinate_drift_beyond_tolerance_reported(self):
        target = SiderealTargetFactory.create(name='Drifted', ra=150.0, dec=30.0, pm_ra=None, pm_dec=None)
        two_arcsec = 2.0 / 3600.0
        target_data = make_target_data('t', 'Drifted', ra=150.0 + two_arcsec, dec=30.0)
        discrepancies = kealahou.compare_target_fields(target, target_data)
        self.assertEqual(len(discrepancies), 1)
        self.assertEqual(discrepancies[0].field_name, 'ra')
        self.assertEqual((discrepancies[0].tom_value, discrepancies[0].kealahou_value),
                         (150.0, 150.0 + two_arcsec))

    def test_one_sided_proper_motion_reported(self):
        target = SiderealTargetFactory.create(name='PM', ra=1.0, dec=1.0, pm_ra=5.0, pm_dec=None)
        target_data = make_target_data('t', 'PM', ra=1.0, dec=1.0)  # Kealahou side has no proper motion
        field_names = [d.field_name for d in kealahou.compare_target_fields(target, target_data)]
        self.assertEqual(field_names, ['pm_ra'])


class TestComputeSyncState(TestCase):
    """tom_cfht.kealahou.compute_sync_state: the four-bucket partition."""

    def setUp(self):
        self.association = make_association()
        self.target_list = self.association.target_list
        self.linked_target = SiderealTargetFactory.create(name='Linked', ra=150.0, dec=30.0,
                                                          pm_ra=None, pm_dec=None)
        self.linked_target_data = make_target_data(f'{PROGRAM_TOKEN}-1000000001', 'Linked',
                                                   ra=150.0, dec=30.0, version=2)
        KealahouTargetLink.objects.create(target=self.linked_target, program_token=PROGRAM_TOKEN,
                                          kealahou_target_token=self.linked_target_data.token, version=2)
        self.target_list.targets.add(self.linked_target)

    def test_buckets_partition_correctly(self):
        kealahou_only_data = make_target_data(f'{PROGRAM_TOKEN}-1000000002', 'KealahouOnly')
        tom_only_target = SiderealTargetFactory.create(name='TOMOnly')
        self.target_list.targets.add(tom_only_target)

        sync_state = kealahou.compute_sync_state(
            PROGRAM_TOKEN, [self.linked_target_data, kealahou_only_data], self.target_list)

        self.assertEqual([c.target_data.name for c in sync_state.kealahou_only], ['KealahouOnly'])
        self.assertEqual([t.name for t in sync_state.tom_only], ['TOMOnly'])
        self.assertEqual([p.target.name for p in sync_state.linked_in_sync], ['Linked'])
        self.assertEqual(sync_state.linked_discrepant, [])

    def test_discrepant_linked_target_bucketed_with_field_details(self):
        drifted_data = make_target_data(self.linked_target_data.token, 'Linked', ra=151.0, dec=30.0, version=3)
        sync_state = kealahou.compute_sync_state(PROGRAM_TOKEN, [drifted_data], self.target_list)
        self.assertEqual(sync_state.linked_in_sync, [])
        self.assertEqual(len(sync_state.linked_discrepant), 1)
        self.assertEqual([d.field_name for d in sync_state.linked_discrepant[0].discrepancies], ['ra'])

    def test_name_collision_candidate_carries_existing_target(self):
        SiderealTargetFactory.create(name='AlreadyHere')
        colliding_data = make_target_data(f'{PROGRAM_TOKEN}-1000000003', 'AlreadyHere')
        sync_state = kealahou.compute_sync_state(
            PROGRAM_TOKEN, [self.linked_target_data, colliding_data], self.target_list)
        self.assertEqual(sync_state.kealahou_only[0].existing_target.name, 'AlreadyHere')

    def test_stale_link_puts_target_back_in_tom_only(self):
        # the linked target's Kealahou counterpart vanished (e.g. staging data reset)
        sync_state = kealahou.compute_sync_state(PROGRAM_TOKEN, [], self.target_list)
        self.assertEqual([t.name for t in sync_state.tom_only], ['Linked'])

    def test_removed_member_returns_to_kealahou_only(self):
        # the M107 scenario: linked to the program, but not (or no longer) a member of the
        # associated Target Grouping -> TOM-side participation withdrawn, so it shows as
        # "In Kealahou only", with the previously-linked target offered for re-linking
        self.target_list.targets.remove(self.linked_target)
        sync_state = kealahou.compute_sync_state(PROGRAM_TOKEN, [self.linked_target_data], self.target_list)
        self.assertEqual(sync_state.linked_in_sync, [])
        self.assertEqual(sync_state.tom_only, [])
        self.assertEqual([c.target_data.name for c in sync_state.kealahou_only], ['Linked'])
        self.assertEqual(sync_state.kealahou_only[0].existing_target, self.linked_target)


class TestImportTargets(TestCase):
    """tom_cfht.kealahou.import_targets."""

    def setUp(self):
        self.target_list = make_association().target_list

    def test_import_creates_target_and_link_in_associated_target_grouping(self):
        target_data = make_target_data(f'{PROGRAM_TOKEN}-1000000004', 'NewImport', ra=12.0, dec=34.0, version=7)
        messages = kealahou.import_targets(PROGRAM_TOKEN, [target_data.token], [target_data], self.target_list)

        target = Target.objects.get(name='NewImport')
        self.assertEqual((target.ra, target.dec), (12.0, 34.0))
        self.assertIn(target, self.target_list.targets.all())
        link = KealahouTargetLink.objects.get(kealahou_target_token=target_data.token)
        self.assertEqual((link.target, link.program_token, link.version), (target, PROGRAM_TOKEN, 7))
        self.assertTrue(any('created' in message.text for message in messages))

    def test_import_links_existing_same_name_target(self):
        existing_target = SiderealTargetFactory.create(name='AlreadyInTOM')
        target_data = make_target_data(f'{PROGRAM_TOKEN}-1000000005', 'AlreadyInTOM')
        messages = kealahou.import_targets(PROGRAM_TOKEN, [target_data.token], [target_data], self.target_list)

        self.assertEqual(Target.objects.filter(name='AlreadyInTOM').count(), 1)  # no duplicate
        link = KealahouTargetLink.objects.get(kealahou_target_token=target_data.token)
        self.assertEqual(link.target, existing_target)
        self.assertTrue(any('added existing' in message.text for message in messages))

    def test_reimport_reuses_previously_linked_target_despite_rename(self):
        # withdraw-then-redownload must find the linked target by its link, not its name,
        # so a TOM-side rename doesn't produce a duplicate target on re-download
        renamed_target = SiderealTargetFactory.create(name='NewName')
        kealahou_target_token = f'{PROGRAM_TOKEN}-1000000006'
        KealahouTargetLink.objects.create(target=renamed_target, program_token=PROGRAM_TOKEN,
                                          kealahou_target_token=kealahou_target_token, version=1)
        target_data = make_target_data(kealahou_target_token, 'OldName', version=2)
        kealahou.import_targets(PROGRAM_TOKEN, [kealahou_target_token], [target_data], self.target_list)

        self.assertFalse(Target.objects.filter(name='OldName').exists())  # no duplicate created
        self.assertIn(renamed_target, self.target_list.targets.all())
        link = KealahouTargetLink.objects.get(kealahou_target_token=kealahou_target_token)
        self.assertEqual((link.target, link.version), (renamed_target, 2))


class TestUploadTargets(TestCase):
    """tom_cfht.kealahou.upload_targets, with the aeonlib facade mocked."""

    def setUp(self):
        self.target_list = make_association().target_list
        self.aeon_facility = mock.Mock()
        # echo back the submitted TargetData with a server-incremented version
        self.aeon_facility.create_or_update_target.side_effect = (
            lambda program_token, target_data, instrument: target_data.model_copy(
                update={'version': (target_data.version or 0) + 1}))

    def test_successful_upload_records_link_and_version(self):
        target = SiderealTargetFactory.create(name='Uploadable', pm_ra=None, pm_dec=None)
        results = kealahou.upload_targets(
            self.aeon_facility, PROGRAM_TOKEN, Instrument.megacam,
            [kealahou.UploadRequest(target=target, magnitude=21.0)], self.target_list)

        self.assertTrue(results[0].success)
        self.aeon_facility.create_or_update_target.assert_called_once()
        link = KealahouTargetLink.objects.get(target=target, program_token=PROGRAM_TOKEN)
        self.assertEqual(link.version, 1)
        self.assertIn(target, self.target_list.targets.all())

    def test_row_failures_are_isolated(self):
        good_target = SiderealTargetFactory.create(name='Good', pm_ra=None, pm_dec=None)
        no_magnitude_target = SiderealTargetFactory.create(name='NoMag', pm_ra=None, pm_dec=None)
        results = kealahou.upload_targets(
            self.aeon_facility, PROGRAM_TOKEN, Instrument.megacam,
            [kealahou.UploadRequest(target=no_magnitude_target),  # fails validation, no API call
             kealahou.UploadRequest(target=good_target, magnitude=20.0)], self.target_list)

        self.assertEqual([result.success for result in results], [False, True])
        self.assertEqual(self.aeon_facility.create_or_update_target.call_count, 1)

    def test_api_error_reported_per_row(self):
        self.aeon_facility.create_or_update_target.side_effect = httpx.HTTPError('Kealahou is down')
        target = SiderealTargetFactory.create(name='Unlucky', pm_ra=None, pm_dec=None)
        results = kealahou.upload_targets(
            self.aeon_facility, PROGRAM_TOKEN, Instrument.megacam,
            [kealahou.UploadRequest(target=target, magnitude=20.0)], self.target_list)
        self.assertFalse(results[0].success)
        self.assertIn('Kealahou is down', results[0].message)
        self.assertFalse(KealahouTargetLink.objects.filter(target=target).exists())

    def test_kealahou_rejection_surfaces_api_error_messages(self):
        # a 422 rejection carries {"error": {"messages": [...]}} explaining what was wrong;
        # the row message must show that, not httpx's generic status line
        rejection = httpx.HTTPStatusError(
            "Client error '422 Unprocessable Entity'",
            request=httpx.Request('PUT', 'https://api-stage.cfht.hawaii.edu/'),
            response=httpx.Response(
                422, json={'error': {'messages': ['temperature_effective must be between 2500 and 10000.']}},
                request=httpx.Request('PUT', 'https://api-stage.cfht.hawaii.edu/')))
        self.aeon_facility.create_or_update_target.side_effect = rejection
        target = SiderealTargetFactory.create(name='Rejected', pm_ra=None, pm_dec=None)
        results = kealahou.upload_targets(
            self.aeon_facility, PROGRAM_TOKEN, Instrument.megacam,
            [kealahou.UploadRequest(target=target, magnitude=20.0)], self.target_list)
        self.assertFalse(results[0].success)
        self.assertIn('temperature_effective must be between 2500 and 10000.', results[0].message)


@override_settings(FACILITIES=CFHT_FACILITIES_SETTING)
class TestGetAccessToken(TestCase):
    """CFHTFacility.get_access_token: profile token > settings default, with status tracking."""

    def setUp(self):
        self.user = User.objects.create_user(username='astronomer', password='pw')
        self.facility = CFHTFacility()
        self.facility.set_user(self.user)

    def test_profile_token_wins(self):
        CFHTProfile.objects.create(user=self.user, cfht_access_token='profile-access-token')
        self.assertEqual(self.facility.get_access_token(), 'profile-access-token')
        self.assertEqual(self.facility.credential_status, CredentialStatus.USING_USER_CREDS)

    def test_settings_fallback_when_profile_empty(self):
        CFHTProfile.objects.create(user=self.user, cfht_access_token='')
        self.assertEqual(self.facility.get_access_token(), 'tom-wide-access-token')
        self.assertEqual(self.facility.credential_status, CredentialStatus.USING_DEFAULTS)

    @override_settings(FACILITIES={'CFHT': {'CFHT_ACCESS_TOKEN': ''}})
    def test_no_token_anywhere_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            self.facility.get_access_token()
        self.assertEqual(self.facility.credential_status, CredentialStatus.PROFILE_EMPTY)


@override_settings(FACILITIES=CFHT_FACILITIES_SETTING)
class TestFacilityPageViews(TestCase):
    """The /cfht/ page and its htmx partials, with the aeonlib boundary mocked."""

    def setUp(self):
        self.user = User.objects.create_user(username='astronomer', password='pw')
        self.client.force_login(self.user)

    def test_index_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('tom_cfht:facility-index'))
        self.assertEqual(response.status_code, 302)

    def test_index_renders_static_info(self):
        response = self.client.get(reverse('tom_cfht:facility-index'))
        self.assertContains(response, 'Canada-France-Hawaii Telescope')
        self.assertContains(response, 'Observing Programs')

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_observing_programs_renders_lazy_tabs(self, mock_facility_class):
        mock_facility_class.return_value.get_observing_programs.return_value = [
            make_program(), make_program('25BE30', Instrument.spirou)]
        response = self.client.get(reverse('tom_cfht:observing-programs'))
        self.assertContains(response, '25BE25 &middot; MEGACAM')
        self.assertContains(response, '25BE30 &middot; SPIROU')
        # tabs lazy-load their panes: first on load, the rest on first click
        self.assertContains(response, 'hx-trigger="load once"', count=1)
        self.assertContains(response, 'hx-trigger="click once"', count=1)

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_observing_programs_api_error_shows_alert(self, mock_facility_class):
        mock_facility_class.return_value.get_observing_programs.side_effect = httpx.ConnectError('no route')
        response = self.client.get(reverse('tom_cfht:observing-programs'))
        self.assertEqual(response.status_code, 200)  # htmx partial, never a 500
        self.assertContains(response, 'Could not reach Kealahou')

    def test_program_panel_has_four_sections(self):
        response = self.client.get(reverse('tom_cfht:program-panel', args=[PROGRAM_TOKEN]))
        for section_title in ['Targets', 'Observing Templates', 'Observing Groups', 'Exposures']:
            self.assertContains(response, section_title)

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_targets_section_shows_gate_until_associated(self, mock_facility_class):
        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        response = self.client.get(reverse('tom_cfht:targets-section', args=[PROGRAM_TOKEN]))
        self.assertContains(response, 'not yet associated with a Target Grouping')
        self.assertContains(response, 'CFHT-MEGACAM-25BE25')  # suggested default name
        aeon_facility.targets.assert_not_called()  # gated: no target fetch before association

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_associate_by_creating_new_target_grouping(self, mock_facility_class):
        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        aeon_facility.targets.return_value = []
        response = self.client.post(
            reverse('tom_cfht:associate-target-grouping', args=[PROGRAM_TOKEN]),
            {'new_target_list_name': 'CFHT-MEGACAM-25BE25', 'origin': 'targets-section'})
        self.assertEqual(response.status_code, 200)
        association = KealahouProgramAssociation.objects.get(program_token=PROGRAM_TOKEN)
        self.assertEqual(association.target_list.name, 'CFHT-MEGACAM-25BE25')
        self.assertContains(response, 'Target Grouping:')  # gate replaced by the sync panel

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_associate_with_existing_target_grouping(self, mock_facility_class):
        existing_target_list = TargetList.objects.create(name='My Grouping')
        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        aeon_facility.targets.return_value = []
        self.client.post(reverse('tom_cfht:associate-target-grouping', args=[PROGRAM_TOKEN]),
                         {'target_list_id': existing_target_list.id, 'origin': 'targets-section'})
        association = KealahouProgramAssociation.objects.get(program_token=PROGRAM_TOKEN)
        self.assertEqual(association.target_list, existing_target_list)

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_targets_section_renders_four_buckets_when_associated(self, mock_facility_class):
        make_association()
        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        aeon_facility.targets.return_value = [make_target_data(f'{PROGRAM_TOKEN}-1000000006', 'KealahouOnly')]
        response = self.client.get(reverse('tom_cfht:targets-section', args=[PROGRAM_TOKEN]))
        self.assertContains(response, 'In Kealahou only')
        self.assertContains(response, 'In CFHT-MEGACAM-25BE25 only')  # named for the associated Target Grouping
        self.assertContains(response, 'in sync')
        self.assertContains(response, 'with discrepancies')
        self.assertContains(response, 'KealahouOnly')
        # per-section action buttons render only when their bucket has rows
        self.assertContains(response, 'Download selected targets')  # one kealahou-only row above
        self.assertNotContains(response, 'Upload selected targets')  # tom_only is empty

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_sync_selected_acts_on_exactly_the_checked_rows(self, mock_facility_class):
        association = make_association()
        checked_token = f'{PROGRAM_TOKEN}-1000000007'
        unchecked_token = f'{PROGRAM_TOKEN}-1000000008'
        checked_target = SiderealTargetFactory.create(name='CheckedUpload', pm_ra=None, pm_dec=None)
        unchecked_target = SiderealTargetFactory.create(name='UncheckedUpload', pm_ra=None, pm_dec=None)
        association.target_list.targets.add(checked_target, unchecked_target)

        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        aeon_facility.targets.return_value = [make_target_data(checked_token, 'CheckedImport'),
                                              make_target_data(unchecked_token, 'UncheckedImport')]
        aeon_facility.create_or_update_target.side_effect = (
            lambda program_token, target_data, instrument: target_data.model_copy(update={'version': 1}))

        response = self.client.post(
            reverse('tom_cfht:sync-selected-targets', args=[PROGRAM_TOKEN]),
            {'kealahou_target_token': [checked_token],
             'target_id': [str(checked_target.id)],
             f'magnitude_{checked_target.id}': '20.5'})

        self.assertEqual(response.status_code, 200)
        # import: only the checked Kealahou row
        self.assertTrue(Target.objects.filter(name='CheckedImport').exists())
        self.assertFalse(Target.objects.filter(name='UncheckedImport').exists())
        # upload: only the checked TOM row
        self.assertEqual(aeon_facility.create_or_update_target.call_count, 1)
        uploaded_target_data = aeon_facility.create_or_update_target.call_args.args[1]
        self.assertEqual(uploaded_target_data.name, 'CheckedUpload')

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_upload_failure_renders_danger_alert(self, mock_facility_class):
        association = make_association()
        target = SiderealTargetFactory.create(name='WillFail', pm_ra=None, pm_dec=None)
        association.target_list.targets.add(target)
        aeon_facility = mock_facility_class.return_value.get_aeon_facility.return_value
        aeon_facility.programs.return_value = [make_program()]
        aeon_facility.targets.return_value = []
        aeon_facility.create_or_update_target.side_effect = httpx.HTTPStatusError(
            "Client error '422 Unprocessable Entity'",
            request=httpx.Request('PUT', 'https://api-stage.cfht.hawaii.edu/'),
            response=httpx.Response(422, json={'error': {'messages': ['magnitude out of range.']}},
                                    request=httpx.Request('PUT', 'https://api-stage.cfht.hawaii.edu/')))
        response = self.client.post(
            reverse('tom_cfht:sync-selected-targets', args=[PROGRAM_TOKEN]),
            {'target_id': [str(target.id)], f'magnitude_{target.id}': '99.0'})
        # failures go in a bootstrap danger alert (not info) and carry Kealahou's own message
        self.assertContains(response, 'alert-danger')
        self.assertContains(response, 'magnitude out of range.')

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_kealahou_target_status_walks_the_state_chain(self, mock_facility_class):
        target = SiderealTargetFactory.create(name='FormTarget', pm_ra=None, pm_dec=None)
        mock_facility_class.return_value.get_observing_programs.return_value = [make_program()]
        status_url = reverse('tom_cfht:kealahou-target-status')

        # state 2: no association -> the Target Grouping gate
        response = self.client.get(status_url, {'target_id': target.id})
        self.assertContains(response, 'not yet associated with a Target Grouping')

        # state 3: associated, not uploaded -> upload button naming the Target Grouping
        association = make_association()
        response = self.client.get(status_url, {'target_id': target.id})
        self.assertContains(response, 'not in Kealahou')
        self.assertContains(response, 'Upload to Kealahou (adds to')

        # state 4: uploaded but removed from the Target Grouping -> add button
        KealahouTargetLink.objects.create(target=target, program_token=PROGRAM_TOKEN,
                                          kealahou_target_token=f'{PROGRAM_TOKEN}-1000000009', version=1)
        response = self.client.get(status_url, {'target_id': target.id})
        self.assertContains(response, 'Add to Target Grouping')

        # state 5: uploaded and in the Target Grouping -> just the badge
        association.target_list.targets.add(target)
        response = self.client.get(status_url, {'target_id': target.id})
        self.assertContains(response, 'in Kealahou')
        self.assertNotContains(response, 'Add to Target Grouping')

    @mock.patch('tom_cfht.views.CFHTFacility')
    def test_add_target_to_target_grouping_endpoint(self, mock_facility_class):
        association = make_association()
        target = SiderealTargetFactory.create(name='ReAdd', pm_ra=None, pm_dec=None)
        KealahouTargetLink.objects.create(target=target, program_token=PROGRAM_TOKEN,
                                          kealahou_target_token=f'{PROGRAM_TOKEN}-1000000010', version=1)
        mock_facility_class.return_value.get_observing_programs.return_value = [make_program()]
        response = self.client.post(reverse('tom_cfht:add-target-to-target-grouping'),
                                    {'target_id': target.id, 'program_token': PROGRAM_TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertIn(target, association.target_list.targets.all())

    def test_facility_declares_observation_form_template(self):
        # ObservationCreateView.get_template_names() picks this up (tom_observations/views.py)
        self.assertEqual(CFHTFacility.template_name, 'tom_cfht/observation_form.html')
