"""Lecture d'un jeu de schemas XSD et calcul du modele de contenu.

Objectif : pour chaque type complexe du XSD, produire la liste ordonnee des
elements enfants autorises, avec une "cle de tri" par nom d'element. Cette cle
permet ensuite de remettre les balises d'un XML dans l'ordre attendu.

Principe des cles :
  sequence  -> chaque particule recoit un indice croissant  (l'ordre compte)
  choice    -> chaque branche recoit un indice croissant     (branches exclusives)
  all       -> toutes les particules partagent le meme indice (ordre libre)
  compositeur repetable (maxOccurs > 1) -> tous ses descendants partagent la
              meme cle, car on ne peut pas reordonner a l'interieur d'un groupe
              qui se repete (ex : (a,b)* donne a,b,a,b et non a,a,b,b)

Le tri etant stable, les elements de meme cle conservent leur ordre d'origine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

XSD_NS = "http://www.w3.org/2001/XMLSchema"

# Un nom qualifie : (namespace ou None, nom local)
QName = Tuple[Optional[str], str]

UNBOUNDED = -1


def split_tag(tag: str) -> QName:
    """'{ns}local' -> (ns, 'local') ; 'local' -> (None, 'local')."""
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return (ns, local)
    return (None, tag)


def join_tag(qname: QName) -> str:
    ns, local = qname
    return "{%s}%s" % (ns, local) if ns else local


def local_name(node: etree._Element) -> str:
    tag = node.tag
    if not isinstance(tag, str):
        return ""
    return split_tag(tag)[1]


def is_xsd(node: etree._Element, *names: str) -> bool:
    tag = node.tag
    if not isinstance(tag, str):
        return False
    ns, local = split_tag(tag)
    return ns == XSD_NS and local in names


def _occurs(node: etree._Element, attr: str) -> int:
    raw = node.get(attr)
    if raw is None:
        return 1
    if raw == "unbounded":
        return UNBOUNDED
    try:
        return int(raw)
    except ValueError:
        return 1


def min_occurs(node: etree._Element) -> int:
    return _occurs(node, "minOccurs")


def max_occurs(node: etree._Element) -> int:
    return _occurs(node, "maxOccurs")


def repeats(node: etree._Element) -> bool:
    mx = max_occurs(node)
    return mx == UNBOUNDED or mx > 1


def resolve_qname(value: Optional[str], node: etree._Element) -> Optional[QName]:
    """Resout une valeur d'attribut de type QName (ex: 'tns:MonType')."""
    if value is None:
        return None
    nsmap = node.nsmap
    if ":" in value:
        prefix, local = value.split(":", 1)
        return (nsmap.get(prefix), local)
    return (nsmap.get(None), value)


@dataclass
class ElementDecl:
    """Declaration d'un element (globale ou locale)."""

    qname: QName
    node: etree._Element
    type_qname: Optional[QName] = None
    inline_type: Optional[etree._Element] = None
    abstract: bool = False
    substitution_group: Optional[QName] = None


@dataclass
class Slot:
    """Un emplacement d'element dans le modele de contenu d'un type."""

    qname: QName
    key: Tuple[int, ...]
    decl: ElementDecl
    required: bool = False
    repeatable: bool = False


@dataclass
class ContentModel:
    slots: List[Slot] = field(default_factory=list)
    by_name: Dict[QName, Slot] = field(default_factory=dict)
    has_wildcard: bool = False
    simple_content: bool = False   # <xs:simpleContent> : aucun enfant element
    unconstrained: bool = False    # xs:anyType ou modele inconnu : on ne touche a rien

    @property
    def orderable(self) -> bool:
        return bool(self.slots) and not self.unconstrained


