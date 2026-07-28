from __future__ import annotations

import logging

import httpx
import pydantic
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse_lazy
from django.views.generic import TemplateView
from django.views.generic.edit import UpdateView

from aeonlib.cfht.models import Instrument, ProgramInfo

from tom_targets.models import Target, TargetList

from tom_cfht import kealahou
from tom_cfht.cfht import CFHTFacility
from tom_cfht.models import CFHTProfile, KealahouProgramAssociation, KealahouTargetLink

logger = logging.getLogger(__name__)

# Exceptions that the htmx endpoints report as an alert in the partial (HTTP 200)
# rather than letting the request 500. Anything else is a genuine bug and should raise.
KEALAHOU_ERRORS = (httpx.HTTPError, pydantic.ValidationError, ImproperlyConfigured, ValueError)


class ProfileUpdateView(UpdateView):
    """
    View that handles updating of a user's ``CFHTProfile``.

    The CFHT Facility has a ``CFHTProfile`` model (see ``models.py``). This view updates
    the properties of that model.

    The ``CFHTProfile`` properties are displayed by the `cfht_user_profile.html`` template.
    This typically happens on the on the User Profile page via the ``show_app_profiles``
    inclusion tag (see ``tom_base/tom_common/templates/tom_common/user_profile.html`` and
    ``tom_base/tom_common/templatetags/user_extras.py::show_app_profiles``).
    """
    model = CFHTProfile
    template_name = 'tom_cfht/update_profile.html'
    fields = ['cfht_access_token']  # required by ModelFormMixin, a base class of this ProfileUpdateView

    def get_success_url(self):
        return reverse_lazy('user-profile')  # back to the TOMToolkit user-profile


class CFHTFacilityIndexView(LoginRequiredMixin, TemplateView):
    """The CFHT facility page (``/cfht/``), linked from the navbar Facilities menu.

    Renders immediately with static facility information; the observing-program tabs
    (and each program's sections) load asynchronously via htmx so a slow or unavailable
    Kealahou API never blocks the page.
    """
    template_name = 'tom_cfht/facility_index.html'

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        facility = CFHTFacility()
        context['observing_sites'] = facility.get_observing_sites()
        context['instruments'] = ['MegaCam', 'ESPaDOnS', 'SPIRou']  # what AEONlib/Kealahou support today
        return context


def _facility_for(request: HttpRequest) -> CFHTFacility:
    """Return a CFHTFacility bound to the requesting user (the tom_base credential pattern)."""
    facility = CFHTFacility()
    facility.set_user(request.user)
    return facility


def _program_by_token(programs: list[ProgramInfo], program_token: str) -> ProgramInfo | None:
    """Find a program in ``programs`` by its Kealahou program token."""
    for program in programs:
        if program.program_data is not None and program.program_data.token == program_token:
            return program
    return None


def _program_instrument(program: ProgramInfo) -> Instrument | None:
    """Return the program's (single) instrument, per Kealahou's one-instrument-per-program rule."""
    program_data = program.program_data
    if program_data is None or not program_data.time_allocation:
        return None
    return program_data.time_allocation[0].instrument


def _association_for(program_token: str) -> KealahouProgramAssociation | None:
    """Return the program's Target Grouping association, or None if the user hasn't made one."""
    return (KealahouProgramAssociation.objects.select_related('target_list')
            .filter(program_token=program_token).first())


def _target_grouping_gate_context(program_token: str, instrument: Instrument | None,
                                  origin: str, target: Target | None = None) -> dict:
    """Context for the select-or-create Target Grouping gate partial.

    Args:
        program_token: the program awaiting an association.
        instrument: the program's instrument (for the suggested default name).
        origin: which fragment the gate is rendered in and should re-render on submit:
            'targets-section' or 'target-status'.
        target: the target whose status fragment hosts the gate (origin 'target-status').
    """
    return {
        'program_token': program_token,
        'available_target_lists': TargetList.objects.order_by('name'),
        'default_target_list_name': kealahou.default_target_list_name(program_token, instrument),
        'origin': origin,
        'target': target,
    }


def _instrument_requirements_context(instrument: Instrument | None) -> dict:
    """Context describing the instrument's required upload fields, for the inline inputs."""
    return {
        'instrument': instrument.value if instrument else None,
        'magnitude_band': kealahou.REQUIRED_MAGNITUDE_BAND_BY_INSTRUMENT.get(instrument, '').upper(),
        'needs_temperature': instrument in kealahou.INSTRUMENTS_REQUIRING_TEMPERATURE,
        'needs_radial_velocity': instrument in kealahou.INSTRUMENTS_REQUIRING_RADIAL_VELOCITY,
    }


