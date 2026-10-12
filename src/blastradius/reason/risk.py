"""The risk-level rule (REPORT_SPEC section 1; D29). A pure function of a Report, so the pipeline and the verifier
apply the same rule and a report whose level contradicts its items is rejected.

  UNKNOWN  nothing could be mapped: no item in any tier, and at least one thing could not be determined (a
           caveat about the PR itself, such as a stale base, does not count: the changes it covers were read)
  LOW      nothing needs a look: no item and nothing outstanding (only test, generated, docs, CI or build-file
           changes), or every signal is "possible" (no item of any tier at "likely" or above)
  MEDIUM   an item is "likely" or above (a direct item that is not merely "possible", an indirect item at
           "likely"), or the change is global or touches stored data
  HIGH     a direct item is "confirmed" AND the PR touches at least one flow whose requirement is at risk, or
           changes something global or stored, or removes text that screens still show (the severe case of the
           locale diff: removed keys are high severity)

Requirements "at risk" are those with status `at_risk` or `loses_only_realization`; `cannot_verify` and
`may_close_gap` never raise the level on their own. A direct item that carries `REMOVED_TEXT_VIA` is the
removed-text case. The example headline in REPORT_SPEC says MEDIUM for #6645; the rule as registered gives HIGH
whenever a confirmed direct item sits on a flow with a requirement at risk.
"""

from __future__ import annotations

from ..models import Band
from ..report.model import Report, RiskLevel

REMOVED_TEXT_VIA = "removed text"
AT_RISK = frozenset({"at_risk", "loses_only_realization"})
CAVEATS = frozenset({"STALE_BASE"})        # about how far to trust the diff, not about what could not be mapped


def derive_risk(report: Report) -> RiskLevel:
    items = [*report.direct, *report.indirect, *report.global_changes, *report.stored_data]
    if not items:
        return "UNKNOWN" if any(u.code not in CAVEATS for u in report.undetermined) else "LOW"
    at_risk = {r.requirement_id for r in report.requirements if r.status in AT_RISK}
    confirmed_direct = [i for i in report.direct if i.band is Band.CONFIRMED]
    flow_at_risk = any(i.flows and at_risk & set(i.requirement_ids) for i in [*report.direct, *report.indirect])
    severe = any(i.via == REMOVED_TEXT_VIA for i in confirmed_direct)
    if confirmed_direct and (flow_at_risk or report.global_changes or report.stored_data or severe):
        return "HIGH"
    strong = any(i.band in (Band.CONFIRMED, Band.LIKELY) for i in [*report.direct, *report.indirect])
    if strong or report.global_changes or report.stored_data:
        return "MEDIUM"
    return "LOW"
