"""
Taken from meta-hq (https://github.com/krishnanlab/meta-hq)

Author: Parker Hicks
Date: 2026-09-16
"""

import gzip
import re
import warnings
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import polars as pl


class XRefLevel(StrEnum):
    """Source levels by which ontology cross-references can be filtered."""

    EQUIVALENT_TO = "equivalentTo"
    RELATED_TO = "relatedTo"
    OTHER_HIERARCHY = "otherHierarchy"
    REDUNDANT = "Redundant"
    SHARED_UMLS_XREF = "shared-umls-xref"


@dataclass(slots=True)
class Synonym:
    """Ontology term synonyms."""

    name: str
    scope: str
    sources: list[str]


@dataclass(slots=True)
class XRef:
    """Ontology term cross-references"""

    ref_id: str
    sources: list[str]


@dataclass(slots=True)
class GraphConnections:
    """Ontology is_a / part_of relationships."""

    is_a: list[str]
    part_of: list[str]


@dataclass(slots=True)
class OboEntry:
    """OBO ontology term entry."""

    id: str
    name: str
    definition: str | None = None
    def_sources: list[str] = field(default_factory=list)
    synonyms: list[Synonym] = field(default_factory=list)
    xrefs: list[XRef] = field(default_factory=list)
    is_a: list[str] = field(default_factory=list)
    part_of: list[str] = field(default_factory=list)
    property_values: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_text(cls, text: str) -> "OboEntry":
        id_ = name = definition = None
        def_sources = []
        synonyms = []
        xrefs = []
        is_a = []
        part_of = []
        property_values = {}

        for line in text.strip().splitlines():
            line = line.strip()

            # Skip header and blank lines
            if not line or line == "[Term]":
                continue

            key, _, value = line.partition(": ")

            if key == "id":
                id_ = value

            elif key == "name":
                name = value

            elif key == "def":
                # def: "Some text." [SRC1, SRC2]
                m = re.match(r'"(.+?)"\s*\[([^\]]*)\]', value)
                if m:
                    definition = m.group(1)
                    def_sources = _parse_source_list(m.group(2))

            elif key == "synonym":
                # synonym: "name" SCOPE [SRC1, SRC2]
                m = re.match(r'"(.+?)"\s+(\w+)\s*\[([^\]]*)\]', value)
                if m:
                    synonyms.append(
                        Synonym(
                            name=m.group(1),
                            scope=m.group(2),
                            sources=_parse_source_list(m.group(3)),
                        )
                    )

            elif key == "xref":
                # xref: REF_ID {source="X", source="Y"}  (annotations optional)
                m = re.match(r"(\S+)(?:\s*\{([^}]*)\})?", value)
                if m:
                    ref_id = m.group(1)
                    annotations: list[str] = []
                    if m.group(2):
                        for pair in m.group(2).split(","):
                            _, _, v = pair.strip().partition("=")
                            annotations.append(v.strip().strip('"'))
                    xrefs.append(XRef(ref_id=ref_id, sources=annotations))

            elif key == "property_value":
                # property_value: key value
                pkey, _, pvalue = value.partition(" ")
                property_values.setdefault(pkey, []).append(pvalue)

            elif key == "is_a":
                m = re.match(r"(\S+)(?:\s*\{([^}]*)\})?", value)
                if m and not _is_gci_axiom(m.group(2)):
                    is_a.append(m.group(1))

            elif key == "relationship":
                # relationship: part_of ID ! comment  (other relation types, e.g.
                # develops_from or mutually_spatially_disjoint_with, are not
                # hierarchical and are recorded on both terms they relate, so
                # capturing them here would introduce cycles into the graph)
                reltype, _, target = value.partition(" ")
                if reltype == "part_of":
                    m = re.match(r"(\S+)(?:\s*\{([^}]*)\})?", target)
                    if m and not _is_gci_axiom(m.group(2)):
                        part_of.append(m.group(1))

        if not id_ or not name:
            warnings.warn("Initializing OboEntry without a name or id.", RuntimeWarning)
            return cls(
                id="",
                name="",
                definition=definition,
                def_sources=def_sources,
                synonyms=synonyms,
                xrefs=xrefs,
                is_a=is_a,
                part_of=part_of,
                property_values=property_values,
            )

        return cls(
            id=id_,
            name=name,
            definition=definition,
            def_sources=def_sources,
            synonyms=synonyms,
            xrefs=xrefs,
            is_a=is_a,
            part_of=part_of,
            property_values=property_values,
        )

    @property
    def id_prefix(self) -> str:
        """Return the ontology prefix of a full ontology ID."""
        return self.id.split(":")[0]