def _split_result_messages(result_messages: list[kealahou.ResultMessage] | None) -> dict:
    """Split ResultMessages into context lists: failures (danger alert) vs notices (info alert)."""
    result_messages = result_messages or []
    return {
        'error_messages': [message.text for message in result_messages if not message.success],
        'info_messages': [message.text for message in result_messages if message.success],
    }


def _render_targets_section(request: HttpRequest, program_token: str,
                            result_messages: list[kealahou.ResultMessage] | None = None) -> HttpResponse:
    """(Re-)render the Targets section of a program panel.

    Shows the Target Grouping gate until the user associates one with the program;
    afterwards, the four-bucket sync panel. Used by the GET endpoint and re-used by the
    POST endpoints (associate, sync-selected) so the section always reflects current state.
    """
    template_name = 'tom_cfht/partials/targets_section.html'
    try:
        facility = _facility_for(request)
        aeon_facility = facility.get_aeon_facility()
        program = _program_by_token(aeon_facility.programs(), program_token)
        if program is None:
            return render(request, template_name,
                          {'program_token': program_token,
                           'error': f'Program {program_token} was not found in Kealahou.'})
        instrument = _program_instrument(program)

        association = _association_for(program_token)
        if association is None:
            # the prerequisite gate: sync features wait until a Target Grouping is chosen
            return render(request, template_name, {
                'program_token': program_token,
                'target_grouping_gate': _target_grouping_gate_context(
                    program_token, instrument, origin='targets-section'),
            })

        kealahou_targets = aeon_facility.targets(program_token)
    except KEALAHOU_ERRORS as e:
        logger.warning(f'Kealahou targets section unavailable for program {program_token}: {e}')
        return render(request, template_name, {'program_token': program_token, 'error': str(e)})

    sync_state = kealahou.compute_sync_state(program_token, kealahou_targets, association.target_list,
                                             instrument=instrument)
    context = {
        'program_token': program_token,
        'sync_state': sync_state,
        'target_list': association.target_list,
    }
    context.update(_split_result_messages(result_messages))
    context.update(_instrument_requirements_context(instrument))
    return render(request, template_name, context)


@login_required
def observing_programs(request: HttpRequest) -> HttpResponse:
    """htmx partial: nav-tabs bar with one lazy-loaded tab per CFHT observing program."""
    template_name = 'tom_cfht/partials/observing_programs.html'
    try:
        facility = _facility_for(request)
        programs = facility.get_observing_programs()
    except KEALAHOU_ERRORS as e:
        logger.warning(f'Could not fetch Kealahou observing programs: {e}')
        return render(request, template_name, {'error': str(e)})

    # flatten the ProgramInfo models into what the tab bar displays
    program_rows = []
    for program in programs:
        program_data = program.program_data
        if program_data is None:
            continue
        instrument = _program_instrument(program)
        pi_info = program.pi_info
        completion_ratio = None
        if program_data.time_accounting is not None:
            completion_ratio = program_data.time_accounting.completion_ratio
        program_rows.append({
            'program_token': program_data.token,
            'title': program_data.title,
            'program_type': program_data.program_type.value if program_data.program_type else '',
            'instrument': instrument.value if instrument else '',
            'pi_name': f'{pi_info.first_name} {pi_info.last_name}' if pi_info else '',
            'percent_complete': round(completion_ratio * 100) if completion_ratio is not None else None,
        })
    return render(request, template_name, {'program_rows': program_rows})


@login_required
def program_panel(request: HttpRequest, program_token: str) -> HttpResponse:
    """htmx partial: one program's tab pane -- four full-width sections.

    Renders instantly with no Kealahou API call; the Targets section lazy-loads itself,
    and the Observing Templates / Observing Groups / Exposures sections are stubs until
    AEONlib wraps their endpoints.
    """
    return render(request, 'tom_cfht/partials/program_panel.html', {'program_token': program_token})


@login_required
def targets_section(request: HttpRequest, program_token: str) -> HttpResponse:
    """htmx partial: the Targets section (Target Grouping gate or four-bucket sync panel)."""
    return _render_targets_section(request, program_token)


