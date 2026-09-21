"""Shared neutral age declarations, not independent verification of age."""
REGISTRATION_AGES = ('13to17', 'adult')
DECLARED_AGES = ('under13', *REGISTRATION_AGES)


def declared_age(value):
    if value not in DECLARED_AGES:
        raise ValueError('Unknown age cannot activate.')
    return value


def registration_age(value):
    if value not in REGISTRATION_AGES:
        raise ValueError('Account registration is unavailable for this age selection. Guest tools remain available.')
    return value
