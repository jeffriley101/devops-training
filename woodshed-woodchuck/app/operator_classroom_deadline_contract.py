"""Frozen c28 extension of the c26 invitation contract, independent of ORM."""
from copy import deepcopy

from .operator_classroom_invitation_contract import C26

C28 = deepcopy(C26)
C28["classroom_student_invitations"]["columns"]["deadline_accepted_at"] = ("DateTime", None, True)
