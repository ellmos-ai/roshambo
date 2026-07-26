def parse_resource(name: str) -> tuple[str, str, str]:
    """Split a Roshambo resource name into kind, scope, and path."""
    kind, scope, path = name.split(":", 2)
    return kind, scope, path
