"""Correction automatique d'un XML pour le rendre conforme a un XSD.

Corrections appliquees, dans l'ordre :
  1. espace de noms de la racine (ajout ou suppression) ;
  2. suppression des elements inconnus du XSD          (option, desactivee par defaut) ;
  3. remise en ordre des balises selon le modele XSD   (option, activee) ;
  4. ajout des elements obligatoires manquants, vides  (option, desactivee par defaut) ;
  5. nettoyage des espaces parasites autour des valeurs typees (option, activee).

Regle de conduite : on ne fabrique jamais de donnee metier. Les elements ajoutes
sont vides et signales comme « a completer » dans le rapport.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from lxml import etree

from .schema_model import (
    ContentModel,
    ElementDecl,
    SchemaSet,
    Slot,
    join_tag,
    split_tag,
)
from .validator import CAT_VALUE, Validator

MAX_INSERT_DEPTH = 4

# Commentaire accolé à chaque balise insérée par l'outil. Il doit rester
# repérable d'un coup d'œil dans le fichier : ces balises sont vides et ne
# valideront pas tant qu'une valeur n'y aura pas été portée.
# (Aucun « -- » : la norme XML l'interdit à l'intérieur d'un commentaire.)
INSERTED_COMMENT = (" AJOUTÉ PAR LE CONTRÔLEUR XML/XSD : balise obligatoire absente du "
                    "fichier d'origine. À vérifier et à compléter avant envoi. ")


@dataclass
class Change:
    kind: str          # namespace | order | insert | remove | trim
    path: str
    detail: str

    def as_dict(self) -> Dict:
        return {"kind": self.kind, "path": self.path, "detail": self.detail}


@dataclass
class Options:
    reorder: bool = True
    fix_namespace: bool = True
    trim_values: bool = True
    insert_missing: bool = False
    remove_unknown: bool = False
    comment_inserted: bool = True      # signaler chaque balise ajoutee

    @classmethod
    def from_dict(cls, raw: Optional[Dict]) -> "Options":
        raw = raw or {}
        opts = cls()
        for key in ("reorder", "fix_namespace", "trim_values", "insert_missing",
                    "remove_unknown", "comment_inserted"):
            if key in raw:
                setattr(opts, key, bool(raw[key]))
        return opts


# --------------------------------------------------------------------------- outils

def _element_children(el: etree._Element) -> List[etree._Element]:
    return [c for c in el if isinstance(c.tag, str)]


def _child_path(path: str, el: etree._Element, index: int, total_same: int) -> str:
    name = split_tag(el.tag)[1]
    return "%s/%s%s" % (path, name, "[%d]" % index if total_same > 1 else "")


def _pretty_name(qname) -> str:
    return qname[1]


# --------------------------------------------------------------------------- espace de noms

def document_namespaces(root: etree._Element) -> Set[Optional[str]]:
    """Espaces de noms réellement portés par les éléments du document."""
    return {split_tag(el.tag)[0] for el in root.iter() if isinstance(el.tag, str)}


def diagnose_namespace(root: etree._Element, schema: SchemaSet) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Retourne (ns_actuel, ns_cible) si la racine doit changer d'espace de noms.

    Ajouter un espace de noms absent est sans risque. En retirer un est
    destructeur : on ne le fait que si le document n'en utilise qu'un seul.
    Un document multi-espaces (UBL, Factur-X…) confronté a un XSD sans
    targetNamespace n'est pas un probleme de racine a « reparer » : c'est le
    mauvais schema, et le dire vaut mieux que de mutiler le fichier.
    """
    current_ns, local = split_tag(root.tag)
    if (current_ns, local) in schema.global_elements:
        return None
    candidates = schema.find_root_by_local_name(local)
    if len(candidates) != 1:
        return None
    target_ns = candidates[0].qname[0]
    if target_ns == current_ns:
        return None
    if target_ns is None and current_ns is not None:
        used = {ns for ns in document_namespaces(root) if ns is not None}
        if len(used) > 1:
            return None
    return (current_ns, target_ns)


