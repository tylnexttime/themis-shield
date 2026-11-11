"""One-pass local restoration with bounded syntax tolerance.

Resolved values are emitted literally and never rescanned or unescaped.
The escapes the gate adds (see placeholders.escape_literals) are processed
before token recognition, and no other character is changed: text the model
writes itself, a "::" or a doubled backslash, comes back as written. The
streaming and complete-text paths share the same lexer.
"""

from __future__ import annotations

from collections import Counter
import codecs
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import Mapping, Protocol

from .placeholders import parse_strict_token
from .types import LocalText, ReasonCode, RedactedText
from .vault import Vault


MAXIMUM_HOLDBACK_CHARACTERS = 64
_OPENERS = ("⟦", "\\⟦", "\\u27e6")
_CLOSERS = ("⟧", "\\⟧", "\\u27e7")
_UNICODE_FORMS = ("u27e6", "u27e7")  # what follows the backslash in the two spelled-out delimiters
_BODY = re.compile(
    r"[ \t]{0,4}([PCAIDSQpcaidsq])[ \t]{0,4}:"
    r"[ \t]{0,4}([0-9a-fA-F]{1,16})[ \t]{0,4}"
)
_BARE_START = frozenset("PCAIDSQpcaidsq")
_HEX = frozenset("0123456789abcdefABCDEF")
_SCHEME_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789+.-")
# Schemes that start a URL without "//": the two named in R-01, javascript, and the
# WHATWG special schemes, which browsers also parse as absolute in "https:host" form.
_COLON_SCHEMES = frozenset({
    "data", "mailto", "javascript", "http", "https", "ftp", "file", "ws", "wss",
})
_URL_STOPS = frozenset(")>\"'")
_URL_ATTRIBUTES = frozenset({"src", "href", "action"})
_OPAQUE = "\0"  # how a whole token advances the URL context: never a stop or scheme character


class StreamingRestorationError(PermissionError):
    pass


class ToolArgumentsError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class RestoreReport:
    restored_counts: Mapping[str, int]
    unknown_tokens: tuple[str, ...] = field(repr=False)
    dropped_entities: tuple[str, ...] = field(repr=False)
    tolerant_count: int = 0
    withheld_in_url: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "restored_counts", MappingProxyType(dict(self.restored_counts))
        )

    @property
    def restored_count(self) -> int:
        return sum(self.restored_counts.values())

    @property
    def unknown_count(self) -> int:
        return len(self.unknown_tokens)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_entities)


@dataclass(frozen=True, slots=True)
class RestoreResult:
    text: str = field(repr=False)
    report: RestoreReport


def _snapshot(mapping: Mapping[str, str]) -> dict[str, str]:
    result = dict(mapping)
    for token, value in result.items():
        parse_strict_token(token)
        if not isinstance(value, str):
            raise TypeError("restoration mapping values must be text")
    return result


def _word(character: str) -> bool:
    return bool(character) and (character.isalnum() or character == "_")


@dataclass(frozen=True, slots=True)
class _Atom:
    consumed: int
    output: str = field(repr=False)
    token: str | None = None
    unknown: str | None = field(default=None, repr=False)
    tolerant: bool = False


def _token_atom(raw: str, body: str, mapping: Mapping[str, str]) -> _Atom:
    matched = _BODY.fullmatch(body)
    if matched is None:
        return _Atom(len(raw), raw)
    category, identifier = matched.groups()
    canonical = f"⟦{category.upper()}:{identifier.lower()}⟧"
    if len(identifier) == 6 and canonical in mapping:
        return _Atom(
            len(raw), mapping[canonical], token=canonical,
            tolerant=raw != canonical,
        )
    return _Atom(len(raw), raw, unknown=raw)