def _is_gci_axiom(annotation: str | None) -> bool:
    """Check whether an is_a/relationship trailing annotation marks a GCI axiom.

    A `{gci_relation=..., gci_filler=...}` annotation expresses a conditional class
    axiom (e.g. "X, when part_of Y, is_a Z"), not an unconditional hierarchy edge,
    so it must be excluded from is_a/part_of or Graph.construct() forms a cycle.
    """
    return annotation is not None and "gci_relation" in annotation


def _parse_source_list(raw: str) -> list[str]:
    """Parse a comma-separated source list, returning [] for empty strings."""
    return [s.strip() for s in raw.split(",") if s.strip()]


class OntologyReader(StrEnum):
    """Supported ontology file readers."""

    OBO = "obo"


class IdMapStruct(StrEnum):
    """Supported output data structures for Ontology.id_map."""

    POLARS = "polars"
    DICT = "dict"


XREF_LEVELS: tuple[XRefLevel, ...] = tuple(XRefLevel)
DEFAULT_XREF_LEVELS: tuple[XRefLevel, ...] = (
    XRefLevel.EQUIVALENT_TO,
    XRefLevel.RELATED_TO,
    XRefLevel.SHARED_UMLS_XREF,
)


class XRefMappings:
    """Structured mappings between ontology terms.

    Attributes:
        anchor (str):
            The prefix of the main ontology for which xrefs were collected.
        to (str):
            The prefix of the ontology mapped from the anchor.
        mapping (dict[str, list[str]]):
            A mapping between anchor terms and their cross references.
    """

    def __init__(self, anchor, to, mapping):
        self.anchor: str = anchor
        self.to: str = to
        self.mapping: dict[str, list[str]] = mapping

    def pl(self, explode: bool = False):
        """Export xref mappings to a polars DataFrame."""
        df = pl.DataFrame(
            {
                self.anchor: list(self.mapping.keys()),
                self.to: list(self.mapping.values()),
            }
        ).sort(self.anchor)
        if explode:
            return df.explode(self.to, empty_as_null=True).select(
                [self.anchor, self.to]
            )

        return df.select([self.anchor, self.to])

    def reverse(self) -> dict[str, str]:
        """Export the mappings as a to: anchor dictionary."""
        df = self.pl(explode=True)
        return {row[1]: row[0] for row in df.iter_rows()}

    def add(self, new: dict[str, list[str]]) -> None:
        """Add a new key and value to the mapping."""
        for k, v in new.items():
            if k in self.mapping:
                self.add_existing(k, v)
            else:
                self.mapping[k] = v

    def add_existing(self, key: str, val: list[str]) -> None:
        """Add another mapping to an existing anchor term."""
        if key in self.mapping:
            existing: list[str] = self.mapping[key]
            existing.extend(val)
            self.mapping.update({key: list(set(existing))})

        else:
            warnings.warn(
                "Attempted to add value to XRef mapping, but key does not exist. Skipping..."
            )

    def __repr__(self):
        return f"{self.__class__.__name__}(anchor={self.anchor!r}, to={self.to!r}, mapping={self.mapping!r})"


