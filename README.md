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
| **Ajout des éléments obligatoires manquants** | désactivée | Insère les balises requises **vides**, à la bonne position. Le fichier reste donc souvent « partiel » : à compléter à la main. |
| **Suppression des balises inconnues** | désactivée | Retire les éléments absents du XSD. **Perte de données** : à activer en connaissance de cause. |

Principe de prudence : **aucune donnée métier n'est inventée**. Un montant, une
date ou un numéro manquant ne sera jamais rempli automatiquement — l'outil
signale le problème et laisse trancher.

Autre garde-fou : si les corrections ne réduisent pas le nombre d'erreurs,
aucun fichier corrigé n'est proposé. Mieux vaut rendre la main que livrer un
XML douteux.

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
  webapi.py          frontière Python ↔ navigateur (JSON / base64)
cli.py               même moteur, en ligne de commande
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
