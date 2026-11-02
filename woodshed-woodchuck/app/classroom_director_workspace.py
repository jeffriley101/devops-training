"""Three separate director sources, with the approved individual field contract."""
from sqlalchemy import select

from . import classroom as s1, classroom_s2 as s2, classroom_management_views as management
from . import classroom_dashboard as charts, classroom_comprehensive_reporting as reporting
from .classroom_models import ClassroomStudentMembership


def _administration(detail):
    room = detail['selected_class']
    return {'program_id': detail['program_id'], 'program_name': detail['program_name'],
        'class_id': room['id'], 'class_name': room['display_name'], 'class_state': room['state'],
        'enrollment_open': room['enrollment_open'], 'manage': detail['manage'], 'teaching': room['teaching']}


def _statistics(authorized=None):
    return reporting.unavailable(authorized)


@s2._mutation
def class_view(session, *, actor, program_id, class_id, before=None):
    detail = management.detail(session, actor=actor, program_id=program_id, class_id=class_id,
        before=before, section='roster')
    admin = _administration(detail)
    admin.update(roster=detail['selected_class']['roster'], next_before=detail['selected_class']['next_roster'])
    # Administrative membership is never used to query practice records. The
    # existing Class chart reader stays on its own explicitly labelled page.
    return {'admin': admin, 'statistics': _statistics(),
        'shared_pcharts': {'source': 'explicitly_shared_charts', 'available': admin['teaching'],
            'class_url': f'/classroom/director/{program_id}/classes/{class_id}', 'students': []}}


@s2._mutation
def student_view(session, *, actor, program_id, class_id, membership_id, selected_week=None):
    if type(membership_id) is not int or not 1 <= membership_id < 2147483647:
        raise s1.ClassroomDenied('Class student unavailable.')
    # Seek the exact membership in the accepted bounded roster reader. Do not
    # expose the other page rows or accept a profile ID/WC# as authority.
    detail = management.detail(session, actor=actor, program_id=program_id, class_id=class_id,
        before=membership_id + 1, section='roster')
    member = next((row for row in detail['selected_class']['roster'] if row['id'] == membership_id), None)
    if member is None:
        raise s1.ClassroomDenied('Class student unavailable.')
    admin = _administration(detail)
    admin['student'] = member
    statistics = _statistics(False)
    shared = {'source': 'explicitly_shared_charts', 'available': False, 'students': []}
    if member['woodchuck_id'] is not None and member['status'] == 'Member':
        profile_id = session.scalar(select(ClassroomStudentMembership.profile_id).where(
            ClassroomStudentMembership.id == membership_id, ClassroomStudentMembership.class_id == class_id))
        try:
            statistics = reporting.report(session, actor=actor, program_id=program_id,
                class_id=class_id, profile_id=profile_id, selected_week=selected_week)
        except s1.ClassroomDenied:
            pass
        try:
            data = charts.dashboard(session, actor=actor, program_id=program_id, class_id=class_id,
                _profile_id=profile_id)
            shared.update(available=True, students=data['students'])
        except s1.ClassroomDenied:
            pass
    s1._actor(session, actor)
    return {'admin': admin, 'statistics': statistics, 'shared_pcharts': shared}
