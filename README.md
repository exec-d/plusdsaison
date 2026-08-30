# PlusD'Saison

**Comment le climat de votre commune évolue depuis 1950.**

Ce n'est pas une application de météo : elle ne dit pas le temps qu'il fera demain. Elle regarde
en arrière — *cette année est-elle plus chaude que la précédente ?*, *à quoi ressemble un hiver
normal ici ?*, *pleut-il moins qu'avant ?* — pour les 35 000 communes françaises, sans compte,
sans publicité et sans traçage.

- **[Télécharger l'APK Android](https://github.com/exec-d/plusdsaison/releases/latest)**
- **[Page de présentation](https://exec-d.github.io/plusdsaison/)**

Ce dépôt est la partie publique du projet : les données, le code qui les produit, et la
distribution de l'application. Le code de l'application Android, lui, vit ailleurs — seul son
APK signé est publié ici, en Release.

## Ce que contient le dépôt

### Les données

- **`index/communes.bin`** — les 35 000 communes françaises, chacune rattachée à sa maille de
  grille, avec code INSEE, coordonnées et altitude. Embarqué comme asset dans l'application.
- **`index/grid.bin`** — les mailles ERA5-Land couvrant la France, avec l'orographie du modèle.
  Embarqué lui aussi : la correction altitudinale en dépend.
- **`grid/{maille}/history.bin`** — l'historique quotidien 1950 → N-1 d'une maille : température
  minimale, maximale, moyenne et précipitations, quantifiées en entiers 16 bits. ~118 Ko.
- **`current/{maille}.bin`** — l'année en cours, sur la branche orpheline `data-current`,
  réécrite chaque jour en force. Orpheline précisément pour cela : republier 11 500 fichiers par
  jour sur `main` en ferait un dépôt de plusieurs gigaoctets en un an.
- **`validation/{dept}.json`** — l'écart mesuré entre nos données et les stations Météo-France,
  sur quatre départements témoins (01, 29, 74, 20) choisis pour couvrir montagne, littoral et
  Méditerranée.
- **`manifest.json`** — la couverture publiée : nombre de mailles, de communes, premier et
  dernier jour. C'est ce que l'application lit en premier.

Les formats binaires sont définis par [`tools/plusdsaison/binary.py`](tools/plusdsaison/binary.py)
et [`index_io.py`](tools/plusdsaison/index_io.py). Le contrat est vérifié des deux côtés : les
tests Python ici, et côté application un test qui décode en Dart les fichiers que Python a
écrits.

### Le reste

- **`tools/`** — le pipeline : construction de l'index, backfill historique, bascule annuelle,
  rafraîchissement quotidien, validation.
- **`docs/`** — la page de présentation, servie par GitHub Pages.
- **`app/latest.json`** — la version courante de l'application, publiée par la CI du dépôt privé.
  L'application la relit au lancement pour proposer sa propre mise à jour.

## Comment les données sont tenues à jour

| Quand | Quoi |
| --- | --- |
| tous les jours, 05:00 UTC | `daily.yml` — l'année en cours, réécrite en entier |
| tous les lundis, 04:00 UTC | `validate.yml` — l'écart aux stations Météo-France |
| au 1<sup>er</sup> janvier | `rollover.py` — l'année écoulée rejoint l'historique |
| à la demande | `backfill.yml` — reconstruction d'une période complète |

Aucun serveur : l'application ne parle qu'à des fichiers statiques servis par GitHub. Il n'y a
donc rien à héberger, rien à payer, et rien qui puisse tomber en panne sans que GitHub tombe
avec.

## Données & licence

Données : **ERA5-Land** du Copernicus Climate Change Service, **Météo-France** via
meteo.data.gouv.fr, **geo.api.gouv.fr** pour le référentiel communal, et l'**IGN** pour les
altitudes. Le détail des licences et des mentions d'attribution obligatoires figure dans
[`DATA-LICENSE.md`](DATA-LICENSE.md).

> Contient des données de l'IGN — RGE ALTI®, sous Licence Ouverte 2.0.

> Generated using Copernicus Climate Change Service information 2026.

Ni le service Copernicus ni Météo-France ne sont responsables de l'usage qui est fait ici de
leurs données.

Application non officielle, non affiliée à Météo-France. Les erreurs qu'elle contiendrait sont
les siennes.