@login_required
def associate_target_grouping(request: HttpRequest, program_token: str) -> HttpResponse:
    """htmx POST: satisfy the prerequisite by associating a Target Grouping with a program.

    Accepts either ``target_list_id`` (an existing Target Grouping) or
    ``new_target_list_name`` (creates one). Re-renders the fragment named by ``origin``.
    """
    target_list = None
    target_list_id = request.POST.get('target_list_id', '').strip()
    new_target_list_name = request.POST.get('new_target_list_name', '').strip()
    if target_list_id:
        target_list = TargetList.objects.filter(id=target_list_id).first()
    elif new_target_list_name:
        target_list, _ = TargetList.objects.get_or_create(name=new_target_list_name)

    if target_list is not None:
        KealahouProgramAssociation.objects.update_or_create(
            program_token=program_token, defaults={'target_list': target_list})

    # re-render whichever fragment hosted the gate
    if request.POST.get('origin') == 'target-status':
        target = Target.objects.filter(id=request.POST.get('target_id')).first()
        if target is None:
            return render(request, 'tom_cfht/partials/kealahou_target_status.html',
                          {'error': 'Unknown target.'})
        return _render_kealahou_status(request, target)
    return _render_targets_section(request, program_token)


def _upload_request_from_post(request: HttpRequest, target: Target) -> kealahou.UploadRequest:
    """Build an UploadRequest from the per-target inline inputs."""
    def parse_float(input_name: str) -> float | None:
        raw_value = request.POST.get(f'{input_name}_{target.id}', '').strip()
        return float(raw_value) if raw_value else None

    return kealahou.UploadRequest(
        target=target,
        magnitude=parse_float('magnitude'),
        temperature_effective=parse_float('temperature_effective'),
        radial_velocity_kmps=parse_float('radial_velocity_kmps'),
    )


def _do_upload(request: HttpRequest, program_token: str,
               targets: list[Target]) -> list[kealahou.ResultMessage]:
    """Upload ``targets`` to Kealahou using the inline form values; returns display messages."""
    association = _association_for(program_token)
    if association is None:
        return [kealahou.ResultMessage(
            f'Program {program_token} has no associated Target Grouping yet.', success=False)]
    facility = _facility_for(request)
    aeon_facility = facility.get_aeon_facility()
    program = _program_by_token(aeon_facility.programs(), program_token)
    if program is None:
        return [kealahou.ResultMessage(f'Program {program_token} was not found in Kealahou.', success=False)]
    instrument = _program_instrument(program)
    if instrument is None:
        return [kealahou.ResultMessage(
            f'Program {program_token} has no instrument allocation; cannot upload targets.', success=False)]

    try:
        upload_requests = [_upload_request_from_post(request, target) for target in targets]
    except ValueError as e:  # unparseable magnitude/Teff/RV input
        return [kealahou.ResultMessage(f'Invalid input: {e}', success=False)]
    upload_results = kealahou.upload_targets(aeon_facility, program_token, instrument,
                                             upload_requests, association.target_list)
    return [kealahou.ResultMessage(f'{result.target.name}: {result.message}', success=result.success)
            for result in upload_results]


@login_required
def sync_selected_targets(request: HttpRequest, program_token: str) -> HttpResponse:
    """htmx POST: sync exactly the checked rows, both directions.

    Checked "In Kealahou only" rows (``kealahou_target_token``) are imported into the TOM;
    checked "In TOM only" rows (``target_id``) are uploaded using their inline
    required-field inputs. Rows fail independently. Field-value discrepancies on linked
    targets are displayed by the panel but never auto-resolved.
    """
    selected_kealahou_target_tokens = request.POST.getlist('kealahou_target_token')
    selected_targets = list(Target.objects.filter(id__in=request.POST.getlist('target_id')))
    if not selected_kealahou_target_tokens and not selected_targets:
        return _render_targets_section(request, program_token,
                                       [kealahou.ResultMessage('No targets selected.')])

    result_messages = []
    association = _association_for(program_token)
    if association is None:
        return _render_targets_section(
            request, program_token,
            [kealahou.ResultMessage(f'Program {program_token} has no associated Target Grouping yet.',
                                    success=False)])
    if selected_kealahou_target_tokens:
        try:
            facility = _facility_for(request)
            kealahou_targets = facility.get_aeon_facility().targets(program_token)
            result_messages += kealahou.import_targets(program_token, selected_kealahou_target_tokens,
                                                       kealahou_targets, association.target_list)
        except KEALAHOU_ERRORS as e:
            result_messages.append(kealahou.ResultMessage(f'Download failed: {e}', success=False))
    if selected_targets:
        try:
            result_messages += _do_upload(request, program_token, selected_targets)
        except KEALAHOU_ERRORS as e:
            result_messages.append(kealahou.ResultMessage(f'Upload failed: {e}', success=False))
    return _render_targets_section(request, program_token, result_messages)


