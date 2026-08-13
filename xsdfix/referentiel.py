"""Referentiel de valeurs : controle des donnees portees par les balises.

Le XSD dit quelles balises doivent exister et dans quel ordre ; il ne sait pas
qu'un numero de TVA vaut 3145 et non 11234. Cette source de verite la, c'est un
classeur fourni par l'utilisateur.

Deux difficultes traitees ici :

1. **Un nom de balise ne designe pas un emplacement.** `cbc:CompanyID` est le
   numero de TVA du vendeur sous `AccountingSupplierParty` et celui du client
   sous `AccountingCustomerParty`. Les regles portent donc sur des CHEMINS, avec
   trois niveaux de precision (exact, abrege, nom seul). Quand une regle vise
   plusieurs emplacements aux valeurs differentes, on ne devine pas : on signale
   l'ambiguite et on liste les chemins candidats.

2. **La bonne valeur depend souvent de la facture.** Une regle peut donc etre
   conditionnee par une cle : « si <PartyIdentification/ID> vaut 1084, alors
   <CompanyID> doit valoir FR55987654321 ». Sans cle, la regle vaut pour tous
   les fichiers (constantes : votre TVA, la devise, le ProfileID).

Le controle SIGNALE, il ne corrige pas : reecrire une donnee metier reste une
decision humaine.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from lxml import etree

# espace de noms du format SpreadsheetML (.xlsx)
XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

ANY_DEPTH = "**"


# --------------------------------------------------------------------- lecture

def _lire_xlsx(data: bytes) -> List[List[str]]:
    """Lit la premiere feuille d'un .xlsx. Un classeur est un ZIP de XML : la
    bibliotheque standard et lxml suffisent, ce qui evite une dependance
    absente de Pyodide."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        noms = archive.namelist()
        partages: List[str] = []
        if "xl/sharedStrings.xml" in noms:
            racine = etree.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in racine.iter(XLSX + "si"):
                partages.append("".join(t.text or "" for t in si.iter(XLSX + "t")))

        feuilles = sorted(n for n in noms if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not feuilles:
            raise ValueError("classeur sans feuille de calcul")
        feuille = etree.fromstring(archive.read(feuilles[0]))

        lignes: List[List[str]] = []
        for row in feuille.iter(XLSX + "row"):
            cellules: List[str] = []
            for cell in row.iter(XLSX + "c"):
                # la reference (A1, C3…) donne la colonne : on comble les trous
                colonne = _index_colonne(cell.get("r"))
                while colonne is not None and len(cellules) < colonne:
                    cellules.append("")
                valeur = cell.find(XLSX + "v")
                texte = valeur.text if valeur is not None else None
                if cell.get("t") == "s" and texte is not None:
                    index = int(texte)
                    texte = partages[index] if index < len(partages) else ""
                elif cell.get("t") == "inlineStr":
                    texte = "".join(t.text or "" for t in cell.iter(XLSX + "t"))
                cellules.append((texte or "").strip())
            lignes.append(cellules)
        return lignes


def _index_colonne(reference: Optional[str]) -> Optional[int]:
    """'C7' -> 2 (index a partir de zero)."""
    if not reference:
        return None
    lettres = re.match(r"([A-Z]+)", reference)
    if not lettres:
        return None
    index = 0
    for caractere in lettres.group(1):
        index = index * 26 + (ord(caractere) - 64)
    return index - 1


def _lire_csv(data: bytes) -> List[List[str]]:
    for encodage in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            texte = data.decode(encodage)
            break
        except UnicodeDecodeError:
            continue
    else:
        texte = data.decode("utf-8", "replace")
    # Excel francais ecrit en point-virgule, les exports anglo-saxons en virgule
    echantillon = texte[:4096]
    separateur = ";" if echantillon.count(";") >= echantillon.count(",") else ","
    return [[c.strip() for c in ligne]
            for ligne in csv.reader(io.StringIO(texte), delimiter=separateur)]


def lire_classeur(data: bytes, nom: str) -> List[List[str]]:
    if nom.lower().endswith((".xlsx", ".xlsm")):
        return _lire_xlsx(data)
    if nom.lower().endswith((".csv", ".txt")):
        return _lire_csv(data)
    # on tente le ZIP, puis le texte
    try:
        return _lire_xlsx(data)
    except Exception:
        return _lire_csv(data)


# --------------------------------------------------------------------- regles

@dataclass
class Regle:
    cible: str                       # chemin de la balise a controler
    attendu: str                     # valeur de reference
    cle_chemin: str = ""             # balise qui conditionne la regle
    cle_valeur: str = ""             # valeur que doit avoir cette balise
    commentaire: str = ""
    ligne: int = 0                   # ligne du classeur, pour les messages

    @property
    def conditionnelle(self) -> bool:
        return bool(self.cle_chemin and self.cle_valeur)


# intitules acceptes pour chaque colonne, en minuscules sans accents
_COLONNES = {
    "cible": ("chemin", "balise a controler", "balise", "element", "chemin cible",
              "cible", "path"),
    "attendu": ("valeur attendue", "valeur de reference", "attendu", "reference",
                "valeur correcte", "expected"),
    "cle_chemin": ("balise cle", "chemin cle", "cle", "condition", "si balise",
                   "key", "key path"),
    "cle_valeur": ("valeur cle", "vaut", "si valeur", "key value"),
    "commentaire": ("commentaire", "remarque", "note", "comment"),
    "actuel": ("valeur actuelle", "valeur trouvee", "actuel"),
}


def _normaliser(texte: str) -> str:
    texte = (texte or "").strip().lower()
    for accent, simple in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"),
                           ("ô", "o"), ("û", "u"), ("î", "i"), ("ç", "c")):
        texte = texte.replace(accent, simple)
    return re.sub(r"\s+", " ", texte)


