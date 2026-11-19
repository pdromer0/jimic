"""Round-trip YAML for the personal files: read, change a value and write back.

`ruamel.yaml` in round-trip mode (YAML 1.2, so `7:00` stays text) keeps comments, key order
and quotes. Writing normalises only the spacing inside flow mappings (`{ a: 1 }` becomes
`{a: 1}`); the templates are already written that way. Collections are changed in place:
assigning a new list turns `[mon, tue]` into a block list.
"""

import io
from collections.abc import Sequence
from datetime import time

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString


class YamlSyntaxError(Exception):
    """The text is not YAML that can be loaded.

    It keeps only the exception type and a line near the problem: every loading error of
    `ruamel.yaml` quotes the text in its message, so that message is never kept.
    """

    def __init__(self, kind: str, line: int | None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.line = line


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def parse_yaml(text: str) -> object:
    """Load `text` keeping its comments; `None` if it is empty or only has comments."""
    try:
        return _yaml().load(text)
    except Exception as exc:
        # Any exception, not only `YAMLError`: `!!int x` raises a plain `ValueError`. The mark,
        # when there is one, can point at the line after the problem.
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        raise YamlSyntaxError(type(exc).__name__, line) from None


def dump_yaml(doc: object) -> str:
    stream = io.StringIO()
    _yaml().dump(doc, stream)
    return stream.getvalue()


def quoted_hour(value: time) -> DoubleQuotedScalarString:
    """The hour as JimiC writes it: `"HH:MM"`, quoted even where the key had no quotes."""
    return DoubleQuotedScalarString(value.strftime("%H:%M"))


def line_of(doc: object, loc: Sequence[str | int]) -> int | None:
    """Return the 1-based line of `loc` in `doc`, or the nearest line above it.

    Walks down `loc` through the mappings and sequences of `doc`, skipping the segments that
    are not there (such as the tag of a union). A missing key gives the line of its parent.
    """
    if not isinstance(doc, CommentedMap | CommentedSeq):
        return None
    node: object = doc
    line: int = doc.lc.line
    for segment in loc:
        if isinstance(node, CommentedMap) and isinstance(segment, str) and segment in node:
            line = node.lc.key(segment)[0]
            node = node[segment]
        elif (
            isinstance(node, CommentedSeq) and isinstance(segment, int) and 0 <= segment < len(node)
        ):
            line = node.lc.item(segment)[0]
            node = node[segment]
    return line + 1