def _step(
    text: str,
    previous: str,
    mapping: Mapping[str, str],
    *,
    final: bool,
    reject_incomplete: bool,
) -> _Atom | None:
    """Return a complete lexical atom, or None while a prefix can still grow."""

    character = text[0]
    if character in "⟦⟧":
        if len(text) >= 2 and text[1] == character:
            return _Atom(2, character)
        if len(text) == 1 and not final:
            return None
    elif character == "\\":
        # A run of backslashes is read in pairs, which are the text's own and never join a delimiter. The gate
        # escapes an odd run (one backslash would join one) by adding two, so the last three of an odd run that
        # is followed by a delimiter, by u27e6 or u27e7 (any case) or by the end are one backslash.
        run = 1
        while run < 4 and run < len(text) and text[run] == "\\":
            run += 1
        if run == 4:
            return _Atom(2, "\\\\")
        if run == 2 and (len(text) > 2 or final):
            return _Atom(2, "\\\\")
        if run == 3:
            rest = text[3:].lower()
            if not final and len(rest) < 5 and any(form.startswith(rest) for form in _UNICODE_FORMS):
                return None  # may still become u27e6 or u27e7; an empty rest may be anything
            if not rest or rest[0] in "⟦⟧" or rest[:5] in _UNICODE_FORMS:
                return _Atom(3, "\\")
            return _Atom(3, "\\\\\\")
        if run == 2:
            return None

    lower = text.lower()
    opener = next((value for value in _OPENERS if lower.startswith(value)), None)
    if opener is None and any(value.startswith(lower) for value in _OPENERS):
        if not final:
            return None
    if opener is not None:
        closing: tuple[int, str] | None = None
        for index in range(len(opener), len(text)):
            for closer in _CLOSERS:
                if lower.startswith(closer, index):
                    closing = (index, closer)
                    break
            if closing is not None:
                break
        if closing is None:
            if not final:
                return None
            if reject_incomplete:
                raise StreamingRestorationError("stream ended inside bracketed token")
            return _Atom(len(text), text, unknown=text)
        index, closer = closing
        end = index + len(closer)
        return _token_atom(text[:end], text[len(opener):index], mapping)

    if character in _BARE_START and not _word(previous):
        if len(text) == 1:
            if not final:
                return None
        elif text[1] == ":":
            if len(text) == 2:
                if not final:
                    return None
                if reject_incomplete:
                    raise StreamingRestorationError("stream ended inside bare token")
                return _Atom(2, text, unknown=text)
            if text[2] in _HEX:
                end = 2
                while end < len(text) and text[end] in _HEX:
                    end += 1
                if end == len(text) and not final:
                    return None
                # Embedded runs such as a filename/identifier are not tokens.
                if end < len(text) and _word(text[end]):
                    return _Atom(1, character)
                raw = text[:end]
                return _token_atom(raw, raw, mapping)
            if text[2] == ":":
                if len(text) == 3 and not final:
                    return None
                if len(text) > 3 and text[3] == ":":
                    return _Atom(4, character + ":")  # three colons: the escape for one; two are the model's own
    return _Atom(1, character)


