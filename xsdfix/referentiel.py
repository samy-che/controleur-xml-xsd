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

2. **La bonne valeur depend souvent de la facture.** Le classeur porte donc un
   onglet par fichier : une regle qui y figure ne vaut que pour lui, et prime
   sur l'onglet commun. Pour une regle durable, independante du nom de fichier,
   les colonnes « Balise cle » / « Valeur cle » conditionnent une regle a la
   valeur d'une autre balise (« si Client/Code vaut 1084, alors… »).

Les ecarts sont ecrits dans le fichier corrige, chacun signale par un
commentaire rappelant l'ancienne valeur — sauf les regles ambigues, jamais
appliquees : l'emplacement a corriger ne se devine pas. L'option
`apply_referentiel=False` revient a un simple signalement.
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

REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _lire_xlsx(data: bytes) -> List[Tuple[str, List[List[str]]]]:
    """Lit TOUTES les feuilles d'un .xlsx, dans l'ordre du classeur.

    Un classeur est un ZIP de XML : la bibliotheque standard et lxml suffisent,
    ce qui evite openpyxl, absent de Pyodide. Le nom des feuilles compte ici :
    il rattache un jeu de regles a une facture precise.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        noms = archive.namelist()
        partages: List[str] = []
        if "xl/sharedStrings.xml" in noms:
            racine = etree.fromstring(archive.read("xl/sharedStrings.xml"))
            for si in racine.iter(XLSX + "si"):
                partages.append("".join(t.text or "" for t in si.iter(XLSX + "t")))

        # workbook.xml donne les noms et l'ordre ; les rels donnent les fichiers
        cibles: Dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in noms:
            rels = etree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                cible = rel.get("Target") or ""
                cibles[rel.get("Id")] = "xl/" + cible.lstrip("/").replace("../", "")

        ordre: List[Tuple[str, str]] = []
        if "xl/workbook.xml" in noms:
            classeur = etree.fromstring(archive.read("xl/workbook.xml"))
            for sheet in classeur.iter(XLSX + "sheet"):
                chemin = cibles.get(sheet.get(REL + "id"))
                if chemin and chemin in noms:
                    ordre.append((sheet.get("name") or "", chemin))
        if not ordre:
            ordre = [("", n) for n in
                     sorted(n for n in noms
                            if re.match(r"xl/worksheets/sheet\d+\.xml$", n))]
        if not ordre:
            raise ValueError("classeur sans feuille de calcul")

        feuilles: List[Tuple[str, List[List[str]]]] = []
        for nom, chemin in ordre:
            feuille = etree.fromstring(archive.read(chemin))
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
            feuilles.append((nom, lignes))
        return feuilles


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


def lire_classeur(data: bytes, nom: str) -> List[Tuple[str, List[List[str]]]]:
    """Renvoie [(nom de feuille, lignes)]. Un CSV n'a qu'une feuille, sans nom."""
    if nom.lower().endswith((".xlsx", ".xlsm")):
        return _lire_xlsx(data)
    if nom.lower().endswith((".csv", ".txt")):
        return [("", _lire_csv(data))]
    # on tente le ZIP, puis le texte
    try:
        return _lire_xlsx(data)
    except Exception:
        return [("", _lire_csv(data))]


FEUILLE_COMMUNE = "Toutes les factures"
_INTERDITS_FEUILLE = re.compile(r"[:\\/?*\[\]]")


def nom_feuille(nom_fichier: str) -> str:
    """Nom d'onglet deduit d'un nom de fichier : Excel limite a 31 caracteres et
    interdit  : \\ / ? * [ ]  . La meme fonction sert a generer et a rapprocher."""
    base = nom_fichier.replace("\\", "/").split("/")[-1]
    base = re.sub(r"\.[^.]*$", "", base)
    return (_INTERDITS_FEUILLE.sub("-", base)[:31] or "Feuille")


# --------------------------------------------------------------------- regles

@dataclass
class Regle:
    cible: str                       # chemin de la balise a controler
    attendu: str                     # valeur de reference
    cle_chemin: str = ""             # balise qui conditionne la regle
    cle_valeur: str = ""             # valeur que doit avoir cette balise
    commentaire: str = ""
    ligne: int = 0                   # ligne du classeur, pour les messages
    feuille: str = ""                # onglet d'origine : rattache la regle a un fichier

    @property
    def conditionnelle(self) -> bool:
        return bool(self.cle_chemin and self.cle_valeur)

    def concerne(self, nom_fichier: str) -> bool:
        """Une regle d'un onglet nomme d'apres une facture ne vaut que pour elle.
        Les autres onglets (dont « Toutes les factures ») valent pour tous."""
        if not self.feuille or self.feuille == FEUILLE_COMMUNE:
            return True
        return self.feuille == nom_feuille(nom_fichier or "")


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


def charger_regles(feuilles) -> Tuple[List[Regle], List[str]]:
    """Charge les regles de toutes les feuilles d'un classeur.

    Accepte aussi bien la liste de feuilles renvoyee par `lire_classeur` que de
    simples lignes, pour rester utilisable depuis un script.
    """
    if feuilles and isinstance(feuilles[0], (list, tuple)) and len(feuilles[0]) == 2 \
            and isinstance(feuilles[0][0], str) and isinstance(feuilles[0][1], list):
        blocs = list(feuilles)
    else:
        blocs = [("", list(feuilles))]

    regles: List[Regle] = []
    problemes: List[str] = []
    muettes: List[Tuple[str, List[str]]] = []
    for nom, lignes in blocs:
        obtenues, soucis = _charger_feuille(lignes, nom)
        regles.extend(obtenues)
        if obtenues:
            problemes.extend(soucis)
        else:
            # une feuille sans regle est le cas courant : l'utilisateur ne
            # remplit que les onglets qui l'interessent. On ne s'en plaint que
            # si AUCUNE feuille du classeur n'a produit quoi que ce soit.
            muettes.append((nom or "(feuille unique)", soucis))

    if not regles:
        if len(blocs) > 1:
            problemes.append(
                "Aucune règle exploitable : la colonne « Valeur attendue » est vide "
                "sur les %d feuilles (%s)."
                % (len(blocs), ", ".join(nom for nom, _ in muettes[:5])))
        else:
            problemes.extend(muettes[0][1] if muettes else [])
    return regles, problemes


def _charger_feuille(lignes: Sequence[Sequence[str]],
                     feuille: str = "") -> Tuple[List[Regle], List[str]]:
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
                "%sligne %d : « %s » — la condition est incomplète (il faut la balise "
                "clé ET sa valeur). Règle ignorée."
                % ("Onglet « %s », " % feuille if feuille else "", numero, cible))
            continue
        regles.append(Regle(cible=cible, attendu=attendu, cle_chemin=cle_chemin,
                            cle_valeur=cle_valeur,
                            commentaire=cellule(ligne, "commentaire"), ligne=numero,
                            feuille=feuille))

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
    applique: bool = False           # la valeur a effectivement ete remplacee

    def as_dict(self) -> Dict:
        return {"chemin": self.chemin, "actuel": self.actuel, "attendu": self.attendu,
                "commentaire": self.commentaire, "ligne": self.ligne,
                "ambigu": self.ambigu, "candidats": self.candidats,
                "applique": self.applique, "label": self.label}

    @property
    def label(self) -> str:
        if self.applique:
            base = "%s : « %s » remplacé par « %s »." % (
                self.chemin, self.actuel or "(vide)", self.attendu)
            return base + (" (%s)" % self.commentaire if self.commentaire else "")
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


def controler(racine, regles: Sequence[Regle], nom_fichier: str = "") -> List[Ecart]:
    """Confronte un document au referentiel. Ne modifie rien.

    Une regle inscrite sur l'onglet d'une facture ne vaut que pour elle, et prend
    le pas sur une regle generale visant la meme balise : le particulier l'emporte
    sur le general, c'est ce qu'on attend d'un referentiel.
    """
    applicables = [r for r in regles if r.concerne(nom_fichier)]
    specifiques = {r.cible for r in applicables if r.feuille
                   and r.feuille != FEUILLE_COMMUNE}
    ecarts: List[Ecart] = []
    for regle in applicables:
        if regle.cible in specifiques and (
                not regle.feuille or regle.feuille == FEUILLE_COMMUNE):
            continue                       # une regle propre au fichier prend le relais
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


def _commentaire_correction(avant: str, apres: str, motif: str) -> str:
    """Texte accole a une valeur corrigee. « -- » est interdit dans un commentaire."""
    texte = (" VALEUR CORRIGÉE PAR LE CONTRÔLEUR XML/XSD d'après le référentiel : "
             "« %s » remplacé par « %s ».%s À vérifier. "
             % (avant or "(vide)", apres, " %s." % motif if motif else ""))
    return texte.replace("--", "- -")


def appliquer(racine, regles: Sequence[Regle], nom_fichier: str = "") -> List[Ecart]:
    """Ecrit les valeurs de reference dans le document et signale chaque
    remplacement par un commentaire place juste avant la balise.

    Les regles ambigues ne sont JAMAIS appliquees : on ne devine pas quel
    emplacement corriger. Elles restent signalees pour decision humaine.
    """
    ecarts = controler(racine, regles, nom_fichier)
    for ecart in ecarts:
        if ecart.ambigu:
            continue
        cibles = trouver(racine, ecart.chemin)
        if len(cibles) != 1:
            continue                       # le chemin exact doit designer une balise
        el = cibles[0]
        avant = _texte(el)
        el.text = ecart.attendu
        commentaire = etree.Comment(
            _commentaire_correction(avant, ecart.attendu, ecart.commentaire))
        el.addprevious(commentaire)
        commentaire.tail = el.tail if el.getprevious() is commentaire else commentaire.tail
        # le commentaire reprend l'indentation de la balise qu'il precede
        precedent = commentaire.getprevious()
        blanc = (precedent.tail if precedent is not None
                 else el.getparent().text if el.getparent() is not None else None)
        commentaire.tail = blanc
        ecart.applique = True
    return ecarts


# --------------------------------------------------------------------- modele

# Le modele genere reste volontairement a quatre colonnes : les onglets par
# facture expriment deja « cette valeur, pour ce fichier ». Les colonnes de
# condition (« Balise cle » / « Valeur cle ») restent LUES si elles figurent
# dans un classeur, pour qui veut une regle durable independante du nom de
# fichier, mais elles n'encombrent plus le modele.
ENTETES = ["Chemin", "Valeur actuelle", "Valeur attendue", "Commentaire"]


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


# Excel refuse au-dela de 32 767 caracteres par cellule et repare le fichier
# sans prevenir. Une facture UBL peut porter un PDF encode en base64 dans
# <cbc:EmbeddedDocumentBinaryObject> : largement de quoi depasser.
LIMITE_CELLULE = 32767
# La valeur actuelle sert a decider quoi mettre dans « valeur attendue » : elle
# doit rester lisible en entier dans le cas courant. 2 000 caracteres couvrent
# largement une note, une adresse ou un libelle ; seules les pieces jointes
# encodees en base64 (des centaines de milliers de caracteres) sont ecourtees.
LIMITE_APERCU = 2000

# Caracteres interdits par XML 1.0, quel que soit l'echappement.
_INVALIDES_XML = re.compile(
    "[^\u0009\u000A\u000D\u0020-\uD7FF\uE000-\uFFFD]")


def _cellule(texte: str, limite: int = LIMITE_CELLULE) -> str:
    """Ramene une valeur a ce qu'un classeur peut contenir."""
    propre = _INVALIDES_XML.sub("", str(texte or ""))
    if len(propre) > limite:
        propre = propre[:limite - 1] + "…"
    return propre


