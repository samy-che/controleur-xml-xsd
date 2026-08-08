#!/usr/bin/env python3
"""Diagnostic XSD / XML, sans divulguer de donnees.

Quand un fichier est confidentiel, ce script en extrait uniquement la
STRUCTURE, afin de pouvoir la partager pour analyse :

  - espaces de noms declares (ce sont des identifiants techniques normalises) ;
  - noms des elements globaux du XSD ;
  - nom de la racine du XML et de ses premiers niveaux ;
  - resultat de la confrontation entre les deux.

Ne sont JAMAIS lus ni affiches : le texte des elements, les valeurs
d'attributs, les commentaires. Aucun montant, aucun nom de societe, aucune
adresse ne peut sortir d'ici.

    python3 diagnostic.py --xsd schema.xsd facture.xml
    python3 diagnostic.py --xsd maindoc/Invoice.xsd --xsd common/*.xsd facture.xml

Relisez la sortie avant de la partager : si les noms de balises de votre
schema sont eux-memes sensibles, utilisez --noms-masques.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import sys

try:
    from lxml import etree
except ImportError:
    sys.exit("lxml est requis :  pip3 install lxml")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xsdfix.schema_model import XSD_NS, split_tag       # noqa: E402
from xsdfix.service import InputFile, Session           # noqa: E402
from xsdfix.corrector import Options, document_namespaces  # noqa: E402

MASK = False


def name(text: str) -> str:
    """Masque un nom si demande, de facon stable (meme nom -> meme code)."""
    if not MASK or not text:
        return text
    return "nom_" + hashlib.sha256(text.encode()).hexdigest()[:6]


def ns_label(namespace) -> str:
    return "« %s »" % namespace if namespace else "(sans espace de noms)"


def load(patterns):
    files = []
    for pattern in patterns:
        matches = glob.glob(pattern) if any(c in pattern for c in "*?[") else [pattern]
        for path in sorted(matches):
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    files.append(InputFile(os.path.basename(path), handle.read()))
    return files


def describe_xsd(files):
    print("SCHÉMA XSD")
    print("  fichiers fournis : %d" % len(files))
    for item in files:
        try:
            root = etree.fromstring(item.data)
        except etree.XMLSyntaxError as exc:
            print("   - %s : ILLISIBLE (%s)" % (item.name, str(exc)[:60]))
            continue
        if split_tag(root.tag) != (XSD_NS, "schema"):
            print("   - %s : ce n'est pas un schéma XSD (racine <%s>)"
                  % (item.name, split_tag(root.tag)[1]))
            continue
        tns = root.get("targetNamespace")
        globaux = [c.get("name") for c in root
                   if isinstance(c.tag, str) and split_tag(c.tag) == (XSD_NS, "element")
                   and c.get("name")]
        imports = [(c.get("namespace"), c.get("schemaLocation")) for c in root
                   if isinstance(c.tag, str)
                   and split_tag(c.tag)[1] in ("import", "include", "redefine")]
        print("   - %s" % item.name)
        print("       targetNamespace   : %s" % (tns or "AUCUN"))
        print("       elementFormDefault: %s" % root.get("elementFormDefault", "unqualified"))
        print("       éléments globaux  : %s" % (
            ", ".join("<%s>" % name(g) for g in globaux[:8]) +
            (" (et %d autres)" % (len(globaux) - 8) if len(globaux) > 8 else "")
            if globaux else "aucun"))
        for namespace, location in imports:
            fourni = "fourni" if any(
                os.path.basename(location or "") == f.name for f in files) else "ABSENT"
            print("       import            : %s  ←  %s  [%s]"
                  % (namespace or "(sans ns)", location, fourni))


def describe_xml(path, depth=2):
    print("\nFICHIER XML : %s" % os.path.basename(path))
    try:
        tree = etree.parse(path)
    except etree.XMLSyntaxError as exc:
        print("  ILLISIBLE : %s" % exc)
        return None
    root = tree.getroot()
    ns, local = split_tag(root.tag)
    print("  racine            : <%s> %s" % (name(local), ns_label(ns)))
    print("  préfixes déclarés : %s" % ", ".join(
        "%s → %s" % (prefix or "(défaut)", uri) for prefix, uri in root.nsmap.items()) or "aucun")
    used = sorted(x for x in document_namespaces(root) if x)
    print("  espaces de noms réellement utilisés (%d) :" % len(used))
    for item in used:
        print("      %s" % item)

    print("  structure (noms seuls, %d niveaux, sans aucune valeur) :" % depth)

    def walk(node, level):
        if level > depth:
            return
        seen = []
        for child in node:
            if not isinstance(child.tag, str):
                continue
            child_ns, child_local = split_tag(child.tag)
            key = (child_ns, child_local)
            if key in seen:
                continue
            seen.append(key)
            count = sum(1 for x in node if isinstance(x.tag, str)
                        and split_tag(x.tag) == key)
            print("      %s<%s>%s%s" % ("  " * level, name(child_local),
                                        "  ×%d" % count if count > 1 else "",
                                        "" if child_ns == split_tag(node.tag)[0]
                                        else "   " + ns_label(child_ns)))
            walk(child, level + 1)

    walk(root, 1)
    return root


def verdict(xsd_files, xml_root):
    print("\nCONFRONTATION")
    session = Session(xsd_files, Options())
    try:
        if not session.ok:
            print("  Le XSD ne compile pas : %s" % session.error)
            return
        for warning in session.warnings:
            print("  Avertissement : %s" % warning)

        schema = session.schema
        racines = sorted(schema.global_elements)
        ns, local = split_tag(xml_root.tag)
        print("  racine du XML     : <%s> %s" % (name(local), ns_label(ns)))
        print("  racines du XSD    : %s" % ", ".join(
            "<%s> %s" % (name(l), ns_label(n)) for n, l in racines[:5]) or "aucune")

        if (ns, local) in schema.global_elements:
            print("  → la racine correspond. La validation peut examiner le contenu.")
        elif any(l == local for _, l in racines):
            attendus = sorted({n for n, l in racines if l == local})
            print("  → MÊME NOM, ESPACE DE NOMS DIFFÉRENT.")
            print("      le XML utilise  : %s" % ns_label(ns))
            print("      le XSD attend   : %s" % ", ".join(ns_label(x) for x in attendus))
        else:
            print("  → aucune racine du XSD ne porte ce nom : ce n'est pas le bon schéma.")

        ns_xml = {x for x in document_namespaces(xml_root) if x}
        ns_xsd = {n for n, _ in schema.declared_qnames if n}
        manquants = sorted(ns_xml - ns_xsd)
        if manquants:
            print("  → espaces de noms présents dans le XML mais absents du XSD :")
            for item in manquants:
                print("      %s" % item)
            print("      (il manque probablement les schémas qui les déclarent)")
        elif ns_xml:
            print("  → tous les espaces de noms du XML sont couverts par le XSD.")
    finally:
        session.close()


def main() -> int:
    global MASK
    parser = argparse.ArgumentParser(
        description="Extrait la structure d'un XSD et d'un XML, sans aucune donnée.")
    parser.add_argument("xml", help="un fichier XML représentatif")
    parser.add_argument("--xsd", required=True, action="append",
                        help="schéma XSD (répéter ou utiliser des jokers)")
    parser.add_argument("--profondeur", type=int, default=2)
    parser.add_argument("--noms-masques", action="store_true",
                        help="remplace les noms de balises par des codes stables")
    args = parser.parse_args()
    MASK = args.noms_masques

    xsd_files = load(args.xsd)
    if not xsd_files:
        print("Aucun XSD trouvé.", file=sys.stderr)
        return 2

    print("=" * 72)
    print("DIAGNOSTIC — structure uniquement, aucune valeur n'est lue")
    print("=" * 72)
    describe_xsd(xsd_files)
    root = describe_xml(args.xml, args.profondeur)
    if root is not None:
        verdict(xsd_files, root)
    print("\n" + "=" * 72)
    print("Relisez cette sortie avant de la partager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
