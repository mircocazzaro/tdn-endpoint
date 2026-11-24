"""Whether a plan may run.

Kept apart from the rewriting on purpose: the rewriting only finds
equivalent plans over the catalog, this module decides which of them are
admissible. Today the decision is the disclosure level of the templates
used (the most restrictive one), checked by every endpoint against its own
setting; Central admits every plan. A new disclosure formalism replaces
these two functions, not the rewriting.
"""


def plan_level(plan):
    """Level of a plan: the most restrictive level among its templates."""
    return max(c.template.level for c in plan.calls)


def admissible(plan, user=None):
    """``(ok, reason)``. Endpoints enforce their own level on each template."""
    return True, ""
