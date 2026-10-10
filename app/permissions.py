"""Shared role predicates; permission decisions never trust client capability flags."""


def is_admin(user):
    return bool(user and user.get('role') in {'admin', 'super_admin'})


def is_super_admin(user):
    return bool(user and user.get('role') == 'super_admin')


def can_manage(actor, target):
    return bool(actor and target and actor.get('enabled', True) and not actor.get('deleted_at')
                and (is_super_admin(actor) or (is_admin(actor) and target.get('role') == 'user')))
