#!/usr/bin/env python3
"""Convertit un XSD « à plat » en un vrai XSD à espaces de noms (ligne de commande).

La même conversion est disponible en un clic dans le site : elle n'est proposée
que lorsque l'application reconnaît un schéma de ce type. Ce script sert aux
traitements par lots et aux scripts.

Les générateurs qui déduisent un XSD depuis un XML d'exemple (Liquid
Technologies, XmlGrid, FreeFormatter…) ne gèrent pas les espaces de noms : ils
transforment `cbc:UBLVersionID` en un nom LITTÉRAL `cbc.UBLVersionID` et
suppriment le targetNamespace. Le schéma obtenu ne peut plus valider le XML
dont il est issu.

    python3 convertir_xsd.py mon-schema.xsd --depuis-xml une-facture.xml --out schema-converti/

Les espaces de noms sont appris depuis les déclarations `xmlns:` du XML fourni,
ou indiqués à la main :

    python3 convertir_xsd.py mon-schema.xsd --out dossier/ \\
        --ns ubl=urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 \\
        --ns cbc=urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from xsdfix.flat_schema import Converter, looks_flat, namespaces_from_xml
except ImportError as exc:
    sys.exit("Dépendance manquante : %s\nInstallez-la avec :  pip3 install lxml" % exc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convertit un XSD généré « à plat » en XSD à espaces de noms.")
    parser.add_argument("xsd", help="le XSD à convertir")
    parser.add_argument("--out", default="xsd-converti", help="dossier de sortie")
    parser.add_argument("--depuis-xml", help="XML d'où lire les espaces de noms")
    parser.add_argument("--ns", action="append", default=[],
                        metavar="prefixe=uri", help="espace de noms explicite")
    args = parser.parse_args()

    with open(args.xsd, "rb") as handle:
        data = handle.read()
    if looks_flat(data) is None:
        print("Ce XSD ne ressemble pas à un schéma généré « à plat » "
              "(il a un targetNamespace, ou ses noms ne portent pas de préfixe collé).\n"
              "La conversion n'a probablement pas lieu d'être.", file=sys.stderr)

    namespaces: Dict[str, str] = {}
    if args.depuis_xml:
        namespaces.update(namespaces_from_xml(args.depuis_xml))
    for pair in args.ns:
        if "=" not in pair:
            print("Format attendu : --ns prefixe=uri", file=sys.stderr)
            return 2
        prefix, uri = pair.split("=", 1)
        namespaces[prefix] = uri
    if not namespaces:
        print("Indiquez les espaces de noms avec --depuis-xml ou --ns.", file=sys.stderr)
        return 2

    converter = Converter(namespaces)
    converter.read(args.xsd)
    merged = converter.merge()

    inconnus = sorted(converter.unknown_prefixes - set(namespaces))
    if inconnus:
        print("Préfixes sans espace de noms connu (éléments ignorés) : %s"
              % ", ".join(inconnus), file=sys.stderr)

    written = converter.write(merged, args.out)

    print("Éléments convertis : %d" % len(merged))
    if converter.root:
        print("Racine             : <%s> dans %s"
              % (converter.root[1], namespaces.get(converter.root[0], "?")))
    for path in written:
        print("  écrit : %s" % path)
    if converter.conflicts:
        print("\nRéconciliations effectuées (%d) :" % len(converter.conflicts))
        for line in converter.conflicts[:15]:
            print("  · %s" % line)
        if len(converter.conflicts) > 15:
            print("  · … et %d autres" % (len(converter.conflicts) - 15))
    print("\nDéposez tous les fichiers de %s/ dans le contrôleur XML / XSD,"
          % args.out.rstrip("/"))
    print("en désignant %s.xsd comme schéma principal."
          % (converter.root[0] if converter.root else "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
