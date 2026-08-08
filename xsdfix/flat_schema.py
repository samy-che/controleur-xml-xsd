"""Conversion d'un XSD « à plat » en XSD à espaces de noms.

Les generateurs qui deduisent un XSD depuis un XML d'exemple (Liquid
Technologies, XmlGrid, FreeFormatter…) ne gerent pas les espaces de noms : ils
suppriment le targetNamespace et transforment `cbc:UBLVersionID` en un nom
LITTERAL `cbc.UBLVersionID`. Le schema obtenu ne peut plus valider le XML dont
il est issu.

Ce module retablit la semantique perdue en conservant l'ordre des balises.
Il est utilise aussi bien par le site (via Pyodide) que par convertir_xsd.py.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lxml import etree

XS = "http://www.w3.org/2001/XMLSchema"
NSMAP_XS = {"xs": XS}

# « cbc.UBLVersionID », « cbc_UBLVersionID », « cbc..ID » → (cbc, UBLVersionID)
RE_PREFIXED = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,7})[._-]{1,2}(.+)$")


def qn(tag: str) -> str:
    return "{%s}%s" % (XS, tag)


def local(node) -> str:
    tag = node.tag
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def split_prefixed(name: str) -> Optional[Tuple[str, str]]:
    match = RE_PREFIXED.match(name or "")
    return (match.group(1), match.group(2)) if match else None


@dataclass
class Child:
    prefix: str
    local: str
    min_occurs: str = "1"
    max_occurs: str = "1"


@dataclass
class Decl:
    """Ce que l'on sait d'un élément, avant fusion des doublons."""
    type_name: Optional[str] = None            # type="xs:string"
    simple_base: Optional[str] = None          # simpleContent/extension base
    children: List[Child] = field(default_factory=list)
    attributes: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def is_complex(self) -> bool:
        return bool(self.children) or self.simple_base is not None


class Converter:
    def __init__(self, namespaces: Dict[str, str]):
        self.namespaces = namespaces
        self.registry: "OrderedDict[Tuple[str, str], List[Decl]]" = OrderedDict()
        self.root: Optional[Tuple[str, str]] = None
        self.conflicts: List[str] = []
        self.unknown_prefixes = set()

    # ------------------------------------------------------------- lecture

    def read(self, path: str) -> None:
        schema = etree.parse(path).getroot()
        for node in schema:
            if isinstance(node.tag, str) and local(node) == "element" and node.get("name"):
                key = self._read_element(node)
                if key and self.root is None:
                    self.root = key

    def _read_element(self, node) -> Optional[Tuple[str, str]]:
        raw = node.get("name")
        parts = split_prefixed(raw)
        if parts is None:
            # nom sans préfixe : on le garde tel quel dans l'espace de noms principal
            parts = (next(iter(self.namespaces)) if self.namespaces else "ubl", raw)
        prefix, name = parts
        if prefix not in self.namespaces:
            self.unknown_prefixes.add(prefix)

        decl = Decl()
        if node.get("type"):
            decl.type_name = node.get("type")
        else:
            complex_type = node.find(qn("complexType"))
            if complex_type is not None:
                self._read_complex(complex_type, decl)
        self.registry.setdefault((prefix, name), []).append(decl)
        return (prefix, name)

    def _read_complex(self, node, decl: Decl) -> None:
        simple = node.find(qn("simpleContent"))
        if simple is not None:
            extension = simple.find(qn("extension"))
            if extension is not None:
                decl.simple_base = extension.get("base", "xs:string")
                for attribute in extension.findall(qn("attribute")):
                    decl.attributes.append((attribute.get("name"),
                                            attribute.get("type", "xs:string")))
            return
        for attribute in node.findall(qn("attribute")):
            decl.attributes.append((attribute.get("name"), attribute.get("type", "xs:string")))
        for compositor in node:
            if local(compositor) in ("sequence", "all", "choice"):
                for child in compositor:
                    if isinstance(child.tag, str) and local(child) == "element":
                        key = self._read_element(child)
                        if key:
                            decl.children.append(Child(key[0], key[1],
                                                       child.get("minOccurs", "1"),
                                                       child.get("maxOccurs", "1")))

    # ------------------------------------------------------------- fusion

    def merge(self) -> "OrderedDict[Tuple[str, str], Decl]":
        """Un même nom peut être déclaré plusieurs fois avec des formes différentes
        (le générateur a vu des contextes différents). En XSD un élément global
        n'a qu'une définition : on les réconcilie."""
        merged: "OrderedDict[Tuple[str, str], Decl]" = OrderedDict()
        for key, decls in self.registry.items():
            if len(decls) == 1:
                merged[key] = decls[0]
                continue
            label = "%s:%s" % key
            if all(not d.is_complex for d in decls):
                types = {d.type_name for d in decls}
                if len(types) == 1:
                    merged[key] = decls[0]
                else:
                    self.conflicts.append(
                        "%s : types simples divergents (%s) → xs:string retenu"
                        % (label, ", ".join(sorted(t or "?" for t in types))))
                    merged[key] = Decl(type_name="xs:string")
                continue

            # au moins une forme complexe : on réunit les enfants dans l'ordre
            # de première apparition, et tout devient facultatif car un enfant
            # peut n'exister que dans un contexte
            union: "OrderedDict[Tuple[str, str], Child]" = OrderedDict()
            attributes: "OrderedDict[str, str]" = OrderedDict()
            base = None
            for decl in decls:
                base = base or decl.simple_base
                for attribute, attr_type in decl.attributes:
                    attributes.setdefault(attribute, attr_type)
                for child in decl.children:
                    existing = union.get((child.prefix, child.local))
                    if existing is None:
                        union[(child.prefix, child.local)] = Child(
                            child.prefix, child.local, "0", child.max_occurs)
                    elif child.max_occurs == "unbounded":
                        existing.max_occurs = "unbounded"
            if len(decls) > 1:
                self.conflicts.append(
                    "%s : %d définitions différentes fusionnées (enfants rendus facultatifs)"
                    % (label, len(decls)))
            merged[key] = Decl(simple_base=base, children=list(union.values()),
                               attributes=list(attributes.items()))
        return merged

    # ------------------------------------------------------------- écriture

    def write(self, merged, outdir: str) -> List[str]:
        by_prefix: Dict[str, List[Tuple[str, Decl]]] = {}
        for (prefix, name), decl in merged.items():
            by_prefix.setdefault(prefix, []).append((name, decl))

        os.makedirs(outdir, exist_ok=True)
        written = []
        for prefix, elements in by_prefix.items():
            namespace = self.namespaces.get(prefix)
            if namespace is None:
                continue
            schema = self._build_schema(prefix, namespace, elements)
            path = os.path.join(outdir, "%s.xsd" % prefix)
            etree.ElementTree(schema).write(path, pretty_print=True,
                                            xml_declaration=True, encoding="UTF-8")
            written.append(path)
        return written

    def _build_schema(self, prefix: str, namespace: str, elements):
        """Construit le <xs:schema> d'un espace de noms, imports compris."""
        needed = {c.prefix for _, decl in elements for c in decl.children}
        needed.discard(prefix)

        nsmap = {"xs": XS, prefix: namespace}
        for other in needed:
            if other in self.namespaces:
                nsmap[other] = self.namespaces[other]
        schema = etree.Element(qn("schema"), nsmap=nsmap)
        schema.set("targetNamespace", namespace)
        schema.set("elementFormDefault", "qualified")
        schema.set("attributeFormDefault", "unqualified")

        for other in sorted(needed):
            if other in self.namespaces:
                node = etree.SubElement(schema, qn("import"))
                node.set("namespace", self.namespaces[other])
                node.set("schemaLocation", "%s.xsd" % other)

        for name, decl in elements:
            self._write_element(schema, name, decl)
        return schema

    def _write_element(self, parent, name: str, decl: Decl) -> None:
        node = etree.SubElement(parent, qn("element"))
        node.set("name", name)
        if not decl.is_complex:
            node.set("type", decl.type_name or "xs:string")
            return

        complex_type = etree.SubElement(node, qn("complexType"))
        if decl.simple_base is not None and not decl.children:
            content = etree.SubElement(complex_type, qn("simpleContent"))
            extension = etree.SubElement(content, qn("extension"))
            extension.set("base", decl.simple_base)
            for attribute, attr_type in decl.attributes:
                attr = etree.SubElement(extension, qn("attribute"))
                attr.set("name", attribute)
                attr.set("type", attr_type)
            return

        sequence = etree.SubElement(complex_type, qn("sequence"))
        for child in decl.children:
            ref = etree.SubElement(sequence, qn("element"))
            ref.set("ref", "%s:%s" % (child.prefix, child.local))
            if child.min_occurs != "1":
                ref.set("minOccurs", child.min_occurs)
            if child.max_occurs != "1":
                ref.set("maxOccurs", child.max_occurs)
        for attribute, attr_type in decl.attributes:
            attr = etree.SubElement(complex_type, qn("attribute"))
            attr.set("name", attribute)
            attr.set("type", attr_type)