def charger_regles(lignes: Sequence[Sequence[str]]) -> Tuple[List[Regle], List[str]]:
    """Reconnait les colonnes par leur intitule, quel que soit leur ordre."""
    problemes: List[str] = []
    if not lignes:
        return [], ["Le fichier de référence est vide."]

    entete: Optional[Dict[str, int]] = None
    depart = 0
    for index, ligne in enumerate(lignes[:10]):
        trouve: Dict[str, int] = {}
        for colonne, cellule in enumerate(ligne):
            nom = _normaliser(cellule)
            for champ, intitules in _COLONNES.items():
                if nom in intitules and champ not in trouve:
                    trouve[champ] = colonne
        if "cible" in trouve and "attendu" in trouve:
            entete, depart = trouve, index + 1
            break

    if entete is None:
        return [], ["Colonnes introuvables : il faut au minimum une colonne "
                    "« Chemin » et une colonne « Valeur attendue ». "
                    "Utilisez le modèle généré par l'application."]

    def cellule(ligne: Sequence[str], champ: str) -> str:
        index = entete.get(champ)
        if index is None or index >= len(ligne):
            return ""
        return (ligne[index] or "").strip()

    regles: List[Regle] = []
    for numero, ligne in enumerate(lignes[depart:], start=depart + 1):
        cible = cellule(ligne, "cible")
        attendu = cellule(ligne, "attendu")
        if not cible or not attendu:
            continue                       # ligne non renseignee : on l'ignore
        cle_chemin = cellule(ligne, "cle_chemin")
        cle_valeur = cellule(ligne, "cle_valeur")
        if bool(cle_chemin) != bool(cle_valeur):
            problemes.append(
                "Ligne %d : « %s » — la condition est incomplète (il faut la balise "
                "clé ET sa valeur). Règle ignorée." % (numero, cible))
            continue
        regles.append(Regle(cible=cible, attendu=attendu, cle_chemin=cle_chemin,
                            cle_valeur=cle_valeur,
                            commentaire=cellule(ligne, "commentaire"), ligne=numero))

    if not regles and not problemes:
        problemes.append("Aucune règle exploitable : la colonne « Valeur attendue » "
                         "est vide sur toutes les lignes.")
    return regles, problemes


# --------------------------------------------------------------------- chemins

def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


def chemin_de(el) -> str:
    """Chemin lisible d'un element, sans prefixe d'espace de noms."""
    morceaux = []
    noeud = el
    while noeud is not None and isinstance(noeud.tag, str):
        morceaux.append(_local(noeud.tag))
        noeud = noeud.getparent()
    return "/" + "/".join(reversed(morceaux))


def _motif(expression: str) -> Tuple[List[str], bool]:
    """Transforme « A//B/c:C » en (['A', '**', 'B', 'C'], ancre?)."""
    expression = (expression or "").strip()
    ancre = expression.startswith("/")
    brut = expression.replace("//", "/" + ANY_DEPTH + "/")
    jetons = [j.strip() for j in brut.split("/")]
    motif = []
    for jeton in jetons:
        if not jeton:
            continue
        motif.append(jeton if jeton == ANY_DEPTH else jeton.split(":")[-1])
    return motif, ancre


def _correspond(chemin: List[str], motif: List[str], i: int = 0, j: int = 0) -> bool:
    """Le motif doit se terminer exactement sur l'element vise."""
    if j == len(motif):
        return i == len(chemin)
    if motif[j] == ANY_DEPTH:
        return any(_correspond(chemin, motif, k, j + 1)
                   for k in range(i, len(chemin) + 1))
    if i < len(chemin) and chemin[i] == motif[j]:
        return _correspond(chemin, motif, i + 1, j + 1)
    return False


def trouver(racine, expression: str) -> List:
    """Elements vises par une expression de chemin."""
    motif, ancre = _motif(expression)
    if not motif:
        return []
    if not ancre:
        motif = [ANY_DEPTH] + motif
    resultats = []
    for el in racine.iter():
        if not isinstance(el.tag, str):
            continue
        chemin = []
        noeud = el
        while noeud is not None and isinstance(noeud.tag, str):
            chemin.append(_local(noeud.tag))
            noeud = noeud.getparent()
        chemin.reverse()
        if _correspond(chemin, motif):
            resultats.append(el)
    return resultats


