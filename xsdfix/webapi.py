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

from lxml import etree

from .corrector import Options
from .flat_schema import compile_check, convert, looks_flat, namespaces_from_root
from .referentiel import (charger_regles, collecter_valeurs, generer_modele,
                          lire_classeur)
from .service import AnalysisReport, InputFile, Session, build_zip

_session: Optional[Session] = None
_report: AnalysisReport = AnalysisReport()
_regles: List = []            # regles du referentiel, conservees entre les lots


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
    _session = Session(_decode(payload.get("xsd")), options,
                       payload.get("mainXsd"), regles=_regles)
    _report = AnalysisReport(main_xsd=_session.main_xsd)

    if not _session.ok:
        _report.schema_error = _session.error
        return json.dumps({"ok": False, "error": _session.error}, ensure_ascii=False)
    _report.schema_warnings = _session.warnings
    return json.dumps({"ok": True, "mainXsd": _session.main_xsd,
                       "warnings": _session.warnings,
                       "rules": len(_regles)}, ensure_ascii=False)


def load_referentiel(payload_json: str) -> str:
    """Charge le classeur de référence. Renvoie le nombre de règles et les soucis."""
    global _regles
    payload = json.loads(payload_json)
    entry = payload.get("file")
    if not entry:
        _regles = []
        return json.dumps({"ok": True, "rules": 0})
    try:
        lignes = lire_classeur(base64.b64decode(entry.get("content", "")),
                               str(entry.get("name", "")))
    except Exception as exc:
        _regles = []
        return json.dumps({"ok": False,
                           "error": "Fichier de référence illisible : %s" % exc},
                          ensure_ascii=False)
    _regles, problemes = charger_regles(lignes)
    return json.dumps({"ok": bool(_regles), "rules": len(_regles),
                       "problems": problemes,
                       "error": problemes[0] if (problemes and not _regles) else None},
                      ensure_ascii=False)


def template_base64(payload_json: str) -> str:
    """Modèle Excel pré-rempli à partir des XML fournis."""
    payload = json.loads(payload_json)
    documents = []
    for entry in payload.get("xml") or []:
        try:
            racine = etree.fromstring(base64.b64decode(entry.get("content", "")))
        except etree.XMLSyntaxError:
            continue
        documents.append((str(entry.get("name", "")), racine))
    if not documents:
        return json.dumps({"ok": False,
                           "error": "Aucun XML lisible pour construire le modèle."})
    data = generer_modele(documents)
    return json.dumps({"ok": True,
                       "sheets": len(documents) + (1 if len(documents) > 1 else 0),
                       "rows": len(collecter_valeurs([r for _, r in documents])),
                       "content": base64.b64encode(data).decode("ascii")})


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


def inspect_xsd(payload_json: str) -> str:
    """Signale un XSD généré « à plat », avant même de tenter une validation."""
    payload = json.loads(payload_json)
    for entry in payload.get("xsd") or []:
        info = looks_flat(base64.b64decode(entry.get("content", "")))
        if info is not None:
            return json.dumps({"flat": True, "file": entry.get("name"),
                               "prefixes": info["prefixes"],
                               "prefixed": info["prefixed"],
                               "total": info["total"]}, ensure_ascii=False)
    return json.dumps({"flat": False})


def convert_flat(payload_json: str) -> str:
    """Convertit un XSD à plat, en apprenant les espaces de noms du XML fourni.

    Renvoie les fichiers produits (base64) et la liste des arbitrages, ou une
    erreur explicite si les espaces de noms restent introuvables.
    """
    payload = json.loads(payload_json)
    entries = payload.get("xsd") or []
    target = None
    for entry in entries:
        data = base64.b64decode(entry.get("content", ""))
        if looks_flat(data) is not None:
            target = (entry.get("name"), data)
            break
    if target is None:
        return json.dumps({"ok": False, "error": "Aucun schéma « à plat » à convertir."})

    sample = payload.get("sampleXml")
    if not sample:
        return json.dumps({"ok": False,
                           "error": "Un XML est nécessaire pour retrouver les espaces de noms."})
    try:
        root = etree.fromstring(base64.b64decode(sample))
    except etree.XMLSyntaxError as exc:
        return json.dumps({"ok": False, "error": "XML illisible : %s" % exc})

    namespaces = namespaces_from_root(root)
    if not namespaces:
        return json.dumps({"ok": False, "error":
                           "Ce XML ne déclare aucun espace de noms : il n'y a rien à rétablir."})

    relax = bool(payload.get("relaxTypes", True))
    fichiers, racine, conflits = convert(target[1], namespaces, relax_types=relax)
    if not fichiers:
        return json.dumps({"ok": False, "error":
                           "Conversion impossible : aucun préfixe du XSD ne correspond "
                           "aux espaces de noms du XML."})

    main_name = "%s.xsd" % racine[0] if racine else fichiers[0][0]
    probleme = compile_check(fichiers, main_name)
    if probleme:
        # on ne remet jamais un schéma cassé entre les mains de l'utilisateur
        return json.dumps({"ok": False, "error":
                           "Le schéma converti ne compile pas : %s" % probleme,
                           "conflicts": conflits}, ensure_ascii=False)

    return json.dumps({
        "ok": True,
        "source": target[0],
        "mainXsd": main_name,
        "relaxed": relax,
        "conflicts": conflits,
        "files": [{"name": name,
                   "content": base64.b64encode(data).decode("ascii")}
                  for name, data in fichiers],
    }, ensure_ascii=False)


def close_session() -> None:
    global _session
    if _session is not None:
        _session.close()
        _session = None
