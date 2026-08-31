# Reprendre le backfill

État au 31 août 2026.

## ⚠️ Un seul backfill à la fois

`purger_la_file()` annule **tout** travail en statut `accepted` sur le compte, sans distinguer
le sien de celui d'un autre processus. C'est correct pour une reprise après interruption — au
démarrage, rien de ce run n'a encore été soumis, donc tout ce qui attend vient d'un run mort.

Lancer un second backfill pendant qu'un premier tourne tue donc la file du premier, qui
continuera d'attendre poliment des travaux annulés.

**Ce n'est plus une hypothèse.** Le 30 août, un run `1991-2025` lancé à 20:50 et un run
`1950-2025` lancé à 21:34 ont tourné ensemble douze heures. Le second a annulé sept travaux du
premier — son journal l'annonce d'ailleurs en clair, « 7 travaux d'un run précédent annulés » —
et ces sept-là sont ressortis côté premier en `404 Client Error` sur le statut du travail, sans
qu'aucun message ne relie les deux. Les deux runs se disputaient en outre les créneaux du
compte : 18 requêtes en vol pour un débit qui n'a pas bougé.

Vérifier avant de lancer, et lire la ligne de commande, pas seulement la présence du processus :

```bash
ps -eo pid,etime,args | grep '[b]ackfill.py'
```

**Si un run a été tué, ses travaux survivent côté serveur** et occupent des créneaux. Ne pas
appeler `purger_la_file()` pour les nettoyer si un autre backfill tourne : elle les tuerait
aussi. Les annuler un par un, en épargnant ceux dont le journal du run vivant porte
l'identifiant — `GET /api/retrieve/v1/jobs?limit=200` les liste avec leur statut, `DELETE
/api/retrieve/v1/jobs/{id}` en annule un. Un `429` sur une annulation est une limite de débit :
réessayer vingt secondes plus tard.

## La période publiée est 1991-2025, et c'est une décision

**Ne pas relancer un backfill `--from 1950`.** L'ancienne version de ce document prescrivait
1991-2025 puis 1950-2025 ; c'est cette phrase qui a provoqué la collision du 30 août.

1991-2025 couvre tout ce que l'application sait faire : la normale 1991-2020, les comparaisons,
les palmarès, les sept indicateurs. Les années 1950-1990 ne serviraient que le palmarès, qui n'a
besoin que d'une moyenne annuelle et d'un cumul par an — et les publier au jour le jour coûterait
~750 Mo de pack git supplémentaires, pour des années que personne ne trace jour par jour.

Elles seront reprises un jour en **agrégats annuels**. D'ici là, le cache garde 42 trimestres
1950-1960 téléchargés par le run interrompu : un backfill complet ne les redemandera pas.

```bash
python tools/backfill.py --from 1991 --to 2025 --out . --cache tools/.cache --parallele 12
```

**L'assemblage n'écrit rien tant que toutes les années demandées ne sont pas en cache.** Un run
ne produit donc aucun fichier avant sa dernière requête. Et une seule requête `temperature` en
échec suffit à l'interrompre avant d'assembler : le cache garde le reste, relancer reprend là où
c'était.

## Ce qu'on sait de la durée

**Mesuré le 31 août, 12 requêtes en vol.** Un trimestre horaire : ~29 min en file, puis ~6 min
de traitement et de transfert. Soit une vingtaine de requêtes à l'heure, et de l'ordre de dix
heures pour les 176 requêtes de 1991-2025 (140 trimestres de température, 36 années de pluie).

**Les précipitations sont plus rapides** — quelques minutes — et représentent 36 des 176
requêtes.

**Zéro rejet à 8 requêtes en vol, puis à 12.** `--parallele 16` mérite d'être tenté ; un rejet
n'est pas destructeur.

Ne pas chercher la file du dataset quotidien dérivé dans ces chiffres : la température ne vient
plus de là. Voir `build_hourly_temperature_request` pour la mesure qui a fait préférer le dataset
horaire — 6 à 16 heures par requête contre quelques minutes.

## Volumes, mesurés

| | |
| --- | --- |
| un trimestre horaire en cache | 51,9 Mo (moyenne sur 138 fichiers) |
| une année de précipitations | ~8,7 Mo — une heure par jour, 365 pas contre 2184 |
| cache complet 1991-2025 | ~10 Go |
| cache complet 1950-2025 | ~16 Go |
| sortie `grid/` 1991-2025 | 1,18 Go, soit ~640 Mo de pack git |
| mémoire vive à l'assemblage | 1,18 Go |

## Identifiants

Le secret `CDSAPI_KEY` du dépôt est posé depuis le 30 août. **Un jeton vide fait répondre 500 au
CDS** — et non 401 —, ce que `cdsapi` prenait pour une panne passagère et rejouait 500 fois à
deux minutes. Les workflows refusent désormais de démarrer sans lui.

En local, la clé se lit dans `~/.cdsapirc`.

## Ce qui lit ces données

L'application PlusD'Saison, et **P'tit Jardinier**, qui consomme `main/manifest.json` et
`main/grid/{maille}/history.bin` en production.

Conséquence : `n_vars` reste à 4 et `format_version` à 1, et les chemins ne bougent pas. Les
quatre colonnes sont consommées — `tmin` et `pluie` par les deux applications, `tmoy` aussi,
`tmax` par PlusD'Saison seule. **Ajouter une colonne est sûr** : les deux décodeurs lisent les
quatre premières et ignorent le reste. En retirer une, ou changer une URL, ne l'est pas.

Un changement d'URL est le seul qu'aucun contrôle ne rattrape : il ne produit pas un format
inattendu mais un 404, que `format_version` ne verra jamais. Prévenir avant, pas au push.

## Après le backfill

```bash
# Les assets de l'application viennent de l'index, pas des séries.
# À refaire seulement si build_index.py a tourné entre-temps.
cd ~/Workspaces/exec-d/plusdsaison-app
cp ../plusdsaison/index/communes.bin assets/communes.bin
cp ../plusdsaison/index/grid.bin     assets/grid.bin

flutter test              # dont le contrat binaire Python ↔ Dart
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Puis publier les séries depuis le dépôt de données :
`git add grid/ manifest.json && git commit && git push`.

**Réactiver le rafraîchissement quotidien**, désactivé le temps du backfill pour qu'il ne se
dispute pas la file :

```bash
gh workflow enable daily.yml
gh workflow run daily.yml        # vérifier un run complet avant de compter dessus
```

Enfin, taguer l'application — c'est le tag qui déclenche l'APK signé, la Release publique et
`app/latest.json` :

```bash
cd ~/Workspaces/exec-d/plusdsaison-app && git tag v0.1.0 && git push origin v0.1.0
```
