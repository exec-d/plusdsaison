# Reprendre le backfill

État au 6 août 2026, après une session interrompue.

## Une seule commande

```bash
cd ~/Workspace/exec-d/plusdsaison-public
python tools/backfill.py --from 1950 --to 2025 --out . --cache tools/.cache --parallele 8
```

Elle purge d'elle-même la file Copernicus des travaux laissés par la session
précédente, puis reprend là où le cache s'arrête. Rien à nettoyer à la main.

## Ce que le cache contient déjà

Quatre fichiers dans `tools/.cache/`, soit autant de requêtes qui ne seront pas
redemandées :

- `2m_temperature_daily_minimum_2024.nc`
- `total_precipitation_2024.nc`, `2025.nc`, `2026.nc`

## Ce qu'on sait de la durée, et ce qu'on n'en sait pas

**Mesuré** : les précipitations arrivent en trois minutes. Elles viennent de
`reanalysis-era5-land`, dont la file est fluide, et représentent 77 des
305 requêtes.

**Pas mesuré** : la file du dataset quotidien dérivé, d'où viennent les trois
séries de température. Les attentes observées — jusqu'à trois heures — étaient
faussées par des travaux fantômes que le correctif de purge élimine désormais.
Aucun chiffre fiable n'existe encore.

**Zéro rejet à 8 requêtes en vol.** La concurrence peut sans doute monter ;
`--parallele 16` mérite d'être tenté, un rejet n'étant pas destructeur.

## Si tu veux des données utilisables plus vite

`--from 1991 --to 2025` suffit à tout ce que l'application sait faire :
c'est la période qui porte la normale 1991-2020, les comparaisons et les
classements. Les années antérieures se rajoutent ensuite sans rien perdre — le
cache ne redemande jamais une année obtenue.

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

Puis publier les séries : `git add grid/ manifest.json && git commit && git push`
depuis le dépôt public.