def _echapper(texte: str) -> str:
    return (_cellule(texte).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _lettre(index: int) -> str:
    nom = ""
    index += 1
    while index:
        index, reste = divmod(index - 1, 26)
        nom = chr(65 + reste) + nom
    return nom


def _feuille_xml(lignes: Sequence[Sequence[str]]) -> str:
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
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="60"/><col min="2" max="2" width="24"/>'
        '<col min="3" max="3" width="34"/><col min="4" max="4" width="14"/>'
        '<col min="5" max="5" width="24"/><col min="6" max="6" width="40"/></cols>'
        '<sheetData>%s</sheetData></worksheet>' % "".join(corps))


def _ecrire_classeur(feuilles: Sequence[Tuple[str, Sequence[Sequence[str]]]]) -> bytes:
    """Assemble un .xlsx multi-feuilles. Un classeur n'est qu'une archive de XML :
    quatre pieces de description suffisent, plus une par feuille."""
    onglets = []
    relations = []
    overrides = []
    fichiers = []
    for index, (nom, lignes) in enumerate(feuilles, start=1):
        chemin = "xl/worksheets/sheet%d.xml" % index
        onglets.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
                       % (_echapper(nom), index, index))
        relations.append(
            '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet%d.xml"/>' % (index, index))
        overrides.append(
            '<Override PartName="/%s" ContentType="application/vnd.openxmlformats-'
            'officedocument.spreadsheetml.worksheet+xml"/>' % chemin)
        fichiers.append((chemin, _feuille_xml(lignes)))

    classeur = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>%s</sheets></workbook>' % "".join(onglets))

    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats'
        '-officedocument.spreadsheetml.sheet.main+xml"/>%s</Types>' % "".join(overrides))

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
        '2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')

    rels_classeur = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        'relationships">%s</Relationships>' % "".join(relations))

    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", classeur)
        archive.writestr("xl/_rels/workbook.xml.rels", rels_classeur)
        for chemin, contenu in fichiers:
            archive.writestr(chemin, contenu)
    return tampon.getvalue()


