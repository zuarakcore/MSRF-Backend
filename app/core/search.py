"""Safe substring patterns for LIKE / ILIKE."""


def contains(term: str) -> str:
    """'%term%' with the user's own % and _ (and the escape char) matched literally.

    PostgreSQL's default LIKE escape character is the backslash, so no ESCAPE clause is needed.
    Without this, searching for "%" would match every row and "_" any single character.
    """
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
