"""Orchestration : reçoit des fichiers en memoire, renvoie un rapport complet."""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lxml import etree

from .corrector import Change, Options, correct, document_namespaces
from .schema_model import SchemaSet, split_tag
from .validator import CAT_ORDER, CAT_ROOT, CAT_UNEXPECTED, ValidationError, Validator

STATUS_VALID = "valid"        # deja conforme
STATUS_FIXED = "fixed"        # corrige et desormais conforme
STATUS_PARTIAL = "partial"    # ameliore mais encore des erreurs
STATUS_FAILED = "failed"      # aucune correction automatique possible
STATUS_ERROR = "error"        # XML illisible


@dataclass
class InputFile:
    name: str
    data: bytes


@dataclass
class FileReport:
    name: str
    status: str
    errors_before: List[ValidationError] = field(default_factory=list)
    errors_after: List[ValidationError] = field(default_factory=list)
    changes: List[Change] = field(default_factory=list)
    corrected: Optional[bytes] = None
    corrected_name: Optional[str] = None
    fatal: Optional[str] = None
    note: Optional[str] = None
    encoding: str = "UTF-8"

    def as_dict(self, include_content: bool = True) -> Dict:
        out = {
            "name": self.name,
            "status": self.status,
            "errorsBefore": [e.as_dict() for e in self.errors_before],
            "errorsAfter": [e.as_dict() for e in self.errors_after],
            "changes": [c.as_dict() for c in self.changes],
            "correctedName": self.corrected_name,
            "fatal": self.fatal,
            "note": self.note,
        }
        if include_content and self.corrected is not None:
            # l'apercu est decode avec l'encodage reel du document, sinon les
            # accents d'un fichier ISO-8859-1 seraient illisibles
            out["corrected"] = self.corrected.decode(self.encoding, errors="replace")
        return out