def _rebuild(el: etree._Element, old_ns: Optional[str], target_ns: Optional[str],
             local_ns: Optional[str], nsmap: Optional[Dict]) -> etree._Element:
    """Recopie l'arbre en changeant l'espace de noms des elements concernes.

    `target_ns` s'applique a `el`, `local_ns` a toute sa descendance : les deux
    different quand le XSD est en elementFormDefault="unqualified" (seule la
    racine porte alors l'espace de noms).
    """
    ns, local = split_tag(el.tag)
    new_tag = join_tag((target_ns, local)) if ns == old_ns else el.tag
    new_el = etree.Element(new_tag, nsmap=nsmap) if nsmap is not None else etree.Element(new_tag)
    for key, value in el.attrib.items():
        new_el.set(key, value)
    new_el.text = el.text
    new_el.tail = el.tail
    for child in el:
        if isinstance(child.tag, str):
            new_el.append(_rebuild(child, old_ns, local_ns, local_ns, None))
        else:
            new_el.append(copy.deepcopy(child))
    return new_el


def apply_namespace(tree: etree._ElementTree, old_ns: Optional[str], new_ns: Optional[str],
                    schema: SchemaSet, changes: List[Change]) -> etree._ElementTree:
    root = tree.getroot()
    decl = schema.global_elements.get((new_ns, split_tag(root.tag)[1]))
    qualified = schema.qualified_by_default(decl.node) if decl is not None else True
    local_ns = new_ns if qualified else None

    nsmap = {k: v for k, v in root.nsmap.items() if v != old_ns}
    if not new_ns:
        nsmap.pop(None, None)
    elif local_ns is None:
        # Schema "unqualified" : seule la racine est dans l'espace de noms. On ne
        # peut pas utiliser un xmlns par defaut, car libxml2 n'ecrit pas le
        # xmlns="" des enfants et ceux-ci se retrouveraient dans l'espace de noms.
        nsmap.pop(None, None)
        prefix = "ns"
        while prefix in nsmap:
            prefix += "x"
        nsmap[prefix] = new_ns
    else:
        nsmap[None] = new_ns

    new_root = _rebuild(root, old_ns, new_ns, local_ns, nsmap)
    etree.cleanup_namespaces(new_root)
    new_tree = etree.ElementTree(new_root)
    # on reporte les commentaires / instructions de traitement hors racine
    for node in reversed(list(root.itersiblings(preceding=True))):
        new_root.addprevious(copy.deepcopy(node))
    anchor = new_root
    for node in root.itersiblings():
        clone = copy.deepcopy(node)
        anchor.addnext(clone)
        anchor = clone
    if new_ns:
        detail = "espace de noms « %s » appliqué%s" % (
            new_ns, " à tout le document" if local_ns else " à la racine")
    else:
        detail = "espace de noms « %s » retiré" % old_ns
    changes.append(Change("namespace", "/" + split_tag(new_root.tag)[1], detail))
    return new_tree


# --------------------------------------------------------------------------- reordonnancement

def _sort_keys(nodes: List, model: ContentModel) -> List[Tuple]:
    """Cle de tri pour chaque noeud enfant (elements, commentaires, PI)."""
    total = len(nodes)
    element_keys: List[Optional[Tuple[int, ...]]] = []
    for node in nodes:
        if isinstance(node.tag, str):
            slot = model.by_name.get(split_tag(node.tag))
            element_keys.append(slot.key if slot is not None else None)
        else:
            element_keys.append(None)

    # cle de l'element connu qui suit (pour rattacher les commentaires)
    following: List[Optional[Tuple[int, ...]]] = [None] * total
    seen = None
    for i in range(total - 1, -1, -1):
        if element_keys[i] is not None:
            seen = element_keys[i]
        following[i] = seen

    keys: List[Tuple] = []
    last: Tuple[int, ...] = (-1,)
    for i, node in enumerate(nodes):
        if isinstance(node.tag, str):
            key = element_keys[i]
            if key is None:
                key = last          # element inconnu : reste ancre a sa position
            last = key
        else:
            key = following[i] if following[i] is not None else last
        keys.append(key)
    return keys