def _render_kealahou_status(request: HttpRequest, target: Target,
                            result_messages: list[kealahou.ResultMessage] | None = None) -> HttpResponse:
    """(Re-)render the observation form's Kealahou status fragment for one target.

    Each program row walks a state chain and shows exactly the next action:
    no association -> Target Grouping gate; not uploaded -> upload button; uploaded but
    missing from the Target Grouping -> add button; otherwise an "in Kealahou" badge.
    """
    template_name = 'tom_cfht/partials/kealahou_target_status.html'
    try:
        facility = _facility_for(request)
        programs = facility.get_observing_programs()
    except KEALAHOU_ERRORS as e:
        logger.warning(f'Kealahou status unavailable for target {target.id}: {e}')
        return render(request, template_name, {'error': str(e), 'target': target})

    links_by_program = {link.program_token: link
                        for link in KealahouTargetLink.objects.filter(target=target)}
    program_statuses = []
    for program in programs:
        program_data = program.program_data
        if program_data is None:
            continue
        instrument = _program_instrument(program)
        association = _association_for(program_data.token)
        link = links_by_program.get(program_data.token)
        in_target_list = (association is not None
                          and association.target_list.targets.filter(id=target.id).exists())
        status = {
            'program_token': program_data.token,
            'title': program_data.title,
            'link': link,
            'association': association,
            'in_target_list': in_target_list,
        }
        status.update(_instrument_requirements_context(instrument))
        if association is None:
            status['target_grouping_gate'] = _target_grouping_gate_context(
                program_data.token, instrument, origin='target-status', target=target)
        program_statuses.append(status)
    context = {'target': target, 'program_statuses': program_statuses}
    context.update(_split_result_messages(result_messages))
    return render(request, template_name, context)


@login_required
def kealahou_target_status(request: HttpRequest) -> HttpResponse:
    """htmx partial for the observation form: this target's Kealahou state per program."""
    try:
        target = Target.objects.get(id=request.GET.get('target_id'))
    except (Target.DoesNotExist, ValueError):
        return render(request, 'tom_cfht/partials/kealahou_target_status.html', {'error': 'Unknown target.'})
    return _render_kealahou_status(request, target)


@login_required
def upload_single_target(request: HttpRequest) -> HttpResponse:
    """htmx POST from the observation form: upload one target to one program, then
    re-render the Kealahou status fragment. The upload also adds the target to the
    program's associated Target Grouping."""
    program_token = request.POST.get('program_token', '')
    try:
        target = Target.objects.get(id=request.POST.get('target_id'))
    except (Target.DoesNotExist, ValueError):
        return render(request, 'tom_cfht/partials/kealahou_target_status.html', {'error': 'Unknown target.'})

    try:
        result_messages = _do_upload(request, program_token, [target])
    except KEALAHOU_ERRORS as e:
        logger.warning(f'Kealahou upload of target {target.id} to {program_token} failed: {e}')
        result_messages = [kealahou.ResultMessage(f'Upload failed: {e}', success=False)]
    return _render_kealahou_status(request, target, result_messages)


@login_required
def add_target_to_target_grouping(request: HttpRequest) -> HttpResponse:
    """htmx POST from the observation form: add an already-uploaded target to the
    program's associated Target Grouping (the rare state where it was removed by hand)."""
    program_token = request.POST.get('program_token', '')
    try:
        target = Target.objects.get(id=request.POST.get('target_id'))
    except (Target.DoesNotExist, ValueError):
        return render(request, 'tom_cfht/partials/kealahou_target_status.html', {'error': 'Unknown target.'})

    association = _association_for(program_token)
    if association is None:
        result_messages = [kealahou.ResultMessage(
            f'Program {program_token} has no associated Target Grouping yet.', success=False)]
    else:
        association.target_list.targets.add(target)
        result_messages = [kealahou.ResultMessage(
            f'{target.name}: added to Target Grouping "{association.target_list.name}".')]
    return _render_kealahou_status(request, target, result_messages)