class _UrlContext:
    """Whether the next character of the cloud text sits inside a URL.

    Left context only, advanced with every consumed raw character in order, so
    the answer never depends on how the text was split into chunks. Inside: a
    run from a scheme (``x://`` or a colon scheme) to whitespace, ``)``, ``>``,
    a quote or the end; a markdown ``](...)`` target (balanced parentheses);
    a ``src=``/``href=``/``action=`` attribute value.
    """

    __slots__ = ("_url", "_run", "_letter", "_colon", "_target", "_last",
                 "_name", "_named", "_attribute")

    def __init__(self) -> None:
        self._url = False       # in a bare scheme run
        self._run = ""          # trailing scheme-character run (lower case, last 16)
        self._letter = False    # that run contains a letter
        self._colon = 0         # 1 after "scheme:", 2 after "scheme:/"
        self._target = 0        # markdown target parenthesis depth
        self._last = ""
        self._name = ""         # trailing attribute-name word (lower case, last 8)
        self._named = False     # an attribute name, then only whitespace so far
        self._attribute = ""    # "=" awaiting value, a quote, " " unquoted, "" none

    @property
    def inside(self) -> bool:
        return self._url or self._target > 0 or bool(self._attribute)

    def advance(self, raw: str) -> None:
        for character in raw:
            self._character(character)

    def _character(self, character: str) -> None:
        lower = character.lower() if character.isascii() else character
        space = character.isspace()
        if self._target:
            self._target += {"(": 1, ")": -1}.get(character, 0)
        elif character == "(" and self._last == "]":
            self._target = 1
        self._last = character

        state = self._attribute
        if state == "=":
            if character in "\"'":
                self._attribute = character
            elif not space:
                self._attribute = "" if character == ">" else " "
        elif state in ("\"", "'"):
            if character == state:
                self._attribute = ""
        elif state == " ":
            if space or character == ">":
                self._attribute = ""
        elif character == "=" and (self._named or self._name in _URL_ATTRIBUTES):
            self._attribute = "="
        if lower.isascii() and (lower.isalnum() or lower == "_"):
            self._name, self._named = (self._name + lower)[-8:], False
        else:
            self._named = space and (self._named or self._name in _URL_ATTRIBUTES)
            self._name = ""

        if self._url and (space or character in _URL_STOPS):
            self._url = False
        if character == "/" and self._colon == 2:
            self._url, self._colon = True, 0
        elif character == "/" and self._colon == 1:
            self._colon = 2
        elif character == ":" and self._letter:
            if self._run in _COLON_SCHEMES:
                self._url, self._colon = True, 0
            else:
                self._colon = 1
        else:
            self._colon = 0
        if lower in _SCHEME_CHARACTERS:
            self._run = (self._run + lower)[-16:]
            self._letter = self._letter or lower.isalpha()
        else:
            self._run, self._letter = "", False


class StreamingRestorer:
    """Restore an isolated text stream using an immutable mapping snapshot.

    feed accepts either all str chunks or all UTF-8 bytes chunks. The byte
    decoder additionally retains at most three incomplete UTF-8 bytes.
    The lexical holdback is always at most 64 characters.
    """

    def __init__(
        self,
        mapping: Mapping[str, str],
        *,
        sent_tokens: frozenset[str] | None = None,
    ) -> None:
        self._mapping = _snapshot(mapping)
        self._sent = frozenset(self._mapping) if sent_tokens is None else sent_tokens
        if not self._sent <= self._mapping.keys():
            raise ValueError("sent-token inventory exceeds mapping snapshot")
        self._pending = ""
        self._previous = ""
        self._counts: Counter[str] = Counter()
        self._unknown: list[str] = []
        self._withheld: list[str] = []
        self._url = _UrlContext()
        self._tolerant = 0
        self._closed = False
        self._failed = False
        self._mode: type | None = None
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")

    @property
    def holdback_characters(self) -> int:
        return len(self._pending)

    @property
    def report(self) -> RestoreReport:
        return RestoreReport(
            dict(self._counts), tuple(self._unknown),
            tuple(sorted(self._sent - self._counts.keys() - set(self._withheld))),
            self._tolerant, len(self._withheld),
        )

    def _take(self, raw: str, atom: _Atom) -> str:
        """Account one atom. A resolved token inside a URL is withheld: left as written."""

        self._previous = raw[-1]
        output = atom.output
        if atom.token is not None:
            self._tolerant += int(atom.tolerant)
            if self._url.inside:
                self._withheld.append(atom.token)
                output = raw
            else:
                self._counts[atom.token] += 1
        if atom.unknown is not None:
            self._unknown.append(atom.unknown)
        opaque = atom.token is not None or atom.unknown is not None
        self._url.advance(_OPAQUE if opaque else raw)
        return output

    def _drain(self, *, final: bool, reject_incomplete: bool) -> str:
        output: list[str] = []
        while self._pending:
            atom = _step(
                self._pending, self._previous, self._mapping,
                final=final, reject_incomplete=reject_incomplete,
            )
            if atom is None:
                break
            raw = self._pending[:atom.consumed]
            self._pending = self._pending[atom.consumed:]
            output.append(self._take(raw, atom))
        return "".join(output)

    def _characters(self, text: str) -> str:
        output: list[str] = []
        for character in text:
            if len(self._pending) == MAXIMUM_HOLDBACK_CHARACTERS:
                self._pending = ""
                self._failed = True
                raise StreamingRestorationError("token prefix exceeds holdback limit")
            self._pending += character
            output.append(self._drain(final=False, reject_incomplete=True))
        return "".join(output)

    def feed(self, chunk: str | bytes) -> str:
        if self._closed or self._failed:
            raise StreamingRestorationError("restoration stream is not usable")
        if type(chunk) not in {str, bytes}:
            raise TypeError("stream chunks must be text or UTF-8 bytes")
        if self._mode is not None and self._mode is not type(chunk):
            raise TypeError("stream cannot mix text and byte chunks")
        self._mode = type(chunk)
        try:
            text = self._decoder.decode(chunk, final=False) if isinstance(chunk, bytes) else chunk
            return self._characters(text)
        except UnicodeError:
            self._failed = True
            raise StreamingRestorationError("invalid streamed UTF-8") from None

    def close(self) -> str:
        if self._closed or self._failed:
            raise StreamingRestorationError("restoration stream is not usable")
        self._closed = True
        try:
            suffix = ""
            if self._mode is bytes:
                suffix = self._characters(self._decoder.decode(b"", final=True))
            return suffix + self._drain(final=True, reject_incomplete=True)
        except UnicodeError:
            self._failed = True
            raise StreamingRestorationError("truncated streamed UTF-8") from None

    def finish(self) -> str:
        """Compatibility spelling; callers should use close()."""

        return self.close()