class Ontology:
    """This class contains functionalities for working with ontologies. Currently only supports
    ontologies stored in obo files.

    Attributes:
        entries (list[str]):
            Entries from the ontology that begin with the pattern [Term].

        _class_dict (dict[str, str]):
            Term ID to term name mapping (e.g., {MONDO:0006858: 'mouth disorder'}).

    Example:

        >>> from  metahq_build.ontology import Ontology
        >>> op = Ontology.from_obo("mondo.obo", ontology="mondo")
    """

    def __init__(self):
        self._entries: list[OboEntry] = []
        self._class_dict: dict[str, str] = {}

    def get_class_dict(self):
        """
        Fills the _class_dict attribute with id: name pairs.

        Arguments:
            verbose (bool):
                If True, will print redundant terms.

        """
        for entry in self.entries:
            self._class_dict[entry.id] = entry.name.lower()

    def read(
        self, file: Path | str, reader: OntologyReader = OntologyReader.OBO
    ) -> None:
        """
        Loads and reads an ontology file.

        Arguments:
            file (str | Path):
                Path to ontology file.
            reader (OntologyReader):
                File type to read from.

        Example:

            >>> from  metahq_build.ontology import Ontology
            >>> op = Ontology()
            >>> op.read("mondo.obo", reader=OntologyReader.OBO)
            >>> op.entries[0]
            OboEntry(
                id="MONDO:0000001",
                name="disease",
                def="A diease is a disposition to ...",
                ...,
                xrefs=[XRef(...), ...],
            )
        """
        reader = OntologyReader(reader)
        match reader:
            case OntologyReader.OBO:
                loaded = self.obo_reader(file)
                self.entries = self.get_entries(loaded)

    def id_map(
        self, struct: IdMapStruct = IdMapStruct.POLARS
    ) -> dict[str, str] | pl.DataFrame:
        """Returns class_dict as specified data structure."""
        struct = IdMapStruct(struct)

        match struct:
            case IdMapStruct.POLARS:
                return self._id_map_to_polars()
            case IdMapStruct.DICT:
                return self.class_dict

    def _id_map_to_polars(self):
        """Convert self.class_dict to polars DataFrame."""
        d = {"id": list(self.class_dict.keys()), "name": list(self.class_dict.values())}
        return pl.DataFrame(d)

    @property
    def class_dict(self) -> dict[str, str]:
        """Returns the dictionary storing terms IDs and their names."""
        if len(self._class_dict) == 0:
            self.get_class_dict()

        return self._class_dict

    @property
    def entries(self) -> list[OboEntry]:
        """Returns entries from the ontology."""
        return self._entries

    @entries.setter
    def entries(self, val):
        """Sets self.entries value."""
        if not isinstance(val, list):
            raise TypeError(f"Expected list, not {type(val)}.")

        for entry in val:
            if not isinstance(entry, OboEntry):
                raise TypeError(f"Expected OboEntry. Got {type(entry)}.")

        self._entries = val

    @staticmethod
    def get_entries(obo_text: str) -> list[OboEntry]:
        """Returns a list of entries from entries combined by \n\n"""
        entries = [
            OboEntry.from_text(entry)
            for entry in obo_text.split("\n\n")
            if (entry.startswith("[Term]")) and ("is_obsolete: true" not in entry)
        ]

        return entries

    @staticmethod
    def obo_reader(obo: Path | str) -> str:
        """Reads text from an obo file (plain or gzipped)."""
        obo = Path(obo)
        if obo.suffix == ".gz":
            with gzip.open(obo, "rt", encoding="utf-8") as f:
                text = f.read()
        else:
            with open(obo, "r", encoding="utf-8") as f:
                text = f.read()
        return text

    @staticmethod
    def reverse_dict(d: dict) -> dict:
        """Sets values as keys and keys as values."""
        _d = {}
        for key, val in d.items():
            _d[val] = key
        return _d

    @staticmethod
    def select_from_xref(term: str, _map: dict) -> str:
        """Pulls the xref for a query term."""
        if term in _map.keys():
            _id = _map[term]
        else:
            _id = "NA"

        return _id

    @classmethod
    def from_obo(cls, obo: Path | str):
        """Create Ontology class from an obo file."""
        parser = cls()
        parser.read(obo, reader=OntologyReader.OBO)
        return parser


def get_id_map(obo_file: Path) -> pl.DataFrame:
    """Return a term ID to name mapping."""
    onto = Ontology.from_obo(obo_file)
    return onto.id_map(struct=IdMapStruct.POLARS)
