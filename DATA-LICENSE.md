# Licences et attribution des données publiées dans ce dépôt

Ce dépôt redistribue des données dérivées de jeux publics. Chaque jeu conserve la licence de sa
source.

## `grid/` et `current/` — séries climatiques quotidiennes

Dérivées du jeu **ERA5-Land daily statistics** du Copernicus Climate Change Service (C3S),
distribué par le Climate Data Store — DOI [10.24381/cds.e9c9c792](https://doi.org/10.24381/cds.e9c9c792).

Les précipitations proviennent du jeu **ERA5-Land hourly** du même service : les statistiques
quotidiennes ne couvrent pas les grandeurs accumulées.

Modifications apportées : extraction de l'emprise France métropolitaine, conversion des
températures en degrés Celsius et des précipitations en millimètres, quantification en entiers
16 bits au dixième d'unité.

> Generated using Copernicus Climate Change Service information 2026.
> Ni la Commission européenne ni l'ECMWF ne sont responsables de l'usage fait de ces données.

## `validation/` — écarts mesurés aux stations

Calculés à partir des **Données climatologiques de base quotidiennes** de **Météo-France**,
diffusées sur [meteo.data.gouv.fr](https://meteo.data.gouv.fr) sous
[Licence Ouverte 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).

> Contient des données de Météo-France diffusées via meteo.data.gouv.fr, sous Licence Ouverte 2.0.

## `index/communes.bin` — référentiel communal

Construit à partir de [geo.api.gouv.fr](https://geo.api.gouv.fr) (code officiel géographique et
centroïdes, Licence Ouverte) et de l'API Elevation d'[Open-Meteo](https://open-meteo.com)
(altitudes, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

Modifications apportées : rattachement de chaque commune à sa maille ERA5-Land terrestre la plus
proche, et calcul de la distance de rattachement.

## `index/grid.bin` — orographie du modèle

Dérivée du géopotentiel de surface du jeu **ERA5-Land hourly** du Copernicus Climate Change
Service, converti en mètres.

---

Application non officielle, non affiliée à Météo-France, au Copernicus Climate Change Service ni à
l'ECMWF.