def looks_flat(data: bytes) -> Optional[Dict]:
    """Reconnait un XSD genere « a plat », sans espace de noms.

    Deux signes concordants : aucun targetNamespace, et des noms de balises qui
    portent un prefixe colle (`cbc.ID`). On exige qu'au moins deux prefixes
    differents se repetent, pour ne pas confondre avec un schema dont les noms
    contiennent legitimement un point.
    """
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return None
    if local(root) != "schema" or root.get("targetNamespace"):
        return None

    counts: Dict[str, int] = {}
    total = 0
    for node in root.iter(qn("element")):
        name = node.get("name")
        if not name:
            continue
        total += 1
        parts = split_prefixed(name)
        if parts:
            counts[parts[0]] = counts.get(parts[0], 0) + 1

    repetes = {p: n for p, n in counts.items() if n >= 2}
    if len(repetes) < 2 or sum(repetes.values()) < max(3, total // 2):
        return None
    return {"prefixes": sorted(repetes), "prefixed": sum(repetes.values()), "total": total}


def namespaces_from_root(root) -> Dict[str, str]:
    """Prefixes declares par un document XML deja analyse."""
    return {(prefix or "ubl"): uri for prefix, uri in root.nsmap.items()}


def convert(xsd_data: bytes, namespaces: Dict[str, str]):
    """Convertit en memoire. Renvoie (fichiers, racine, arbitrages).

    `fichiers` : liste de (nom, contenu octets), un par espace de noms.
    """
    root = etree.fromstring(xsd_data)
    converter = Converter(namespaces)
    for node in root:
        if isinstance(node.tag, str) and local(node) == "element" and node.get("name"):
            key = converter._read_element(node)
            if key and converter.root is None:
                converter.root = key
    merged = converter.merge()

    fichiers = []
    by_prefix: Dict[str, List] = {}
    for (prefix, name), decl in merged.items():
        by_prefix.setdefault(prefix, []).append((name, decl))
    for prefix, elements in by_prefix.items():
        namespace = namespaces.get(prefix)
        if namespace is None:
            continue
        schema = converter._build_schema(prefix, namespace, elements)
        fichiers.append(("%s.xsd" % prefix,
                         etree.tostring(schema, pretty_print=True,
                                        xml_declaration=True, encoding="UTF-8")))
    return fichiers, converter.root, converter.conflicts


def namespaces_from_xml(path: str) -> Dict[str, str]:
    """Apprend les préfixes depuis un XML réel : c'est la source la plus sûre."""
    root = etree.parse(path).getroot()
    found = {}
    for prefix, uri in root.nsmap.items():
        found[prefix or "ubl"] = uri      # le préfixe par défaut porte la racine
    return found