# --------------------------------------------------------------------- controle

@dataclass
class Ecart:
    chemin: str
    actuel: str
    attendu: str
    commentaire: str = ""
    ligne: int = 0
    ambigu: bool = False
    candidats: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict:
        return {"chemin": self.chemin, "actuel": self.actuel, "attendu": self.attendu,
                "commentaire": self.commentaire, "ligne": self.ligne,
                "ambigu": self.ambigu, "candidats": self.candidats,
                "label": self.label}

    @property
    def label(self) -> str:
        if self.ambigu:
            return ("La règle ligne %d vise « %s », qui correspond à %d emplacements "
                    "portant des valeurs différentes. Précisez le chemin dans le "
                    "fichier de référence : %s"
                    % (self.ligne, self.chemin, len(self.candidats),
                       " ; ".join(self.candidats[:3])))
        base = "%s vaut « %s », le référentiel attend « %s »." % (
            self.chemin, self.actuel or "(vide)", self.attendu)
        return base + (" (%s)" % self.commentaire if self.commentaire else "")


def _texte(el) -> str:
    return (el.text or "").strip()


def controler(racine, regles: Sequence[Regle]) -> List[Ecart]:
    """Confronte un document au referentiel. Ne modifie rien."""
    ecarts: List[Ecart] = []
    for regle in regles:
        if regle.conditionnelle:
            porteurs = trouver(racine, regle.cle_chemin)
            if not any(_texte(el) == regle.cle_valeur for el in porteurs):
                continue                   # la regle ne concerne pas ce fichier
        cibles = trouver(racine, regle.cible)
        if not cibles:
            continue                       # balise absente : c'est au XSD de le dire
        valeurs = {_texte(el) for el in cibles}
        if len(cibles) > 1 and len(valeurs) > 1:
            ecarts.append(Ecart(chemin=regle.cible, actuel="", attendu=regle.attendu,
                                commentaire=regle.commentaire, ligne=regle.ligne,
                                ambigu=True,
                                candidats=[chemin_de(el) for el in cibles]))
            continue
        for el in cibles:
            if _texte(el) != regle.attendu:
                ecarts.append(Ecart(chemin=chemin_de(el), actuel=_texte(el),
                                    attendu=regle.attendu,
                                    commentaire=regle.commentaire, ligne=regle.ligne))
    return ecarts


# --------------------------------------------------------------------- modele

ENTETES = ["Chemin", "Valeur actuelle", "Balise clé", "Valeur clé",
           "Valeur attendue", "Commentaire"]


def collecter_valeurs(racines: Sequence) -> List[Tuple[str, str, int]]:
    """Recense les balises porteuses de texte : (chemin, valeur vue, nb de valeurs)."""
    vues: "Dict[str, List[str]]" = {}
    for racine in racines:
        for el in racine.iter():
            if not isinstance(el.tag, str) or len(el):
                continue
            texte = _texte(el)
            if not texte:
                continue
            vues.setdefault(chemin_de(el), []).append(texte)
    lignes = []
    for chemin, valeurs in vues.items():
        distinctes = list(dict.fromkeys(valeurs))
        lignes.append((chemin, distinctes[0], len(distinctes)))
    return sorted(lignes)


def _echapper(texte: str) -> str:
    return (str(texte).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _lettre(index: int) -> str:
    nom = ""
    index += 1
    while index:
        index, reste = divmod(index - 1, 26)
        nom = chr(65 + reste) + nom
    return nom


def generer_modele(racines: Sequence) -> bytes:
    """Produit un .xlsx pret a remplir : un ligne par balise, chemin et valeur
    actuelle deja renseignes. L'utilisateur n'a que la colonne « Valeur
    attendue » a completer, ce qui evite d'ecrire des chemins a la main."""
    lignes = [ENTETES]
    for chemin, valeur, nombre in collecter_valeurs(racines):
        commentaire = ("%d valeurs différentes selon les fichiers" % nombre
                       if nombre > 1 else "")
        lignes.append([chemin, valeur, "", "", "", commentaire])

    corps = []
    for numero, ligne in enumerate(lignes, start=1):
        cellules = []
        for colonne, valeur in enumerate(ligne):
            if valeur == "":
                continue
            cellules.append(
                '<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                % (_lettre(colonne), numero, _echapper(valeur)))
        corps.append('<row r="%d">%s</row>' % (numero, "".join(cellules)))

    feuille = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="60"/><col min="2" max="2" width="24"/>'
        '<col min="3" max="3" width="34"/><col min="4" max="4" width="14"/>'
        '<col min="5" max="5" width="24"/><col min="6" max="6" width="40"/></cols>'
        '<sheetData>%s</sheetData></worksheet>' % "".join(corps))

    classeur = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Référentiel" sheetId="1" r:id="rId1"/></sheets></workbook>')

    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats'
        '-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')

    rels_classeur = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", classeur)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_classeur)
        archive.writestr("xl/worksheets/sheet1.xml", feuille)
    return tampon.getvalue()