def _reorder_children(el: etree._Element, model: ContentModel, path: str,
                      changes: List[Change]) -> bool:
    nodes = list(el)
    if len(nodes) < 2:
        return False
    keys = _sort_keys(nodes, model)
    order = sorted(range(len(nodes)), key=lambda i: keys[i])
    if order == list(range(len(nodes))):
        return False

    before = [split_tag(n.tag)[1] for n in nodes if isinstance(n.tag, str)]
    tails = [n.tail for n in nodes]
    reordered = [nodes[i] for i in order]
    for node in nodes:
        el.remove(node)
    for position, node in enumerate(reordered):
        el.append(node)
        node.tail = tails[position]

    after = [split_tag(n.tag)[1] for n in reordered if isinstance(n.tag, str)]
    changes.append(Change(
        "order", path,
        "ordre des balises corrigé : %s  →  %s" % (" ; ".join(before), " ; ".join(after)),
    ))
    return True


def _remove_unknown(el: etree._Element, model: ContentModel, path: str,
                    changes: List[Change]) -> None:
    if model.has_wildcard or model.unconstrained:
        return
    for child in _element_children(el):
        if split_tag(child.tag) not in model.by_name:
            name = split_tag(child.tag)[1]
            previous = child.getprevious()
            if previous is not None:
                previous.tail = child.tail
            el.remove(child)
            changes.append(Change("remove", path, "élément <%s> supprimé (absent du XSD)" % name))


def _build_required_subtree(schema: SchemaSet, decl: ElementDecl, depth: int) -> etree._Element:
    el = etree.Element(join_tag(decl.qname))
    if depth >= MAX_INSERT_DEPTH:
        return el
    model = schema.model_for_decl(decl)
    if model.unconstrained or model.simple_content:
        return el
    for slot in model.slots:
        if slot.required:
            el.append(_build_required_subtree(schema, slot.decl, depth + 1))
    return el


def _indent_subtree(el: etree._Element, base: str, unit: str) -> None:
    """Indente un sous-arbre nouvellement cree : `base` est l'indentation de `el`."""
    children = list(el)
    if not children:
        return
    el.text = "\n" + base + unit
    for index, child in enumerate(children):
        last = index == len(children) - 1
        child.tail = "\n" + base + ("" if last else unit)
        _indent_subtree(child, base + unit, unit)


def _insert_missing(el: etree._Element, model: ContentModel, schema: SchemaSet,
                    path: str, depth: int, options: Options,
                    changes: List[Change]) -> None:
    present = {split_tag(c.tag) for c in _element_children(el)}
    if model.simple_content or model.unconstrained:
        return
    for slot in model.slots:
        if not slot.required or slot.qname in present:
            continue
        new_el = _build_required_subtree(schema, slot.decl, depth + 1)
        nodes = list(el)
        keys = _sort_keys(nodes, model)
        position = len(nodes)
        for i, key in enumerate(keys):
            if key > slot.key:
                position = i
                break

        unit = "  "
        lead = el.text if position == 0 else nodes[position - 1].tail
        # indentation des freres : en fin de liste, `lead` est l'indentation de
        # fermeture du parent, plus courte que celle des enfants
        if position == len(nodes) and position >= 1:
            frere = nodes[position - 2].tail if position >= 2 else el.text
            nodes[position - 1].tail = frere
            fermeture = lead
        else:
            frere = lead
            fermeture = lead

        el.insert(position, new_el)
        if options.comment_inserted:
            # le commentaire precede la balise : c'est la position lisible, et
            # le reordonnancement rattache un commentaire a l'element qui le suit
            comment = etree.Comment(INSERTED_COMMENT)
            new_el.addprevious(comment)
            comment.tail = frere
        new_el.tail = fermeture

        if len(new_el):
            _indent_subtree(new_el, (frere or "\n").rsplit("\n", 1)[-1], unit)

        present.add(slot.qname)
        detail = "élément obligatoire <%s> ajouté (vide, à compléter)" % _pretty_name(slot.qname)
        if options.comment_inserted:
            detail += ", signalé par un commentaire dans le fichier"
        changes.append(Change("insert", path, detail))


