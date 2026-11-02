"""Graph templates: typical multi-step changes as ready-made task graphs (steps with dependencies and a reason each)."""
from __future__ import annotations

# key -> (label, steps); a step is (title, description, type, acceptance, indexes of the steps it needs, why)
TEMPLATES: dict[str, tuple[str, list[tuple]]] = {
    "api-endpoint": ("API endpoint", [
        ("{n}: data model and storage", "Define the data structures and storage for {n}.", "feature", "Model and storage exist and are tested", [], "Everything else builds on the data"),
        ("{n}: service logic", "Implement the logic for {n} with validation and error cases.", "feature", "Logic works for valid and invalid input", [0], "The endpoint stays thin when the logic is separate"),
        ("{n}: endpoint", "Expose {n} as an endpoint with a stable request/response shape.", "feature", "Endpoint answers with the documented shape", [1], "This is what clients call"),
        ("{n}: tests and docs", "Add endpoint tests including errors and document the endpoint.", "test", "Tests and docs exist; build green", [2], "A guard against regressions"),
    ]),
    "ui-screen": ("UI screen", [
        ("{n}: state and data access", "Create the state handling and data access for the {n} screen.", "feature", "State works without the view", [], "The view needs data first"),
        ("{n}: layout", "Build the {n} layout with the needed components.", "feature", "The screen renders with data", [0], "Structure before behaviour"),
        ("{n}: interaction and errors", "Wire up interactions, loading and error states for {n}.", "feature", "All states are reachable and shown", [1], "Users meet errors and empty states"),
        ("{n}: UI tests", "Add UI or component tests for {n}.", "test", "Tests cover the main flows", [2], "A guard against regressions"),
    ]),
    "migration": ("Migration", [
        ("{n}: analyse and plan", "List what {n} touches and write the safe order of changes in the notes.", "refactor", "Affected places are listed", [], "A migration without a map breaks things"),
        ("{n}: compatibility layer", "Add a layer that supports old and new at the same time for {n}.", "refactor", "Old and new work side by side", [0], "Allows small safe steps"),
        ("{n}: switch over", "Move all callers to the new way for {n}.", "refactor", "No caller uses the old way", [1], "The actual change"),
        ("{n}: remove the old way", "Delete the old code of {n} and update docs and tests.", "refactor", "Old code is gone; build green", [2], "Leftovers are future bugs"),
    ]),
    "refactor-module": ("Refactor a module", [
        ("{n}: pin behaviour with tests", "Add tests that pin the current behaviour of {n}.", "test", "Tests describe current behaviour", [], "A refactor needs a safety net"),
        ("{n}: simplify and split", "Split long functions and remove duplication in {n} without behaviour change.", "refactor", "Same behaviour, simpler code", [0], "Smaller units are cheaper to change"),
        ("{n}: clean up names and docs", "Improve names and docs of {n}.", "refactor", "Names and docs match the code", [1], "Readable code stays maintainable"),
    ]),
}


def labels() -> list[dict]:
    return [{"id": k, "label": v[0], "steps": len(v[1])} for k, v in TEMPLATES.items()]


def instantiate(backlog, key: str, name: str) -> list[dict]:
    """Adds the template as a graph to `backlog` (steps depend on each other as declared). Returns the new items."""
    if key not in TEMPLATES:
        raise ValueError(f"unknown template: {key}")
    name = " ".join(name.split())[:60]
    if not name:
        raise ValueError("a name is required")
    out: list[dict] = []
    for title, desc, typ, accept, needs, why in TEMPLATES[key][1]:
        item = backlog.add(title.format(n=name), desc.format(n=name), typ, 5.0, [accept, "Build and tests green"], f"Template {key}", "user",
                           force=True, depends_on=[out[j]["id"] for j in needs if j < len(out)], why=why)
        if item:
            out.append(item)
    return out
