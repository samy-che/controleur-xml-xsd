"""Frontiere entre le moteur Python et le navigateur (Pyodide).

Le JavaScript ne manipule que des chaines JSON et du base64 : tout le reste
(validation, correction, archive ZIP) reste ici, ce qui garde une seule
implementation, testable en local comme dans le navigateur.

Cycle d'appel cote JS :
    webapi.open_session(json)   -> prepare le XSD, une seule fois
    webapi.check_one(nom, b64)  -> traite un XML, renvoie son resultat
    webapi.finish()             -> synthese du lot
    webapi.zip_base64()         -> archive de tous les fichiers corriges
"""

from __future__ import annotations

import base64
import json
from typing import Dict, List, Optional

from .corrector import Options
from .service import AnalysisReport, InputFile, Session, build_zip

_session: Optional[Session] = None
_report: AnalysisReport = AnalysisReport()


def _decode(entries) -> List[InputFile]:
    out = []
    for entry in entries or []:
        out.append(InputFile(name=str(entry.get("name", "sans-nom")),
                             data=base64.b64decode(entry.get("content", ""))))
    return out


def open_session(payload_json: str) -> str:
    """Compile le XSD. Renvoie {"ok": bool, "error": str, "warnings": [...]}"""
    global _session, _report
    close_session()

    payload = json.loads(payload_json)
    options = Options.from_dict(payload.get("options"))
    _session = Session(_decode(payload.get("xsd")), options, payload.get("mainXsd"))
    _report = AnalysisReport(main_xsd=_session.main_xsd)

    if not _session.ok:
        _report.schema_error = _session.error
        return json.dumps({"ok": False, "error": _session.error}, ensure_ascii=False)
    _report.schema_warnings = _session.warnings
    return json.dumps({"ok": True, "mainXsd": _session.main_xsd,
                       "warnings": _session.warnings}, ensure_ascii=False)


def check_one(name: str, content_b64: str) -> str:
    """Traite un XML et renvoie son rapport (avec le corrige en base64)."""
    if _session is None or not _session.ok:
        return json.dumps({"error": "Aucune session ouverte."})
    result = _session.check(InputFile(name=name, data=base64.b64decode(content_b64)))
    _report.results.append(result)

    payload: Dict = result.as_dict()
    # base64 : le telechargement doit restituer les octets exacts, encodage
    # d'origine compris (ISO-8859-1 et autres)
    payload["correctedB64"] = (base64.b64encode(result.corrected).decode("ascii")
                               if result.corrected is not None else None)
    payload["encoding"] = result.encoding
    return json.dumps(payload, ensure_ascii=False)


def finish() -> str:
    """Synthese du lot (compteurs par statut)."""
    return json.dumps(_report.as_dict()["summary"], ensure_ascii=False)


def zip_base64() -> str:
    """Archive ZIP de tous les corriges + rapport.txt, en base64."""
    if not _report.results:
        return ""
    return base64.b64encode(build_zip(_report)).decode("ascii")


def close_session() -> None:
    global _session
    if _session is not None:
        _session.close()
        _session = None
