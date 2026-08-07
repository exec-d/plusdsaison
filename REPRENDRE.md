# Reprendre le backfill

État au 7 août 2026.

## ⚠️ Un seul backfill à la fois

`purger_la_file()` annule **tout** travail en statut `accepted` sur le compte, sans distinguer
le sien de celui d'un autre processus. C'est correct pour une reprise après interruption — au
démarrage, rien de ce run n'a encore été soumis, donc tout ce qui attend vient d'un run mort.

Lancer un second backfill pendant qu'un premier tourne tue donc la file du premier, qui
continuera d'attendre poliment des travaux annulés. Rien dans le journal ne le dira.

Vérifier avant de lancer : `pgrep -af backfill.py`.

## En deux temps, et pourquoi

L'assemblage n'écrit rien tant que **toutes** les années de la période demandée ne sont pas en
cache. Un run 1950-2025 ne produit donc aucun fichier avant sa 305<sup>e</sup> requête, et les
années qui portent l'application — celles de la normale — sont téléchargées en dernier,
`taches_de_telechargement` allant dans l'ordre croissant.

D'où l'ordre retenu :

```bash
# 1. 141 requêtes. Suffit à tout ce que l'application sait faire : la normale
#    1991-2020, les comparaisons, les palmarès. Publiable en l'état.
python tools/backfill.py --from 1991 --to 2025 --out . --cache tools/.cache --parallele 12

# 2. 305 requêtes, dont 141 déjà en cache — seules 1950-1990 sont téléchargées.
#    Le coût total est le même qu'un run unique ; seul l'assemblage est refait.
python tools/backfill.py --from 1950 --to 2025 --out . --cache tools/.cache --parallele 12
```

Le cache ne redemande jamais une année obtenue : une interruption ne coûte que les requêtes en
vol au moment où elle survient.

## Ce qu'on sait de la durée, et ce qu'on n'en sait pas

**Mesuré** : les précipitations arrivent en trois minutes. Elles viennent de
`reanalysis-era5-land`, dont la file est fluide, et représentent 77 des 305 requêtes.

**Pas mesuré** : la file du dataset quotidien dérivé, d'où viennent les trois séries de
température. Les attentes observées — jusqu'à trois heures — étaient faussées par des travaux
fantômes que le correctif de purge élimine désormais. Aucun chiffre fiable n'existe encore.

**Zéro rejet à 8 requêtes en vol**, puis à 12. La concurrence peut sans doute monter ;
`--parallele 16` mérite d'être tenté, un rejet n'étant pas destructeur.

## Après le backfill

```bash
# Les assets de l'application viennent de l'index, pas des séries.
# À refaire seulement si build_index.py a tourné entre-temps.
cd ~/Workspace/exec-d/plusdsaison
cp ../plusdsaison-public/index/communes.bin assets/communes.bin
cp ../plusdsaison-public/index/grid.bin     assets/grid.bin

flutter test              # dont le contrat binaire Python ↔ Dart
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Puis publier les séries : `git add grid/ manifest.json && git commit && git push` depuis le
dépôt public.

Enfin, taguer l'application — c'est le tag qui déclenche l'APK signé, la Release publique et
`app/latest.json` :

```bash
cd ~/Workspace/exec-d/plusdsaison && git tag v0.1.0 && git push origin v0.1.0
```
