# Contrôleur XML / XSD

Site web qui vérifie un lot de fichiers XML face à un schéma XSD de référence,
puis **génère les fichiers corrigés** pour ceux qui ne sont pas conformes.

Cas d'usage d'origine : des factures XML dont les balises ne sont pas dans
l'ordre imposé par le XSD.

> **Tout s'exécute dans le navigateur du visiteur.** Le moteur de validation est
> lxml (libxml2) compilé en WebAssembly via [Pyodide](https://pyodide.org).
> Aucun fichier n'est téléversé : les factures ne quittent pas la machine de
> celui qui les dépose. Il n'y a pas de serveur, donc rien à administrer, rien à
> payer, et aucune donnée personnelle à protéger côté serveur.

## Mettre le site en ligne (GitHub Pages, gratuit)

Le dépôt **est** le site : aucune étape de compilation.

```bash
gh auth login
gh repo create controleur-xml-xsd --public --source=. --push
```

Puis, dans le dépôt sur GitHub : **Settings → Pages → Build and deployment →
Source : GitHub Actions**.

Le statut `deployment_queued` affiché par l’action de déploiement est normal :
il signifie que GitHub a bien créé la publication et qu’elle attend son tour.

Une minute plus tard, le site est en ligne :
`https://<votre-compte>.github.io/controleur-xml-xsd/`

Sans la commande `gh`, c'est l'équivalent manuel : créer le dépôt sur
github.com, puis

```bash
git remote add origin https://github.com/<votre-compte>/controleur-xml-xsd.git
git push -u origin main
```

Pour mettre à jour le site ensuite, il suffit de pousser : `git push`.

### Points d'attention

- Le fichier `.nojekyll` est **nécessaire** : il empêche GitHub de faire passer
  le site par Jekyll, qui ignorerait certains fichiers. Ne le supprimez pas.
- Les chemins sont tous relatifs, le site fonctionne donc aussi bien à la racine
  d'un domaine que dans un sous-dossier `/nom-du-depot/`.
- GitHub Pages sert le site en HTTPS avec un certificat automatique.
- Un **domaine personnalisé** s'ajoute dans Settings → Pages → Custom domain.

## Utilisation

1. Déposez le **XSD** de référence dans la zone de gauche (ajoutez aussi les
   schémas importés via `xs:include` / `xs:import` s'il y en a).
2. Déposez les **XML** à vérifier dans la zone de droite — autant que vous
   voulez, ou un dossier entier.
3. Cliquez **Analyser et corriger**.

Chaque fichier reçoit un statut :

| Statut | Signification |
|---|---|
| **conforme** | valide dès le départ, rien à faire |
| **corrigé** | des erreurs ont été trouvées, le fichier généré est maintenant valide |
| **partiel** | amélioré, mais il reste des erreurs qui demandent une décision humaine |
| **échec** | aucune correction automatique fiable n'était possible |
| **illisible** | XML mal formé (balise non fermée, caractère interdit…) |

Les erreurs sont classées par nature, ce qui n'est pas immédiat : le validateur
emploie le **même message** pour « cette balise est mal placée » et pour « la
balise obligatoire qui devait précéder est absente » — il signale l'élément sur
lequel il bute, jamais celui qui manque. L'outil tranche en regardant si
l'élément attendu figure ailleurs sous le même parent : présent, c'est un
problème d'ordre, corrigé automatiquement ; absent, c'est une balise manquante,
qui relève de l'option d'ajout ou d'une décision de votre part.

Téléchargez chaque fichier corrigé individuellement, ou tous d'un coup en ZIP
(l'archive contient aussi un `rapport.txt` récapitulatif).

Le bouton **Charger le jeu d'exemple** remplit l'application avec les fichiers
de `samples/` : une facture conforme et trois fautives.

### Schémas en plusieurs fichiers (UBL, Factur-X…)

Les schémas normalisés sont découpés en dizaines de fichiers qui s'importent
entre eux par chemins relatifs (`../common/UBL-CommonBasicComponents-2.1.xsd`).
Deux façons de les fournir, les deux fonctionnent :

- **glissez le dossier entier** : l'arborescence est conservée telle quelle ;
- **déposez tous les `.xsd` en vrac** : l'application répare d'elle-même les
  chemins d'import en retrouvant chaque fichier par son nom.

Si un import reste introuvable, le fichier manquant est nommé explicitement
dans le rapport — c'est presque toujours la cause d'un « XSD invalide ».

Quand plusieurs XSD sont déposés, un menu permet de désigner le schéma
principal ; par défaut c'est celui qu'aucun autre n'importe.

### Diagnostiquer sans divulguer ses fichiers

Si un XSD refuse vos XML sans que la raison soit claire, et que les fichiers
sont confidentiels :

```bash
python3 diagnostic.py --xsd schema.xsd facture.xml
```

Le script n'extrait que la **structure** — espaces de noms, noms des éléments
globaux, arborescence sur deux niveaux — et confronte les deux. Il ne lit
jamais le texte des éléments ni les valeurs d'attributs : aucun montant, aucun
nom de société, aucune adresse ne peut en sortir. L'option `--noms-masques`
remplace en plus les noms de balises par des codes stables.

La cause la plus fréquente d'un rejet de la racine : un XSD **sans
`targetNamespace`** confronté à un XML qui, lui, utilise des espaces de noms.
Les deux décrivent alors des mondes différents, et aucune correction
automatique ne peut les réconcilier — il faut le schéma correspondant.

### XSD généré depuis un XML d'exemple : à convertir d'abord

Les générateurs en ligne (Liquid Technologies, XmlGrid, FreeFormatter…) qui
déduisent un XSD à partir d'un XML **ne gèrent pas les espaces de noms**. Ils
produisent un schéma reconnaissable à deux signes :

- pas de `targetNamespace` sur `<xs:schema>` ;
- des noms de balises qui contiennent le préfixe : `name="cbc.UBLVersionID"`
  au lieu de `ref="cbc:UBLVersionID"`.

Un tel schéma **ne peut valider aucun XML à espaces de noms**, pas même celui
dont il est issu : pour un validateur, `cbc.UBLVersionID` est un nom contenant
un point, sans rapport avec `UBLVersionID` dans l'espace de noms `cbc`. Ce
n'est pas une limite de cet outil — `xmllint` refuse exactement pareil.

**L'application reconnaît ces schémas d'elle-même** et propose la conversion en
un clic, avant même de lancer l'analyse : les fichiers produits remplacent le
XSD d'origine dans la zone de dépôt et l'analyse repart aussitôt. Rien à
installer, rien à taper.

La même conversion existe en ligne de commande, pour les traitements par lots.
Elle rétablit la sémantique perdue **sans toucher à l'ordre des balises** que
vous avez défini :

```bash
python3 convertir_xsd.py mon-schema.xsd --depuis-xml une-facture.xml --out schema-converti
```

Les espaces de noms sont appris depuis les déclarations `xmlns:` du XML fourni
(ou indiqués à la main avec `--ns prefixe=uri`). Il produit un fichier par
espace de noms, à déposer tous ensemble dans l'application, en désignant
`ubl.xsd` comme schéma principal.

#### Les types devinés, et pourquoi ils sont assouplis

Un générateur déduit les types d'**un seul exemple**. Si l'identifiant de la
facture témoin était `380`, il écrit `type="xs:short"` — et toute facture dont
l'identifiant contient une lettre (`FA-2026-0042`) sera rejetée, alors que rien
ne cloche vraiment.

La conversion **remplace donc les types par `xs:string` par défaut** : le
contrôle porte sur la structure et l'ordre des balises, pas sur le format des
valeurs. C'est le réglage adapté à un schéma déduit d'un exemple. Une case à
cocher permet de conserver les types devinés (`--types-stricts` en ligne de
commande) si vous voulez aussi contrôler les formats.

Indépendamment de ce réglage, le générateur a pu déclarer un même nom sous des
formes différentes selon le contexte (`cbc.ID` numérique en tête de facture,
mais textuel dans `cac:OrderReference`). En XSD un élément global n'a qu'une
seule définition : le convertisseur retient alors **le type le plus permissif**
— rejeter à tort toutes les valeurs d'un contexte serait pire que ne pas les
contrôler — et **liste chaque arbitrage**. Relisez cette liste, c'est là que la
conversion perd en précision.

### Référentiel de valeurs (facultatif)

Le XSD contrôle la **structure** : quelles balises, dans quel ordre, de quel
type. Il ne saura jamais qu'un numéro de TVA doit valoir `3145` et non `11234`.
Cette source de vérité-là se fournit sous forme de classeur **Excel ou CSV**.

Les écarts sont **signalés, jamais corrigés** : réécrire une donnée métier reste
une décision humaine. Un fichier peut donc être « conforme » au XSD et présenter
des écarts de données.

**Le plus simple : laisser l'application produire le modèle.** Déposez vos XML,
cliquez *Générer le modèle Excel* : vous obtenez un classeur listant chaque
balise, son chemin complet et sa valeur actuelle. Vous ne remplissez que la
colonne « Valeur attendue », et vous redéposez le fichier. Aucun chemin à écrire
à la main.

| Colonne | Rôle |
|---|---|
| **Chemin** | quelle balise contrôler (voir ci-dessous) |
| **Valeur attendue** | la valeur de référence |
| **Balise clé** *(option)* | balise dont dépend la règle |
| **Valeur clé** *(option)* | valeur que doit avoir cette balise pour que la règle s'applique |
| **Commentaire** | libre, repris dans le rapport |

Les colonnes sont reconnues **par leur intitulé**, quel que soit leur ordre.

#### Une même balise à deux endroits

C'est le cas courant : `CompanyID` est le numéro de TVA du vendeur sous
`AccountingSupplierParty` et celui du client sous `AccountingCustomerParty`. Le
nom seul ne suffit donc pas à désigner un emplacement. Trois écritures sont
acceptées, de la plus souple à la plus précise :

| Écriture | Exemple |
|---|---|
| Nom seul | `DocumentCurrencyCode` |
| Chemin abrégé | `AccountingSupplierParty//CompanyID` |
| Chemin exact | `/Invoice/cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID` |

Les préfixes d'espace de noms sont ignorés : écrivez `cbc:ID` ou `ID`, c'est
identique.

Si une règle vise **plusieurs emplacements portant des valeurs différentes**,
l'application **refuse de deviner** : elle signale l'ambiguïté et affiche les
chemins candidats, à recopier dans le classeur pour lever le doute.

#### Une valeur qui dépend de la facture

Les colonnes « Balise clé » et « Valeur clé » conditionnent une règle :

| Chemin | Valeur attendue | Balise clé | Valeur clé |
|---|---|---|---|
| `Vendeur/NumeroTVA` | `3145` | | |
| `Client/NumeroTVA` | `FR55987654321` | `Client/Code` | `1084` |
| `Client/NumeroTVA` | `FR12000000009` | `Client/Code` | `2201` |

La première ligne est une **constante** : elle s'applique à tous les fichiers.
Les deux suivantes ne s'appliquent qu'aux factures du client concerné. Une
condition à moitié renseignée est signalée et la règle ignorée, plutôt
qu'appliquée de travers.

En ligne de commande : `--referentiel mon-fichier.xlsx`.

### Premier chargement

Le navigateur télécharge ≈ 8 Mo (Python + lxml en WebAssembly) à la première
visite, puis tout est mis en cache. Le moteur se charge en tâche de fond dès
l'ouverture de la page : il est généralement prêt avant que l'utilisateur ait
fini de déposer ses fichiers. La pastille en haut de page indique son état.

## Ce que l'application corrige

| Correction | Par défaut | Détail |
|---|---|---|
| **Ordre des balises** | activée | Remet les éléments dans l'ordre du `xs:sequence`, à tous les niveaux d'imbrication. |
| **Espace de noms** | activée | Ajoute le `xmlns` manquant sur la racine (ou le retire s'il est en trop), en respectant `elementFormDefault`. |
| **Nettoyage des valeurs** | activée | Retire les espaces parasites autour d'une valeur qui fait échouer un format (code pays, date…). N'agit que sur les valeurs effectivement en erreur. |
| **Ajout des éléments obligatoires manquants** | désactivée | Insère les balises requises **vides**, à la bonne position, chacune précédée d'un commentaire signalant l'ajout. Le fichier reste donc souvent « partiel » : à compléter à la main. |
| **Suppression des balises inconnues** | désactivée | Retire les éléments absents du XSD. **Perte de données** : à activer en connaissance de cause. |

Principe de prudence : **aucune donnée métier n'est inventée**. Un montant, une
date ou un numéro manquant ne sera jamais rempli automatiquement — l'outil
signale le problème et laisse trancher.

Une balise ajoutée est donc vide, et se signale dans le fichier :

```xml
<PaymentMeans>
  <!-- AJOUTÉ PAR LE CONTRÔLEUR XML/XSD : balise obligatoire absente du fichier
       d'origine. À vérifier et à compléter avant envoi. -->
  <PaymentMeansCode/>
  <PaymentID>P1</PaymentID>
</PaymentMeans>
```

Le commentaire reste accolé à sa balise même si un réordonnancement intervient
ensuite. `--sans-commentaires` en ligne de commande pour s'en passer.

Autre garde-fou : les corrections destructrices sont bridées. Un espace de noms
n'est retiré que si le document n'en utilise qu'un seul — sur un fichier
multi-espaces (UBL, Factur-X), l'outil refuse d'y toucher et signale que le
schéma ne correspond pas, plutôt que de mutiler le fichier pour le faire entrer
au forceps dans le XSD.

Le rapport ne juge jamais une correction au nombre d'erreurs restantes : quand
la racine est rejetée, le validateur s'arrête là, et la corriger fait
légitimement apparaître toutes les erreurs qu'elle masquait.

## Ce que l'application ne corrige pas

- Les **valeurs invalides** (un montant `abc` dans un champ décimal, une date au
  mauvais format, une énumération non respectée) : elles sont signalées, pas
  réécrites.
- Les **attributs** manquants ou invalides.
- Les **XML mal formés** : un fichier que le parseur ne peut pas lire n'est pas
  réparé.
- Les contraintes hors XSD (`xs:key`, Schematron, règles métier EN 16931…).

## Usage local

Le site fonctionne aussi sans connexion, depuis le dossier du dépôt :

```bash
python3 -m http.server 8000
```

puis <http://127.0.0.1:8000>. Sur macOS, `demarrer.command` se double-clique.

> Ouvrir `index.html` directement (`file://`) ne fonctionne pas : les
> navigateurs interdisent à une page locale de charger ses propres ressources.

### Ligne de commande

Pour traiter des lots sans navigateur, ou brancher l'outil dans un script. Cette
voie utilise le Python de votre machine et nécessite lxml (`pip3 install lxml`) :

```bash
python3 cli.py --xsd samples/facture.xsd --out corriges 'samples/*.xml'
```

Options : `--ajouter-manquants`, `--supprimer-inconnus`, `--sans-reordonner`,
`--sans-namespace`, `--verbeux`.
Code de sortie `0` si tout est conforme, `1` sinon — utilisable en CI.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Les tests couvrent surtout les constructions XSD où un réordonnancement naïf
casserait le document : `xs:all` (ordre libre), groupes répétés `(A,B)*`,
`xs:choice maxOccurs="unbounded"`, extensions de type, groupes nommés, groupes
de substitution, `elementFormDefault="unqualified"`, schémas multi-fichiers
(`xs:include`, `xs:import` multi-espaces de noms façon Factur-X / UBL), ainsi
que la conservation des attributs, commentaires et encodages (dont ISO-8859-1).

Ils couvrent aussi un piège de diagnostic : quand l'élément racine est rejeté,
le validateur s'arrête immédiatement et ne signale qu'une seule erreur. Corriger
la racine fait apparaître toutes celles qu'elle masquait — le nombre d'erreurs
augmente alors que le fichier s'améliore. Le rapport ne doit donc jamais juger
une correction sur le décompte d'erreurs.

Le moteur étant strictement le même en local et dans le navigateur, ces tests
valident aussi le site.

## Organisation du code

```
index.html           le site
assets/              interface (CSS + JS, sans framework)
xsdfix/              le moteur, chargé tel quel par le navigateur
  schema_model.py    lecture du XSD → ordre attendu des éléments
  validator.py       validation lxml + messages d'erreur en français
  corrector.py       moteur de correction (ordre, namespace, ajouts, valeurs)
  service.py         Session (un XSD, N fichiers) + rapport + ZIP
  flat_schema.py     remet les espaces de noms dans un XSD généré « à plat »
  referentiel.py     contrôle des données face à un classeur Excel/CSV
  webapi.py          frontière Python ↔ navigateur (JSON / base64)
cli.py               même moteur, en ligne de commande
diagnostic.py        extrait la structure XSD/XML sans divulguer de données
convertir_xsd.py     idem en ligne de commande (le moteur est dans xsdfix/flat_schema.py)
samples/             jeu d'exemple
tests/               tests unitaires
```

Le moteur ne dépend d'aucune interface. Pour le déployer un jour derrière un
vrai backend (FastAPI, Flask…), un appel à `analyze()` suffit :

```python
from xsdfix.service import InputFile, analyze
from xsdfix.corrector import Options

rapport = analyze(
    [InputFile("facture.xsd", xsd_bytes)],
    [InputFile("f1.xml", xml_bytes), InputFile("f2.xml", autre_bytes)],
    Options(insert_missing=True),
)
```

## Comment fonctionne la remise en ordre

Le XSD est aplati en une **clé de tri** par nom d'élément, puis les enfants sont
triés par cette clé (tri stable, donc à clé égale l'ordre d'origine est
conservé) :

- `xs:sequence` → indice croissant : l'ordre compte ;
- `xs:choice` → une clé par branche ;
- `xs:all` → clé identique pour tous : l'ordre est libre, on ne touche à rien ;
- compositeur répétable (`maxOccurs > 1`) → clé identique pour toute sa
  descendance, car `(A,B)*` produit `A,B,A,B` et un tri par nom le casserait ;
- `xs:extension` → contenu du type de base d'abord, puis celui de l'extension.

Les commentaires suivent l'élément qu'ils précèdent, et une balise inconnue
reste ancrée à sa position d'origine plutôt que d'être déplacée au hasard.

## Dépendance externe

Le seul appel réseau du site est le téléchargement de Pyodide depuis le CDN
jsdelivr. Pour supprimer même cette dépendance, copiez la distribution Pyodide
dans le dépôt et remplacez `PYODIDE_MJS` / `PYODIDE_INDEX` en tête de
`assets/app.js` par des chemins relatifs. Le dépôt gagne ≈ 8 Mo, et le site ne
dépend alors plus que de GitHub.
