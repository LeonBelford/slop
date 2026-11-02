"""Project templates: predefined layer plans that are added to the idea so the blueprint follows a proven shape."""
from __future__ import annotations

TEMPLATES = {
    "cli": "Layer 0: walking skeleton (argument parsing -> core logic -> storage -> printed output) with one test. "
           "Layer 1: all subcommands on the core. Layer 2: config file, error messages with exit codes, --help text. "
           "Layer 3: packaging, shell completion, README with usage examples.",
    "web-api": "Layer 0: walking skeleton (one endpoint -> logic -> storage -> JSON response) with one test and the app factory. "
               "Layer 1: data models, CRUD endpoints, validation. Layer 2: auth, error format, pagination, config. "
               "Layer 3: OpenAPI docs, health check, logging, Dockerfile.",
    "library": "Layer 0: walking skeleton (public function -> logic -> result) with a contract test of the public API. "
               "Layer 1: the full public API with typed signatures. Layer 2: edge cases, error types, property tests. "
               "Layer 3: docs with examples, packaging metadata, changelog.",
}


def apply(idea: str, name: str) -> str:
    """Returns the idea with the template's layer plan appended; unknown names raise ValueError, empty means no template."""
    if not name or name == "none":
        return idea
    if name not in TEMPLATES:
        raise ValueError(f"Unknown template '{name}' (use: {', '.join(TEMPLATES)})")
    return f"{idea}\n\nProject template '{name}': plan the layers like this. {TEMPLATES[name]}"