class SchemaSet:
    """Charge un XSD principal et ses include/import, puis expose le modele."""

    def __init__(self, main_path: str):
        self.main_path = os.path.abspath(main_path)
        self.docs: Dict[str, etree._Element] = {}
        self.global_elements: Dict[QName, ElementDecl] = {}
        self.global_types: Dict[QName, etree._Element] = {}
        self.global_groups: Dict[QName, etree._Element] = {}
        # tete de groupe de substitution -> membres (transitif)
        self.substitutions: Dict[QName, List[QName]] = {}
        self.load_errors: List[str] = []
        # noms locaux de tous les elements declares quelque part dans le XSD :
        # permet de distinguer « balise inconnue » de « balise mal placee »
        self.declared_names: Set[str] = set()
        self._model_cache: Dict[int, ContentModel] = {}

        self._load(self.main_path)
        self._index_substitutions()

    # ------------------------------------------------------------------ chargement

    def _load(self, path: str) -> None:
        path = os.path.abspath(path)
        if path in self.docs:
            return
        try:
            tree = etree.parse(path)
        except (etree.XMLSyntaxError, OSError) as exc:
            self.load_errors.append("%s : %s" % (os.path.basename(path), exc))
            return
        root = tree.getroot()
        if not is_xsd(root, "schema"):
            self.load_errors.append("%s n'est pas un schema XSD" % os.path.basename(path))
            return
        self.docs[path] = root

        for decl_node in root.iter("{%s}element" % XSD_NS):
            name = decl_node.get("name")
            if name:
                self.declared_names.add(name)
            ref = decl_node.get("ref")
            if ref:
                self.declared_names.add(ref.split(":", 1)[-1])

        base_dir = os.path.dirname(path)
        for child in root:
            if not isinstance(child.tag, str):
                continue
            name = local_name(child)
            if name in ("include", "import", "redefine", "override"):
                loc = child.get("schemaLocation")
                if loc and "://" not in loc:
                    self._load(os.path.join(base_dir, loc))
                continue
            ident = child.get("name")
            if not ident:
                continue
            tns = root.get("targetNamespace")
            qname = (tns, ident)
            if name == "element":
                self.global_elements[qname] = self._make_decl(child, qname)
            elif name in ("complexType", "simpleType"):
                self.global_types.setdefault(qname, child)
            elif name == "group":
                self.global_groups.setdefault(qname, child)

    def _index_substitutions(self) -> None:
        direct: Dict[QName, List[QName]] = {}
        for qname, decl in self.global_elements.items():
            head = decl.substitution_group
            if head is not None:
                direct.setdefault(head, []).append(qname)

        def expand(head: QName, seen: Set[QName]) -> List[QName]:
            out: List[QName] = []
            for member in direct.get(head, []):
                if member in seen:
                    continue
                seen.add(member)
                out.append(member)
                out.extend(expand(member, seen))
            return out

        for head in direct:
            self.substitutions[head] = expand(head, {head})

    # ------------------------------------------------------------------ declarations

    def target_namespace(self, node: etree._Element) -> Optional[str]:
        return node.getroottree().getroot().get("targetNamespace")

    def qualified_by_default(self, node: etree._Element) -> bool:
        root = node.getroottree().getroot()
        return root.get("elementFormDefault", "unqualified") == "qualified"

    def _make_decl(self, node: etree._Element, qname: QName) -> ElementDecl:
        inline = None
        for child in node:
            if is_xsd(child, "complexType", "simpleType"):
                inline = child
                break
        return ElementDecl(
            qname=qname,
            node=node,
            type_qname=resolve_qname(node.get("type"), node),
            inline_type=inline,
            abstract=node.get("abstract") == "true",
            substitution_group=resolve_qname(node.get("substitutionGroup"), node),
        )

    def element_decl(self, node: etree._Element) -> Optional[ElementDecl]:
        """Declaration correspondant a un <xs:element> local ou a un ref."""
        ref = node.get("ref")
        if ref:
            qname = resolve_qname(ref, node)
            return self.global_elements.get(qname) if qname else None
        name = node.get("name")
        if not name:
            return None
        form = node.get("form")
        qualified = form == "qualified" or (form is None and self.qualified_by_default(node))
        ns = self.target_namespace(node) if qualified else None
        return self._make_decl(node, (ns, name))

    # ------------------------------------------------------------------ modele de contenu

    def model_for_decl(self, decl: Optional[ElementDecl]) -> ContentModel:
        if decl is None:
            return ContentModel(unconstrained=True)
        if decl.inline_type is not None:
            return self.model_for_type_node(decl.inline_type)
        if decl.type_qname is not None:
            return self.model_for_type_qname(decl.type_qname)
        return ContentModel(unconstrained=True)  # xs:anyType

    def model_for_type_qname(self, qname: QName) -> ContentModel:
        if qname[0] == XSD_NS:
            # type simple predefini (xs:string, xs:decimal, ...)
            return ContentModel(simple_content=True)
        node = self.global_types.get(qname)
        if node is None:
            return ContentModel(unconstrained=True)
        return self.model_for_type_node(node)

    def model_for_type_node(self, type_node: etree._Element) -> ContentModel:
        cached = self._model_cache.get(id(type_node))
        if cached is not None:
            return cached
        model = ContentModel()
        # place-holder anti-recursion (type qui se reference lui-meme)
        self._model_cache[id(type_node)] = model

        if is_xsd(type_node, "simpleType"):
            model.simple_content = True
            return model

        self._fill_complex_type(type_node, model, (), set())
        for slot in model.slots:
            model.by_name.setdefault(slot.qname, slot)
            for member in self.substitutions.get(slot.qname, []):
                member_decl = self.global_elements.get(member)
                if member_decl is not None:
                    model.by_name.setdefault(
                        member,
                        Slot(qname=member, key=slot.key, decl=member_decl,
                             required=False, repeatable=slot.repeatable),
                    )
        return model

    def _fill_complex_type(
        self,
        type_node: etree._Element,
        model: ContentModel,
        prefix: Tuple[int, ...],
        seen_groups: Set[QName],
    ) -> None:
        for child in type_node:
            if is_xsd(child, "simpleContent"):
                model.simple_content = True
                return
            if is_xsd(child, "complexContent"):
                for deriv in child:
                    if is_xsd(deriv, "extension"):
                        base = resolve_qname(deriv.get("base"), deriv)
                        # contenu effectif = sequence(contenu de base, extension)
                        if base and base[0] != XSD_NS:
                            base_node = self.global_types.get(base)
                            if base_node is not None:
                                self._fill_complex_type(
                                    base_node, model, prefix + (0,), set(seen_groups)
                                )
                        self._fill_particles(deriv, model, prefix + (1,), False, True, seen_groups)
                    elif is_xsd(deriv, "restriction"):
                        # la restriction redefinit entierement le modele
                        self._fill_particles(deriv, model, prefix, False, True, seen_groups)
                    return
                return
            if is_xsd(child, "sequence", "choice", "all", "group"):
                self._fill_particles(type_node, model, prefix, False, True, seen_groups)
                return
        # complexType vide : aucun enfant autorise

    def _fill_particles(
        self,
        container: etree._Element,
        model: ContentModel,
        prefix: Tuple[int, ...],
        frozen: bool,
        required_ctx: bool,
        seen_groups: Set[QName],
    ) -> None:
        """Parcourt les compositeurs directs d'un conteneur (type / extension)."""
        for child in container:
            if is_xsd(child, "sequence", "choice", "all", "group"):
                self._walk_compositor(child, model, prefix, frozen, required_ctx, seen_groups)

    def _walk_compositor(
        self,
        node: etree._Element,
        model: ContentModel,
        prefix: Tuple[int, ...],
        frozen: bool,
        required_ctx: bool,
        seen_groups: Set[QName],
    ) -> None:
        kind = local_name(node)

        if kind == "group":
            ref = resolve_qname(node.get("ref"), node)
            if ref is None or ref in seen_groups:
                return
            group_node = self.global_groups.get(ref)
            if group_node is None:
                return
            child_frozen = frozen or repeats(node)
            ctx = required_ctx and min_occurs(node) >= 1
            for inner in group_node:
                if is_xsd(inner, "sequence", "choice", "all"):
                    self._walk_compositor(
                        inner, model, prefix, child_frozen, ctx, seen_groups | {ref}
                    )
            return

        ctx = required_ctx and min_occurs(node) >= 1
        frozen = frozen or repeats(node)
        # dans un <xs:all> l'ordre est libre : toutes les particules partagent la cle
        keep_index = kind != "all"
        index = 0

        for child in node:
            if not isinstance(child.tag, str):
                continue
            key = prefix if frozen else prefix + (index,)
            if keep_index and not frozen:
                index += 1

            if is_xsd(child, "element"):
                decl = self.element_decl(child)
                if decl is None:
                    continue
                required = (
                    ctx
                    and kind != "choice"
                    and min_occurs(child) >= 1
                    and not frozen
                    and not decl.abstract
                )
                model.slots.append(
                    Slot(
                        qname=decl.qname,
                        key=key,
                        decl=decl,
                        required=required,
                        repeatable=repeats(child) or frozen,
                    )
                )
            elif is_xsd(child, "sequence", "choice", "all", "group"):
                child_ctx = ctx if kind != "choice" else False
                self._walk_compositor(child, model, key, frozen, child_ctx, seen_groups)
            elif is_xsd(child, "any"):
                model.has_wildcard = True

    # ------------------------------------------------------------------ divers

    def root_decl(self, qname: QName) -> Optional[ElementDecl]:
        return self.global_elements.get(qname)

    def find_root_by_local_name(self, name: str) -> List[ElementDecl]:
        return [d for q, d in self.global_elements.items() if q[1] == name]
