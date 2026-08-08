"""Validation XSD via lxml + normalisation des messages d'erreur en francais."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lxml import etree


# Categories d'erreurs (utilisees par l'IHM pour le code couleur et par le
# correcteur pour savoir ce qu'il sait reparer).
CAT_ORDER = "ordre"
CAT_MISSING = "manquant"
CAT_UNEXPECTED = "inattendu"
CAT_VALUE = "valeur"
CAT_ATTRIBUTE = "attribut"
CAT_ROOT = "racine"
CAT_OTHER = "autre"

AUTO_FIXABLE = {CAT_ORDER, CAT_ROOT, CAT_VALUE}


@dataclass
class ValidationError:
    line: int
    column: int
    message: str            # message brut lxml
    label: str              # message reformule en francais
    category: str
    element: Optional[str] = None       # nom local de l'element concerne
    element_ns: Optional[str] = None    # son espace de noms, s'il en a un
    expected: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "label": self.label,
            "category": self.category,
            "element": self.element,
            "expected": self.expected,
            "autoFixable": self.category in AUTO_FIXABLE,
        }


_RE_ELEMENT = re.compile(r"^Element '([^']+)'(?:, attribute '([^']+)')?: (.*)$", re.S)
_RE_EXPECTED = re.compile(r"Expected is (?:one of )?\(([^)]*)\)")
_RE_ATOMIC = re.compile(r"'(.*)' is not a valid value of the (?:atomic|local|union) type '([^']+)'")
_RE_FACET = re.compile(r"\[facet '([^']+)'\] The value '(.*?)' (.*)$", re.S)
_RE_PATTERN = re.compile(r"pattern '(.*)'\.?$", re.S)
_RE_SET = re.compile(r"set \{(.*)\}", re.S)

_FACET_LABEL = {
    "minInclusive": "est inférieure au minimum autorisé",
    "minExclusive": "est inférieure au minimum autorisé",
    "maxInclusive": "dépasse le maximum autorisé",
    "maxExclusive": "dépasse le maximum autorisé",
    "minLength": "est trop courte",
    "maxLength": "est trop longue",
    "length": "n'a pas la longueur attendue",
    "totalDigits": "comporte trop de chiffres",
    "fractionDigits": "comporte trop de décimales",
}


def _facet_label(where: str, facet: str, value: str, rest: str) -> str:
    """Reformule une violation de contrainte (pattern, énumération, bornes…)."""
    if facet == "pattern":
        found = _RE_PATTERN.search(rest)
        detail = "ne respecte pas le format attendu"
        constraint = "motif : %s" % found.group(1) if found else "format imposé"
    elif facet == "enumeration":
        found = _RE_SET.search(rest)
        detail = "n'est pas une valeur reconnue"
        constraint = ("valeurs autorisées : %s" % found.group(1).replace("'", "")
                      if found else "liste de valeurs imposée")
    else:
        detail = _FACET_LABEL.get(facet, "ne respecte pas la contrainte « %s »" % facet)
        constraint = None

    if value == "":
        return "%s est vide : valeur à compléter%s." % (
            where, " (%s)" % constraint if constraint else "")
    return "Valeur « %s » de %s : elle %s%s." % (
        value, where, detail, " (%s)" % constraint if constraint else "")


def _split(qname: str):
    """'{urn:ns}Facture' -> ('urn:ns', 'Facture') ; 'Facture' -> (None, 'Facture')."""
    if qname.startswith("{"):
        namespace, local = qname[1:].split("}", 1)
        return (namespace, local)
    return (None, qname)


def _short(qname: str) -> str:
    """'{urn:ns}Facture' -> 'Facture' (on garde le nom lisible)."""
    return _split(qname)[1]


def _expected_list(raw: str) -> List[str]:
    return [_short(part.strip()) for part in raw.split(",") if part.strip()]


def humanize(message: str) -> Dict:
    """Traduit un message lxml en (categorie, libelle francais, element, attendus)."""
    element = None
    element_ns = None
    attribute = None
    body = message
    match = _RE_ELEMENT.match(message.strip())
    if match:
        element_ns, element = _split(match.group(1))
        attribute = match.group(2)
        body = match.group(3)

    expected: List[str] = []
    exp = _RE_EXPECTED.search(body)
    if exp:
        expected = _expected_list(exp.group(1))

    where = "<%s>" % element if element else "le document"

    if attribute:
        atomic = _RE_ATOMIC.search(body)
        if atomic:
            label = "Attribut « %s » de %s : la valeur « %s » n'est pas conforme au type %s." % (
                attribute, where, atomic.group(1), atomic.group(2))
        elif "required" in body.lower():
            label = "Attribut obligatoire « %s » manquant sur %s." % (attribute, where)
        else:
            label = "Problème sur l'attribut « %s » de %s : %s" % (attribute, where, body)
        return {"category": CAT_ATTRIBUTE, "label": label, "element": element, "element_ns": element_ns, "expected": expected}

    if "No matching global declaration available for the validation root" in body:
        return {
            "category": CAT_ROOT,
            "label": "L'élément racine %s n'existe pas dans le XSD (souvent un problème "
                     "d'espace de noms)." % where,
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    if "This element is not expected" in body:
        if expected:
            label = "%s n'est pas attendu ici. Le XSD attend : %s." % (where, ", ".join(expected))
        else:
            label = "%s n'est pas attendu à cet endroit." % where
        # lxml signale de la meme facon un mauvais ordre et un element inconnu :
        # la presence d'une liste "Expected is" indique presque toujours un ordre
        # ou un element manquant juste avant.
        return {
            "category": CAT_ORDER if expected else CAT_UNEXPECTED,
            "label": label,
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    if "Missing child element" in body:
        label = "Il manque un ou plusieurs éléments obligatoires dans %s%s" % (
            where, (" : " + ", ".join(expected) + ".") if expected else ".")
        return {"category": CAT_MISSING, "label": label, "element": element, "element_ns": element_ns, "expected": expected}

    facet = _RE_FACET.search(body)
    if facet:
        return {
            "category": CAT_VALUE,
            "label": _facet_label(where, facet.group(1), facet.group(2), facet.group(3)),
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    atomic = _RE_ATOMIC.search(body)
    if atomic:
        return {
            "category": CAT_VALUE,
            "label": "Valeur « %s » de %s non conforme au type %s." % (
                atomic.group(1), where, atomic.group(2)),
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    if "Character content other than whitespace is not allowed" in body:
        return {
            "category": CAT_VALUE,
            "label": "%s ne doit pas contenir de texte." % where,
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    if "No matching global element declaration available" in body or "is not declared" in body:
        return {
            "category": CAT_UNEXPECTED,
            "label": "%s n'est pas déclaré dans le XSD." % where,
            "element": element, "element_ns": element_ns,
            "expected": expected,
        }

    return {"category": CAT_OTHER, "label": "%s : %s" % (where, body), "element": element, "element_ns": element_ns,
            "expected": expected}


class Validator:
    """Encapsule un lxml.etree.XMLSchema compile."""

    def __init__(self, xsd_path: str):
        self.error: Optional[str] = None
        self.schema: Optional[etree.XMLSchema] = None
        try:
            doc = etree.parse(xsd_path)
            self.schema = etree.XMLSchema(doc)
        except (etree.XMLSchemaParseError, etree.XMLSyntaxError, OSError) as exc:
            self.error = str(exc)

    @property
    def ok(self) -> bool:
        return self.schema is not None

    def validate(self, tree) -> List[ValidationError]:
        if self.schema is None:
            return []
        if self.schema.validate(tree):
            return []
        out: List[ValidationError] = []
        for entry in self.schema.error_log:
            info = humanize(entry.message)
            out.append(
                ValidationError(
                    line=entry.line,
                    column=entry.column,
                    message=entry.message,
                    label=info["label"],
                    category=info["category"],
                    element=info["element"],
                    element_ns=info.get("element_ns"),
                    expected=info["expected"],
                )
            )
        return out
