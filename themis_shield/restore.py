"""One-pass local restoration with bounded syntax tolerance.

Resolved values are emitted literally and never rescanned or unescaped.
Doubled source escape atoms are processed before token recognition. The
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
_BODY = re.compile(
    r"[ \t]{0,4}([PCAIDSQpcaidsq])[ \t]{0,4}:"
    r"[ \t]{0,4}([0-9a-fA-F]{1,16})[ \t]{0,4}"
)
_BARE_START = frozenset("PCAIDSQpcaidsq")
_HEX = frozenset("0123456789abcdefABCDEF")


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
    if character in "⟦⟧:\\":
        if len(text) >= 2 and text[1] == character:
            return _Atom(2, character)
        if len(text) == 1 and not final:
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
    return _Atom(1, character)


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
            tuple(sorted(self._sent - self._counts.keys())), self._tolerant,
        )

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
            self._previous = raw[-1]
            output.append(atom.output)
            if atom.token is not None:
                self._counts[atom.token] += 1
                self._tolerant += int(atom.tolerant)
            if atom.unknown is not None:
                self._unknown.append(atom.unknown)
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
        stream._previous = raw[-1]
        output.append(atom.output)
        if atom.token is not None:
            stream._counts[atom.token] += 1
            stream._tolerant += int(atom.tolerant)
        if atom.unknown is not None:
            stream._unknown.append(atom.unknown)
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
    the tool schema, paths, fixed enums, destination and local action.
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
