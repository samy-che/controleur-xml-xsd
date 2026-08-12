#!/usr/bin/env python3
"""Contrôle et correction XML / XSD en ligne de commande.

    python3 cli.py --xsd facture.xsd --out corriges/ factures/*.xml
    python3 cli.py --xsd facture.xsd --ajouter-manquants --supprimer-inconnus *.xml

Code de sortie : 0 si tous les fichiers sont conformes (avant ou apres
correction), 1 s'il reste au moins un fichier non conforme.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from xsdfix.corrector import Options
    from xsdfix.service import (
        STATUS_FIXED,
        STATUS_PARTIAL,
        STATUS_VALID,
        InputFile,
        analyze,
    )
except ImportError as exc:  # lxml absent
    sys.exit("Dépendance manquante : %s\nInstallez-la avec :  pip3 install lxml" % exc)

MARK = {
    STATUS_VALID: "  OK    ",
    STATUS_FIXED: "  CORRIGÉ",
    STATUS_PARTIAL: "  PARTIEL",
    "failed": "  ÉCHEC ",
    "error": "  ILLISIBLE",
}


def load(paths):
    out = []
    for pattern in paths:
        matches = glob.glob(pattern) if any(c in pattern for c in "*?[") else [pattern]
        for path in sorted(matches):
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    out.append(InputFile(os.path.basename(path), handle.read()))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie des XML face à un XSD et génère les fichiers corrigés.")
    parser.add_argument("xml", nargs="+", help="fichiers XML (jokers acceptés)")
    parser.add_argument("--xsd", required=True, action="append",
                        help="schéma XSD de référence (répéter si plusieurs)")
    parser.add_argument("--out", default="corriges", help="dossier de sortie")
    parser.add_argument("--sans-reordonner", action="store_true")
    parser.add_argument("--sans-namespace", action="store_true")
    parser.add_argument("--ajouter-manquants", action="store_true",
                        help="insère les éléments obligatoires absents (vides), chacun "
                             "précédé d'un commentaire signalant l'ajout")
    parser.add_argument("--sans-commentaires", action="store_true",
                        help="n'accole pas de commentaire aux balises ajoutées")
    parser.add_argument("--supprimer-inconnus", action="store_true",
                        help="retire les éléments absents du XSD (perte de données)")
    parser.add_argument("--verbeux", "-v", action="store_true")
    args = parser.parse_args()

    xsds = load(args.xsd)
    xmls = load(args.xml)
    if not xsds:
        print("XSD introuvable.", file=sys.stderr)
        return 2
    if not xmls:
        print("Aucun XML trouvé.", file=sys.stderr)
        return 2

    options = Options(
        reorder=not args.sans_reordonner,
        fix_namespace=not args.sans_namespace,
        insert_missing=args.ajouter_manquants,
        remove_unknown=args.supprimer_inconnus,
        comment_inserted=not args.sans_commentaires,
    )
    report = analyze(xsds, xmls, options)
    if report.schema_error:
        print(report.schema_error, file=sys.stderr)
        return 2
    for warning in report.schema_warnings:
        print("Avertissement : %s" % warning, file=sys.stderr)

    written = 0
    for result in report.results:
        print("%s  %s" % (MARK.get(result.status, result.status), result.name))
        if args.verbeux:
            for error in result.errors_before:
                print("        · %s" % error.label)
        for change in result.changes:
            print("        → %s  [%s]" % (change.detail, change.path))
        for error in result.errors_after:
            print("        ! reste : %s" % error.label)
        if result.fatal:
            print("        ! %s" % result.fatal)
        if result.corrected is not None:
            os.makedirs(args.out, exist_ok=True)
            target = os.path.join(args.out, result.corrected_name)
            with open(target, "wb") as handle:
                handle.write(result.corrected)
            written += 1
            print("        ↳ %s" % target)

    counts = report.as_dict()["summary"]
    print("\n%d fichier(s) : %d conforme(s), %d corrigé(s), %d partiel(s), %d en échec."
          % (len(report.results), counts["valid"], counts["fixed"],
             counts["partial"], counts["failed"] + counts["error"]))
    if written:
        print("%d fichier(s) écrit(s) dans %s/" % (written, args.out.rstrip("/")))

    non_conformes = counts["partial"] + counts["failed"] + counts["error"]
    return 1 if non_conformes else 0


if __name__ == "__main__":
    sys.exit(main())