@dataclass
class AnalysisReport:
    results: List[FileReport] = field(default_factory=list)
    main_xsd: Optional[str] = None
    schema_error: Optional[str] = None
    schema_warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        counts = {"valid": 0, "fixed": 0, "partial": 0, "failed": 0, "error": 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return {
            "mainXsd": self.main_xsd,
            "schemaError": self.schema_error,
            "schemaWarnings": self.schema_warnings,
            "summary": counts,
            "results": [r.as_dict() for r in self.results],
        }


def _describe_roots(schema: SchemaSet) -> str:
    """Liste les racines acceptees par le XSD, pour un message d'erreur utile."""
    roots = sorted(schema.global_elements)
    if not roots:
        return ("Ce XSD ne déclare aucun élément global : il ne peut servir de schéma "
                "principal. Il s'agit sans doute d'un fichier de types importé par un "
                "autre XSD — fournissez le schéma principal.")
    shown = ["<%s>%s" % (local, " dans l'espace de noms « %s »" % ns if ns
                         else " (sans espace de noms)")
             for ns, local in roots[:6]]
    suite = " (et %d autre(s))" % (len(roots) - 6) if len(roots) > 6 else ""
    return "Ce XSD accepte comme racine : " + " ; ".join(shown) + suite + "."


def _namespace_mismatch_hint(schema: SchemaSet, doc_namespaces) -> str:
    """Explique le cas frequent « XSD sans targetNamespace / XML qualifie »."""
    used = sorted(ns for ns in doc_namespaces if ns)
    schema_ns = {ns for ns, _ in schema.global_elements if ns}
    if used and not schema_ns:
        return (" Votre XSD ne déclare aucun targetNamespace : il décrit des balises "
                "sans espace de noms. Votre fichier, lui, en utilise %d (%s). Les deux "
                "sont incompatibles : il vous faut le XSD correspondant à ces espaces "
                "de noms, et non celui-ci." % (len(used), ", ".join(used[:3])))
    return ""


def _refine(errors: List[ValidationError], schema: SchemaSet,
            root_qname=None, doc_namespaces=None) -> List[ValidationError]:
    """Affine les messages bruts de lxml avec ce que l'on sait du schema."""
    for error in errors:
        if error.category == CAT_ORDER and error.element:
            qname = (error.element_ns, error.element)
            if qname in schema.declared_qnames:
                continue                      # connue et bien qualifiée : c'est un ordre
            if error.element in schema.declared_names:
                # le nom existe, mais pas dans cet espace de noms : ce n'est pas
                # une balise inconnue, c'est un espace de noms qui ne colle pas
                autres = sorted({ns for ns, local in schema.declared_qnames
                                 if local == error.element and ns})
                error.category = CAT_UNEXPECTED
                error.label = (
                    "<%s> est bien déclaré dans le XSD, mais dans un autre espace de "
                    "noms%s. Votre fichier l'utilise %s." % (
                        error.element,
                        " (%s)" % ", ".join(autres[:2]) if autres else " (sans espace de noms)",
                        "dans « %s »" % error.element_ns if error.element_ns
                        else "sans espace de noms"))
            else:
                error.category = CAT_UNEXPECTED
                error.label = ("<%s> n'est déclaré nulle part dans le XSD : balise inconnue."
                               % error.element)
        elif error.category == CAT_ROOT and root_qname is not None:
            ns, local = root_qname
            trouve = ("dans l'espace de noms « %s »" % ns if ns
                      else "sans espace de noms")
            error.label = ("Le XSD n'accepte pas <%s> comme élément racine. "
                           "Votre fichier commence par <%s> %s. %s%s" %
                           (local, local, trouve, _describe_roots(schema),
                            _namespace_mismatch_hint(schema, doc_namespaces or set())))
    return errors


def _corrected_name(name: str) -> str:
    base, ext = os.path.splitext(os.path.basename(name))
    return "%s_corrige%s" % (base, ext or ".xml")


_RE_LOCATION = re.compile(r'schemaLocation\s*=\s*(["\'])(.*?)\1')


def safe_relpath(name: str) -> str:
    """Conserve l'arborescence fournie, sans jamais sortir du dossier de travail."""
    parts = [p for p in name.replace("\\", "/").split("/")
             if p and p not in (".", "..")]
    return os.path.join(*parts) if parts else "fichier.xsd"


def repair_schema_locations(workdir: str, written: List[str]) -> List[str]:
    """Rend les xs:import / xs:include resolubles, quelle que soit l'arborescence.

    Les schemas normalises (UBL, Factur-X…) s'importent par chemins relatifs
    (`../common/X.xsd`). Si l'utilisateur depose les fichiers a plat, ces chemins
    ne pointent nulle part. On les reecrit vers le fichier de meme nom
    effectivement fourni. Renvoie la liste des imports introuvables.
    """
    index = {os.path.basename(rel): rel for rel in written}
    missing: List[str] = []

    for rel in written:
        path = os.path.join(workdir, rel)
        try:
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as handle:
                text = handle.read()
        except OSError:
            continue

        def replace(match):
            quote, location = match.group(1), match.group(2)
            if not location or "://" in location:
                return match.group(0)
            target = os.path.normpath(os.path.join(os.path.dirname(rel), location))
            if os.path.exists(os.path.join(workdir, target)):
                return match.group(0)
            candidate = index.get(os.path.basename(location))
            if candidate is None:
                missing.append(os.path.basename(location))
                return match.group(0)
            fixed = os.path.relpath(os.path.join(workdir, candidate),
                                    os.path.dirname(path))
            return "schemaLocation=%s%s%s" % (quote, fixed, quote)

        repaired = _RE_LOCATION.sub(replace, text)
        if repaired != text:
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as handle:
                handle.write(repaired)

    return sorted(set(missing))


def pick_main_xsd(xsds: List[InputFile], preferred: Optional[str] = None) -> Optional[str]:
    """Determine le XSD principal : celui qui n'est importe/inclus par aucun autre."""
    if not xsds:
        return None
    names = [f.name for f in xsds]
    if preferred:
        if preferred in names:
            return preferred
        # tolere un nom donne sans son arborescence
        for name in names:
            if os.path.basename(name) == os.path.basename(preferred):
                return name
    if len(xsds) == 1:
        return names[0]

    referenced = set()
    pattern = re.compile(rb'schemaLocation\s*=\s*["\']([^"\']+)["\']')
    for item in xsds:
        for match in pattern.findall(item.data):
            referenced.add(os.path.basename(match.decode("utf-8", "replace")))
    roots = [n for n in names if os.path.basename(n) not in referenced]
    return roots[0] if roots else names[0]


class Session:
    """Un XSD compile une fois, puis autant de XML que l'on veut.

    Utile pour traiter un lot fichier par fichier (barre de progression) sans
    recompiler le schema a chaque appel, ce qui domine le temps de calcul.
    A refermer avec `close()` : les XSD sont ecrits dans un dossier temporaire
    pour que les `schemaLocation` relatifs se resolvent normalement.
    """

    def __init__(self, xsds: List[InputFile], options: Options,
                 preferred_xsd: Optional[str] = None):
        self.options = options
        self.error: Optional[str] = None
        self.warnings: List[str] = []
        self.main_xsd: Optional[str] = None
        self.workdir = tempfile.mkdtemp(prefix="xsdfix_")
        self._parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)

        if not xsds:
            self.error = "Aucun XSD fourni."
            return

        written: List[str] = []
        for item in xsds:
            relative = safe_relpath(item.name)
            path = os.path.join(self.workdir, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(item.data)
            written.append(relative)

        missing = repair_schema_locations(self.workdir, written)
        if missing:
            self.warnings.append(
                "schéma(s) importé(s) mais non fourni(s) : %s — ajoutez ces fichiers "
                "au dépôt du XSD, sinon la validation sera incomplète."
                % ", ".join(missing))

        self.main_xsd = pick_main_xsd(xsds, preferred_xsd)
        main_path = os.path.join(self.workdir, safe_relpath(self.main_xsd))

        self.validator = Validator(main_path)
        if not self.validator.ok:
            hint = (" — cause probable : %s" % self.warnings[0]) if missing else ""
            self.error = "XSD invalide : %s%s" % (self.validator.error, hint)
            return
        self.schema = SchemaSet(main_path)
        self.warnings.extend(self.schema.load_errors)

    @property
    def ok(self) -> bool:
        return self.error is None

    def check(self, item: InputFile) -> FileReport:
        """Valide puis corrige un seul XML."""
        result = FileReport(name=item.name, status=STATUS_ERROR)
        try:
            tree = etree.fromstring(item.data, self._parser).getroottree()
        except etree.XMLSyntaxError as exc:
            result.fatal = "XML mal formé (impossible à lire) : %s" % exc
            return result

        result.encoding = tree.docinfo.encoding or "UTF-8"
        root_qname = split_tag(tree.getroot().tag)
        namespaces = document_namespaces(tree.getroot())
        result.errors_before = _refine(self.validator.validate(tree), self.schema,
                                       root_qname, namespaces)
        if not result.errors_before:
            result.status = STATUS_VALID
            return result

        try:
            corrected, changes = correct(item.data, self.schema, self.validator, self.options)
        except Exception as exc:  # une correction ne doit jamais faire tomber le lot
            result.status = STATUS_FAILED
            result.fatal = "Correction impossible : %s" % exc
            return result

        result.changes = changes
        try:
            new_tree = etree.fromstring(corrected, self._parser).getroottree()
            result.errors_after = _refine(self.validator.validate(new_tree), self.schema,
                                          split_tag(new_tree.getroot().tag),
                                          document_namespaces(new_tree.getroot()))
        except etree.XMLSyntaxError as exc:
            result.status = STATUS_FAILED
            result.fatal = "Le fichier corrigé n'est pas relisible : %s" % exc
            return result

        # Attention : on ne compare surtout pas le NOMBRE d'erreurs avant/apres.
        # Quand la racine est rejetee, lxml s'arrete immediatement et ne signale
        # qu'une seule erreur ; corriger la racine fait apparaitre toutes celles
        # qu'elle masquait. Le compte augmente alors que le fichier s'ameliore.
        if not result.errors_after:
            result.status = STATUS_FIXED
        elif changes:
            result.status = STATUS_PARTIAL
        else:
            result.status = STATUS_FAILED

        if changes:
            result.corrected = corrected
            result.corrected_name = _corrected_name(item.name)

        if any(e.category == CAT_ROOT for e in result.errors_before) and result.errors_after:
            result.note = (
                "L'élément racine était rejeté : le validateur s'arrête là et ne peut "
                "pas examiner le contenu du fichier. Les erreurs ci-dessous n'ont pu "
                "être découvertes qu'une fois la racine corrigée — elles étaient déjà "
                "présentes dans le fichier d'origine.")
        return result

    def close(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def analyze(xsds: List[InputFile], xmls: List[InputFile], options: Options,
            preferred_xsd: Optional[str] = None) -> AnalysisReport:
    """Valide et corrige un lot de XML face a un jeu de XSD."""
    report = AnalysisReport()
    if not xmls:
        report.schema_error = "Aucun XML fourni."
        return report

    session = Session(xsds, options, preferred_xsd)
    try:
        report.main_xsd = session.main_xsd
        if not session.ok:
            report.schema_error = session.error
            return report
        report.schema_warnings = session.warnings
        for item in xmls:
            report.results.append(session.check(item))
        return report
    finally:
        session.close()


def build_zip(report: AnalysisReport) -> bytes:
    """Archive de tous les fichiers corriges + un rapport texte."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        lines = ["Rapport de conformité XML / XSD",
                 "XSD de référence : %s" % (report.main_xsd or "?"), ""]
        for result in report.results:
            if result.corrected is not None and result.corrected_name:
                archive.writestr(result.corrected_name, result.corrected)
            lines.append("- %s : %s" % (result.name, {
                STATUS_VALID: "conforme, aucune correction nécessaire",
                STATUS_FIXED: "corrigé, désormais conforme",
                STATUS_PARTIAL: "partiellement corrigé",
                STATUS_FAILED: "non corrigeable automatiquement",
                STATUS_ERROR: "illisible",
            }.get(result.status, result.status)))
            for change in result.changes:
                lines.append("    · [%s] %s : %s" % (change.kind, change.path, change.detail))
            for error in result.errors_after:
                lines.append("    ! reste : %s" % error.label)
        archive.writestr("rapport.txt", "\n".join(lines).encode("utf-8"))
    return buffer.getvalue()
