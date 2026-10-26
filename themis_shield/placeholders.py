"""Conversation-scoped opaque tokens and conservative, local entity linking.

Literal escaping doubles delimiters, backslashes and colons. Doubling colons
also shields literal bare tokens from tolerant restoration. Inserted tokens
are never escaped. Restoration unescapes literal segments, not resolved values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import secrets
from threading import RLock
from types import MappingProxyType
from typing import Callable, Mapping, Protocol
from uuid import uuid4

from .types import Label


OPEN_DELIMITER = "⟦"
CLOSE_DELIMITER = "⟧"
STRICT_TOKEN_PATTERN = r"⟦[PCAIDSQ]:[0-9a-f]{6}⟧"
TOKEN_IDENTIFIER_CAPACITY_PER_CLASS = 16 ** 6
_STRICT = re.compile(STRICT_TOKEN_PATTERN)
_HEX = re.compile(r"[0-9a-f]{6}")


class AllocationError(RuntimeError):
    pass


class SecretFindingError(PermissionError):
    pass


class TokenClass(StrEnum):
    PERSON = "P"
    CONTACT = "C"
    ADDRESS = "A"
    IDENTIFIER = "I"
    DATE = "D"
    STATEMENT = "S"
    QUASI_IDENTIFIER = "Q"


SENSITIVE_LABELS = frozenset({
    Label.HEALTH, Label.DISABILITY, Label.INDIGENOUS, Label.SEXUALITY,
    Label.RELIGION, Label.CRIMINAL, Label.FAMILY_VIOLENCE,
    Label.ETHNICITY, Label.UNION,
})


def token_class_for(label: Label | str) -> TokenClass:
    label = Label(label)
    if label == Label.SECRET:
        raise SecretFindingError("SECRET findings block substitution")
    if label == Label.PERSON:
        return TokenClass.PERSON
    if label in {Label.EMAIL, Label.PHONE, Label.URL_PERSONAL}:
        return TokenClass.CONTACT
    if label == Label.ADDRESS:
        return TokenClass.ADDRESS
    if label in {Label.DATE_OF_BIRTH, Label.OTHER_DATE}:
        return TokenClass.DATE
    if label == Label.QUASI_ID:
        return TokenClass.QUASI_IDENTIFIER
    if label in SENSITIVE_LABELS:
        return TokenClass.STATEMENT
    return TokenClass.IDENTIFIER


@dataclass(frozen=True, slots=True)
class PlaceholderToken:
    token_class: TokenClass
    identifier: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_class", TokenClass(self.token_class))
        if not isinstance(self.identifier, str) or not _HEX.fullmatch(self.identifier):
            raise ValueError("invalid placeholder identifier")

    def render(self) -> str:
        return f"⟦{self.token_class.value}:{self.identifier}⟧"


def parse_strict_token(text: str) -> PlaceholderToken:
    if not isinstance(text, str) or not _STRICT.fullmatch(text):
        raise ValueError("invalid strict placeholder")
    return PlaceholderToken(TokenClass(text[1]), text[3:9])


def escape_literals(text: str) -> str:
    return "".join(character * 2 if character in "⟦⟧:\\" else character
                   for character in text)


def unescape_literals(text: str) -> str:
    """Undo pairs once, without interpreting token syntax."""

    output: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if (
            character in "⟦⟧:\\"
            and index + 1 < len(text)
            and text[index + 1] == character
        ):
            index += 1
        output.append(character)
        index += 1
    return "".join(output)


@dataclass(frozen=True, slots=True)
class EntityMap:
    """Value-free local reporting snapshot; entity IDs are not capabilities."""

    entity_by_token: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "entity_by_token", MappingProxyType(dict(self.entity_by_token))
        )

    def entity_id(self, token: str) -> str:
        return self.entity_by_token[token]

    @property
    def entity_count(self) -> int:
        return len(set(self.entity_by_token.values()))


def confusable(first: str, second: str) -> bool:
    """True when two identifiers are equal, differ in one hex digit, or differ by one adjacent swap.

    The allocator never issues an identifier confusable with one already issued in the conversation (whatever
    its class letter), so a cloud answer that damages one character of a placeholder either restores exactly
    that placeholder or matches nothing: it can never resolve to another person's value. Restoration itself is
    exact (no fuzzy matching), and an unmatched placeholder is left visible.
    """
    if len(first) != len(second):
        return False
    differences = [index for index, (a, b) in enumerate(zip(first, second)) if a != b]
    if len(differences) <= 1:
        return True
    return (
        len(differences) == 2 and differences[1] == differences[0] + 1
        and first[differences[0]] == second[differences[1]]
        and first[differences[1]] == second[differences[0]]
    )


class MappingVault(Protocol):
    allocation_lock: RLock

    def put(self, conversation_id: str, token: str, value: str) -> None: ...
    def mapping(self, conversation_id: str) -> dict[str, str]: ...


_HONORIFICS = {"ms", "mr", "mrs", "miss", "dr", "prof"}


def _name_parts(value: str) -> tuple[str, ...]:
    return tuple(part.casefold().rstrip(".") for part in value.split())


def _full_name(value: str) -> tuple[str, ...] | None:
    parts = _name_parts(value)
    if parts and parts[0] in _HONORIFICS:
        parts = parts[1:]
    if (
        len(parts) >= 2
        and len(parts[0]) > 1
        and all(part.isalpha() for part in parts)
    ):
        return parts
    return None


class EntityLinker:
    """Resolve only unique exact full-name, surname and initial candidates.

    Recompute links when new full names appear: a formerly unique bare name
    becomes independent if it becomes ambiguous. Tokens never change.
    """

    def link(
        self,
        values_by_token: Mapping[str, str],
        independent_ids: Mapping[str, str],
    ) -> EntityMap:
        full_entities: dict[tuple[str, ...], str] = {}
        for token, value in values_by_token.items():
            if parse_strict_token(token).token_class == TokenClass.PERSON:
                full = _full_name(value)
                if full is not None:
                    full_entities.setdefault(full, independent_ids[token])

        linked: dict[str, str] = {}
        for token, value in values_by_token.items():
            entity_id = independent_ids[token]
            if parse_strict_token(token).token_class != TokenClass.PERSON:
                linked[token] = entity_id
                continue
            full = _full_name(value)
            if full is not None:
                linked[token] = full_entities[full]
                continue
            parts = _name_parts(value)
            candidates: set[str] = set()
            if len(parts) == 1:
                candidates = {
                    identifier for name, identifier in full_entities.items()
                    if parts[0] in {name[0], name[-1]}
                }
            elif len(parts) == 2 and parts[0] in _HONORIFICS:
                candidates = {
                    identifier for name, identifier in full_entities.items()
                    if parts[1] == name[-1]
                }
            elif len(parts) == 2 and len(parts[0]) == 1:
                candidates = {
                    identifier for name, identifier in full_entities.items()
                    if parts[0] == name[0][0] and parts[1] == name[-1]
                }
            if len(candidates) == 1:
                entity_id = next(iter(candidates))
            linked[token] = entity_id
        return EntityMap(linked)


class TokenAllocator:
    """One authoritative allocator per active conversation.

    Calls are thread-safe. Allocators sharing the SAME vault object share its
    allocation lock. M4 must serialize a conversation across processes.
    Values are held locally and never exposed in this object's representation.
    """

    def __init__(
        self,
        conversation_id: str,
        vault: MappingVault | None = None,
        *,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(conversation_id, str) or not conversation_id:
            raise ValueError("conversation identifier required")
        if vault is None:
            from .vault import InMemoryVault
            vault = InMemoryVault()
        self.conversation_id = conversation_id
        self.vault = vault
        self._lock = vault.allocation_lock
        self._id_factory = id_factory or (lambda: secrets.token_hex(3))
        self._values: dict[str, str] = {}
        self._independent_ids: dict[str, str] = {}
        self._linker = EntityLinker()
        self._refresh()

    def _refresh(self) -> None:
        self._values = self.vault.mapping(self.conversation_id)
        for token in self._values:
            parse_strict_token(token)
            self._independent_ids.setdefault(token, uuid4().hex)

    def allocate(self, value: str, category: TokenClass | str) -> str:
        if not isinstance(value, str) or not value:
            raise AllocationError("non-empty original value required")
        category = TokenClass(category)
        with self._lock:
            self._refresh()
            for token, original in self._values.items():
                if original == value:
                    if parse_strict_token(token).token_class != category:
                        raise AllocationError("exact value has conflicting categories")
                    return token
            identifiers = {parse_strict_token(token).identifier
                           for token in self._values}
            if len(identifiers) >= TOKEN_IDENTIFIER_CAPACITY_PER_CLASS:
                raise AllocationError("placeholder identifier space exhausted")
            for _ in range(1024):
                identifier = self._id_factory()
                if not isinstance(identifier, str) or not _HEX.fullmatch(identifier):
                    raise AllocationError("invalid identifier generator output")
                if not any(confusable(identifier, other) for other in identifiers):
                    token = PlaceholderToken(category, identifier).render()
                    self.vault.put(self.conversation_id, token, value)
                    self._values[token] = value
                    self._independent_ids[token] = uuid4().hex
                    return token
            raise AllocationError("placeholder collision retry limit exceeded")

    @property
    def entity_map(self) -> EntityMap:
        with self._lock:
            self._refresh()
            return self._linker.link(self._values, self._independent_ids)

    def mapping(self) -> dict[str, str]:
        """Local restoration snapshot. Never log or package this result."""

        with self._lock:
            self._refresh()
            return dict(self._values)