def restore_text(
    text: str,
    mapping: Mapping[str, str],
    *,
    sent_tokens: frozenset[str] | None = None,
) -> RestoreResult:
    if not isinstance(text, str):
        raise TypeError("restoration input must be text")
    stream = StreamingRestorer(mapping, sent_tokens=sent_tokens)
    # Complete text can retain/report malformed tokens rather than emit them
    # incrementally. No regex replacement ever examines restored originals.
    output: list[str] = []
    index = 0
    while index < len(text):
        atom = _step(
            text[index:], stream._previous, stream._mapping,
            final=True, reject_incomplete=False,
        )
        assert atom is not None
        raw = text[index:index + atom.consumed]
        output.append(stream._take(raw, atom))
        index += atom.consumed
    return RestoreResult("".join(output), stream.report)


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON number")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def restore_tool_arguments(json_text: str, mapping: Mapping[str, str]) -> str:
    """Restore complete JSON only; unknown tokens or key collisions block.

    Syntax recovery is not execution authority. M4 must additionally validate
    the tool schema, paths, fixed enums, destination and local action. Each
    string is restored on its own, so a token inside a URL there stays a
    placeholder, as in text.
    """

    snapshot = _snapshot(mapping)

    def string(value: str) -> str:
        result = restore_text(value, snapshot)
        if result.report.unknown_tokens:
            raise ToolArgumentsError("unresolved tool argument token")
        return result.text

    def walk(value: object) -> object:
        if isinstance(value, str):
            return string(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                restored_key = string(key)
                if restored_key in result:
                    raise ToolArgumentsError("restored JSON keys collide")
                result[restored_key] = walk(item)
            return result
        return value

    try:
        parsed = json.loads(
            json_text, object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        return json.dumps(
            walk(parsed), ensure_ascii=False, allow_nan=False,
            separators=(",", ":"),
        )
    except ToolArgumentsError:
        raise
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ToolArgumentsError("invalid or unsupported tool argument JSON") from None


class ToolCallAccumulator:
    """Buffer argument deltas by call index. Completion is explicit, not inferred."""

    def __init__(
        self,
        mapping: Mapping[str, str],
        *,
        maximum_argument_bytes: int = 1024 * 1024,
        maximum_total_bytes: int = 8 * 1024 * 1024,
    ) -> None:
        if maximum_argument_bytes <= 0 or maximum_total_bytes <= 0:
            raise ValueError("positive tool argument bounds required")
        self._mapping = _snapshot(mapping)
        self._per_call = maximum_argument_bytes
        self._maximum_total = maximum_total_bytes
        self._chunks: dict[int, list[str]] = {}
        self._sizes: dict[int, int] = {}
        self._finished: set[int] = set()

    def feed(self, index: int, delta: str) -> None:
        if type(index) is not int or index < 0 or not isinstance(delta, str):
            raise ToolArgumentsError("invalid tool argument delta")
        if index in self._finished:
            raise ToolArgumentsError("tool call index is already completed")
        try:
            size = len(delta.encode("utf-8"))
        except UnicodeError:
            raise ToolArgumentsError("invalid tool argument text") from None
        if (
            self._sizes.get(index, 0) + size > self._per_call
            or sum(self._sizes.values()) + size > self._maximum_total
        ):
            self.cancel(index)
            raise ToolArgumentsError("tool argument buffer bound exceeded")
        self._chunks.setdefault(index, []).append(delta)
        self._sizes[index] = self._sizes.get(index, 0) + size

    def complete(self, index: int) -> str:
        if index in self._finished or index not in self._chunks:
            raise ToolArgumentsError("tool call is absent or already completed")
        raw = "".join(self._chunks.pop(index))
        self._sizes.pop(index)
        self._finished.add(index)
        return restore_tool_arguments(raw, self._mapping)

    def cancel(self, index: int) -> None:
        self._chunks.pop(index, None)
        self._sizes.pop(index, None)
        self._finished.add(index)


# Import-compatible scope contracts for adapters that remain deferred.
@dataclass(frozen=True, slots=True)
class TokenInventory:
    token_counts: tuple[tuple[str, int], ...]
    entity_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RestorationScope:
    conversation_id: str
    request_id: str
    authorised_tokens: frozenset[str]
    expected_inventory: TokenInventory
    for_tool_execution: bool = False


@dataclass(frozen=True, slots=True)
class RestorationReport:
    restored_count: int
    unknown_count: int
    dropped_count: int
    invented_count: int
    mangled_count: int
    ambiguous_count: int
    reasons: tuple[ReasonCode, ...]
    safe_for_tool_execution: bool


@dataclass(frozen=True, slots=True)
class RestorationResult:
    local: LocalText
    canonical_redacted: RedactedText
    report: RestorationReport


class TolerantTokenMatcher(Protocol):
    """Reserved adapter extension point; the core lexer is not replaceable."""

    def match(self, text: str, scope: RestorationScope) -> tuple: ...


@dataclass(frozen=True, slots=True)
class Restorer:
    vault: Vault
    tolerant_matcher: TolerantTokenMatcher | None = None

    def restore(
        self, response: RedactedText, scope: RestorationScope
    ) -> RestorationResult:
        available = self.vault.mapping(scope.conversation_id)
        mapping = {
            token: value for token, value in available.items()
            if token in scope.authorised_tokens
        }
        sent = frozenset(
            token for token, count in scope.expected_inventory.token_counts
            if count > 0 and token in mapping
        )
        result = restore_text(response.text, mapping, sent_tokens=sent)
        report = result.report
        missing_map = scope.authorised_tokens - mapping.keys()
        safe = not report.unknown_tokens and not missing_map
        reasons = []
        if report.unknown_tokens or missing_map:
            reasons.append(ReasonCode.UNKNOWN_TOKEN)
        if report.dropped_entities:
            reasons.append(ReasonCode.DROPPED_TOKEN)
        if report.tolerant_count:
            reasons.append(ReasonCode.MANGLED_TOKEN)
        legacy = RestorationReport(
            report.restored_count, report.unknown_count + len(missing_map),
            report.dropped_count, report.unknown_count, report.tolerant_count,
            0, tuple(reasons), safe,
        )
        return RestorationResult(LocalText(result.text), response, legacy)

    def compare_inventory(
        self, output: RedactedText, scope: RestorationScope
    ) -> RestorationReport:
        return self.restore(output, scope).report
