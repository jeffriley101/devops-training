"""Frozen c30 extension; legacy authorization evidence is never upgraded."""
from copy import deepcopy

from .operator_classroom_progress_contract import C29

C30 = deepcopy(C29)
C30["classroom_progress_authorizations"]["columns"]["source_revision"] = ("String", 64, True)
