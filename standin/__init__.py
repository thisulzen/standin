__version__ = "1.0.0"


class Problem(Exception):
    """Something the user has to fix. The message says what went wrong and what
    to do about it, and never quotes a cell, because the cell may be the very
    thing that mustn't end up in a terminal or a CI log."""