def generer_modele(documents: Sequence) -> bytes:
    """Produit un .xlsx pret a remplir, **un onglet par facture**.

    Deux factures n'ont pas forcement les memes balises : un onglet unique
    melangerait leurs structures et masquerait ce qui est propre a chacune. Une
    regle inscrite sur l'onglet d'une facture ne vaudra donc que pour elle.

    Quand il y a plusieurs fichiers, un premier onglet « Toutes les factures »
    regroupe les balises presentes partout : c'est la que se mettent les
    constantes (votre TVA, la devise), pour ne pas les recopier partout.

    `documents` : liste de (nom de fichier, racine) — ou de racines seules.
    """
    paires = []
    for element in documents:
        if isinstance(element, (list, tuple)) and len(element) == 2:
            paires.append((str(element[0]), element[1]))
        else:
            paires.append(("", element))

    def bloc(valeurs, entete_commentaire=""):
        lignes = [ENTETES]
        if entete_commentaire:
            lignes.append(["", "", "", entete_commentaire])
        for chemin, valeur, nombre in valeurs:
            notes = []
            if nombre > 1:
                notes.append("%d valeurs différentes selon les fichiers" % nombre)
            apercu = valeur
            if len(valeur) > LIMITE_APERCU:
                apercu = valeur[:LIMITE_APERCU] + "…"
                notes.append("valeur tronquée pour l'affichage (%d caractères dans le "
                             "fichier)" % len(valeur))
            lignes.append([chemin, apercu, "", " ; ".join(notes)])
        return lignes

    feuilles: List[Tuple[str, List[List[str]]]] = []
    if len(paires) > 1:
        communs = None
        for _, racine in paires:
            chemins = {c for c, _, _ in collecter_valeurs([racine])}
            communs = chemins if communs is None else (communs & chemins)
        valeurs = [v for v in collecter_valeurs([r for _, r in paires])
                   if v[0] in (communs or set())]
        feuilles.append((FEUILLE_COMMUNE, bloc(
            valeurs, "Balises présentes dans les %d fichiers. Les règles écrites ici "
                     "s'appliquent à tous." % len(paires))))

    utilises = {FEUILLE_COMMUNE}
    for index, (nom, racine) in enumerate(paires, start=1):
        onglet = nom_feuille(nom) if nom else "Facture %d" % index
        base, compteur = onglet, 2
        while onglet in utilises:            # Excel refuse deux onglets homonymes
            suffixe = " (%d)" % compteur
            onglet = base[:31 - len(suffixe)] + suffixe
            compteur += 1
        utilises.add(onglet)
        commentaire = ("Règles propres à ce fichier ; elles priment sur l'onglet commun."
                       if len(paires) > 1 else "")
        feuilles.append((onglet, bloc(collecter_valeurs([racine]), commentaire)))

    return _ecrire_classeur(feuilles)
