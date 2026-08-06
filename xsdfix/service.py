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

from .corrector import Change, Options, correct
from .schema_model import SchemaSet, split_tag
from .validator import CAT_ORDER, CAT_UNEXPECTED, ValidationError, Validator

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


def _refine(errors: List[ValidationError], schema: SchemaSet) -> List[ValidationError]:
    """lxml signale de la meme facon une balise mal placee et une balise inconnue.
    On tranche en regardant si le nom existe quelque part dans le XSD."""
    for error in errors:
        if error.category == CAT_ORDER and error.element:
            if error.element not in schema.declared_names:
                error.category = CAT_UNEXPECTED
                error.label = ("<%s> n'est déclaré nulle part dans le XSD : balise inconnue."
                               % error.element)
    return errors


def _corrected_name(name: str) -> str:
    base, ext = os.path.splitext(os.path.basename(name))
    return "%s_corrige%s" % (base, ext or ".xml")


def pick_main_xsd(xsds: List[InputFile], preferred: Optional[str] = None) -> Optional[str]:
    """Determine le XSD principal : celui qui n'est importe/inclus par aucun autre."""
    if not xsds:
        return None
    names = [f.name for f in xsds]
    if preferred and preferred in names:
        return preferred
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

        for item in xsds:
            path = os.path.join(self.workdir, os.path.basename(item.name))
            with open(path, "wb") as handle:
                handle.write(item.data)

        self.main_xsd = pick_main_xsd(xsds, preferred_xsd)
        main_path = os.path.join(self.workdir, os.path.basename(self.main_xsd))

        self.validator = Validator(main_path)
        if not self.validator.ok:
            self.error = "XSD invalide : %s" % self.validator.error
            return
        self.schema = SchemaSet(main_path)
        self.warnings = list(self.schema.load_errors)

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
        result.errors_before = _refine(self.validator.validate(tree), self.schema)
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
            result.errors_after = _refine(self.validator.validate(new_tree), self.schema)
        except etree.XMLSyntaxError as exc:
            result.status = STATUS_FAILED
            result.fatal = "Le fichier corrigé n'est pas relisible : %s" % exc
            return result

        if not result.errors_after:
            result.status = STATUS_FIXED
            result.corrected = corrected
            result.corrected_name = _corrected_name(item.name)
        elif changes and len(result.errors_after) < len(result.errors_before):
            result.status = STATUS_PARTIAL
            result.corrected = corrected
            result.corrected_name = _corrected_name(item.name)
        else:
            # aucune amelioration mesurable : on ne propose pas de fichier corrige,
            # mieux vaut rendre la main que livrer un XML douteux
            result.status = STATUS_FAILED
            result.errors_after = []      # la liste « erreurs détectées » suffit
            result.changes = []
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
