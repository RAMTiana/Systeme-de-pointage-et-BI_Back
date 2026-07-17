# Modifications à appliquer à `app/services/pointage_service.py`

Objectif : persister `motif_sortie` et `commentaire_motif` reçus dans les 3 payloads
(`PointageQrBadgeCreate`, `PointageFacialCreate`, `PointageWebAuthnCreate`).

## 1) Helper à ajouter en haut du fichier (après les imports)

```python
def _champs_motif(payload) -> dict:
    """
    Extrait motif_sortie/commentaire_motif du payload pour les passer au
    constructeur `Pointage(...)`. Les schémas Pydantic garantissent déjà que
    ces champs valent None pour une entrée (cf. _MotifSortieMixin).
    """
    motif = getattr(payload, "motif_sortie", None)
    return {
        "motif_sortie": motif.value if motif is not None else None,
        "commentaire_motif": getattr(payload, "commentaire_motif", None),
    }
```

## 2) Injection dans chacun des 4 `Pointage(...)` du fichier

Dans `pointer_qr_badge` (~ligne 210), `pointer_facial` (branche rejet ~ligne 250 ET
branche succès ~ligne 264), et `pointer_webauthn` (~ligne 362), remplacer chaque
bloc :

```python
pointage = Pointage(
    id_agent=agent.id_agent,
    date_heure=maintenant,
    type_pointage=payload.type_pointage,
    mode_pointage=<...>,
    statut=<...>,
)
```

par :

```python
pointage = Pointage(
    id_agent=agent.id_agent,
    date_heure=maintenant,
    type_pointage=payload.type_pointage,
    mode_pointage=<...>,
    statut=<...>,
    **_champs_motif(payload),
)
```

Aucune autre logique métier ne change : les anomalies (retard / départ anticipé)
et la détection de doublon restent inchangées.
```
