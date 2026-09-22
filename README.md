# Phone Ringtones — statistiques

Le petit service qui reçoit les statistiques anonymes de l'app Phone Ringtones et
affiche la console. Tourne sur un NAS personnel, en France.

- Ce qu'il accepte : `stats/vocabulary.py`, en entier. Tout le reste est refusé.
- Ce qu'il garde : 13 mois au plus (`stats/store.py`).
- Pas d'adresse IP conservée, pas de journal d'accès.

Tests : `uv run pytest`. Déploiement : voir `compose.nas.yaml`.
