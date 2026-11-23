"""When a proposal runs at once, without a button (decision 1.11-b).

Only if every condition holds:

0. the run carries a message of Pedro (`origin`): a run without one (the plan, the follow-up
   after a tap, task 1.14) never runs anything at once, whatever the agent marked;
1. the agent marked `requested_by_pedro` (Pedro asked for it in this message);
2. this run called no tool that contaminates (`read_calendar`, `read_tasks`);
3. the proposal has **one** item, and it is a `create` or a `move`;
4. a `move` is not of a recurring task (the snapshot says so).

A deletion never runs at once (H1-1): it waits for its button or a «sí». Why one item: with the
session of the day (1.10-a), content read in an earlier run is still in the context and
condition 2 does not see it; the cap is **one** action, visible, that a tap undoes and that is
never a deletion.
"""

from jimic.agent.tools import RunState


def runs_now(run: RunState) -> bool:
    if run.origin is None or not run.items or not run.requested_by_pedro or run.contaminated:
        return False
    if len(run.items) != 1:
        return False
    (entry,) = run.items
    if entry["action"] == "create":
        return True
    if entry["action"] == "move":
        return not entry["snapshot"].get("is_recurring", True)
    return False