def _walk(el: etree._Element, decl: Optional[ElementDecl], schema: SchemaSet,
          options: Options, path: str, depth: int, changes: List[Change]) -> None:
    model = schema.model_for_decl(decl)
    if model.unconstrained or model.simple_content:
        return

    if options.remove_unknown:
        _remove_unknown(el, model, path, changes)
    if options.reorder and model.orderable:
        _reorder_children(el, model, path, changes)
    if options.insert_missing and model.orderable:
        _insert_missing(el, model, schema, path, depth, options, changes)

    children = _element_children(el)
    counts: Dict[str, int] = {}
    for child in children:
        name = split_tag(child.tag)[1]
        counts[name] = counts.get(name, 0) + 1
    seen: Dict[str, int] = {}
    for child in children:
        qname = split_tag(child.tag)
        name = qname[1]
        seen[name] = seen.get(name, 0) + 1
        child_path = _child_path(path, child, seen[name], counts[name])
        slot = model.by_name.get(qname)
        child_decl = slot.decl if slot is not None else schema.global_elements.get(qname)
        _walk(child, child_decl, schema, options, child_path, depth + 1, changes)


# --------------------------------------------------------------------------- valeurs

def _trim_values(tree: etree._ElementTree, validator: Validator,
                 changes: List[Change]) -> None:
    for _ in range(3):
        errors = validator.validate(tree)
        lines = {e.line for e in errors if e.category == CAT_VALUE}
        if not lines:
            return
        touched = False
        for el in tree.getroot().iter():
            if not isinstance(el.tag, str) or len(el):
                continue
            if el.sourceline in lines and el.text and el.text.strip() != el.text:
                el.text = el.text.strip()
                touched = True
                changes.append(Change(
                    "trim", "<%s>" % split_tag(el.tag)[1],
                    "espaces superflus retirés autour de la valeur"))
        if not touched:
            return


# --------------------------------------------------------------------------- pipeline

def serialize(tree: etree._ElementTree, encoding: Optional[str]) -> bytes:
    """Serialise en conservant declaration, doctype et commentaires de prologue.

    On assemble le document a la main : lxml ne memorise pas les blancs du
    prologue, et modifier le `tail` d'un noeud de prologue le detacherait.
    """
    enc = encoding or "UTF-8"
    root = tree.getroot()
    parts = ["<?xml version='%s' encoding='%s'?>" % (tree.docinfo.xml_version or "1.0", enc)]
    if tree.docinfo.doctype:
        parts.append(tree.docinfo.doctype)
    for node in reversed(list(root.itersiblings(preceding=True))):
        parts.append(etree.tostring(node, encoding="unicode", with_tail=False))
    parts.append(etree.tostring(root, encoding="unicode", with_tail=False))
    for node in root.itersiblings():
        parts.append(etree.tostring(node, encoding="unicode", with_tail=False))
    return ("\n".join(parts) + "\n").encode(enc, "xmlcharrefreplace")


def correct(xml_bytes: bytes, schema: SchemaSet, validator: Validator,
            options: Options) -> Tuple[bytes, List[Change]]:
    """Applique les corrections et renvoie (xml corrige, liste des changements)."""
    changes: List[Change] = []
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    tree = etree.fromstring(xml_bytes, parser).getroottree()
    encoding = tree.docinfo.encoding

    if options.fix_namespace:
        diag = diagnose_namespace(tree.getroot(), schema)
        if diag is not None:
            tree = apply_namespace(tree, diag[0], diag[1], schema, changes)

    root = tree.getroot()
    root_decl = schema.global_elements.get(split_tag(root.tag))
    if root_decl is not None:
        _walk(root, root_decl, schema, options, "/" + split_tag(root.tag)[1], 0, changes)

    data = serialize(tree, encoding)

    if options.trim_values:
        # on relit le document pour disposer de numeros de ligne a jour
        reparsed = etree.fromstring(data, parser).getroottree()
        before = len(changes)
        _trim_values(reparsed, validator, changes)
        if len(changes) > before:
            data = serialize(reparsed, encoding)

    return data, changes
