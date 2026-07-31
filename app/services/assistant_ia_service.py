"""
Service métier — Module Assistant IA.

Nouveau module, ajouté à la demande, qui expose une interface conversationnelle
regroupant 4 capacités :

  1. Détection d'anomalies : résumé des anomalies récentes / en attente
     de traitement (s'appuie sur `anomalie_service`).
  2. Prévisions : lecture en langage naturel du tableau de bord prédictif
     déjà existant (`bi_service.prevision`, régression linéaire simple).
  3. Rapport auto : génère à la demande un rapport (PDF/Excel) via
     `rapport_service.generer_rapport`.
  4. Question RH : questions libres sur les effectifs, la présence, le
     classement des agents/services (s'appuie sur `agent_service`,
     `service_service` et `bi_service`).

Volontairement, ce module n'introduit AUCUNE dépendance externe (pas d'appel
à une API d'IA générative tierce) : il s'agit d'un moteur d'intentions par
mots-clés qui réutilise et met en forme les calculs déjà réalisés par les
services métier existants — cohérent avec le choix déjà fait pour le module
BI (« méthodes statistiques simples », cf. `bi_service.prevision`), et plus
prudent pour un système qui manipule des données RH sensibles.

Le RBAC est respecté au niveau de chaque intention plutôt qu'au niveau de
l'endpoint : un utilisateur actif quelconque peut interroger l'assistant,
mais une question qui requiert une capacité protégée (BI, génération de
rapports) reçoit une réponse explicite s'il n'a pas la permission requise,
plutôt qu'une erreur HTTP brute — l'assistant reste conversationnel même
quand il refuse.
"""
from datetime import date as date_
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.enums import FormatRapport, StatutJustification, TypeAnomalie, TypePeriode
from app.models.utilisateur import Utilisateur
from app.services import agent_service, anomalie_service, bi_service, journal_audit_service, rapport_service
from app.ml import intent_classifier

# ----------------------------------------------------------------------
# Détection d'intention (mots-clés, insensible à la casse/accents partiels)
# ----------------------------------------------------------------------

_MOTS_RAPPORT = ("rapport", "génère", "genere", "exporte", "export", "télécharge", "telecharge", "pdf", "excel")
_MOTS_PREVISION = ("prévision", "prevision", "prévoir", "prevoir", "prédi", "predi", "tendance", "évolution", "evolution", "futur")
_MOTS_ANOMALIES = ("anomalie", "anomalies")
_MOTS_RETARD = ("retard", "retardataire", "retardataires")
_MOTS_ABSENCE = ("absent", "absents", "absence", "absences")
_MOTS_DEPART = ("départ anticipé", "depart anticipe", "départs anticipés", "depart anticipes")
_MOTS_EFFECTIF = ("combien d'agent", "combien d agent", "effectif", "nombre d'agent", "nombre d agent")
_MOTS_CLASSEMENT_PONCTUEL = ("plus ponctuel", "plus ponctuelle", "meilleur agent", "meilleurs agents")
_MOTS_CLASSEMENT_RETARD = ("plus souvent en retard", "le plus de retard", "les plus de retard")
_MOTS_SERVICE_COMPARAISON = ("meilleur service", "quel service", "comparaison des services", "compare les services")
_MOTS_PRESENCE = ("taux de présence", "taux de presence", "présence aujourd'hui", "presence aujourd hui")


def _contient(message: str, mots: tuple) -> bool:
    return any(mot in message for mot in mots)


_MOTS_RISQUE = ("risque", "risquent", "à surveiller", "a surveiller", "score de risque")


def _detecter_intention_mots_cles(message: str) -> str:
    """
    Moteur d'intention à mots-clés (approche d'origine) : sert de filet de
    sécurité lorsque le classifieur ML (`intent_classifier`) n'est pas
    assez confiant sur le message reçu.
    """
    m = message.lower().strip()
    if _contient(m, _MOTS_RAPPORT):
        return "rapport"
    if _contient(m, _MOTS_PREVISION):
        return "prevision"
    if _contient(m, _MOTS_RISQUE):
        return "risque"
    # Une question sur les anomalies EN GÉNÉRAL (pas un classement d'agent
    # précis, cf. question_rh) déclenche le résumé du module Anomalies.
    if _contient(m, _MOTS_ANOMALIES) or (
        (_contient(m, _MOTS_RETARD) or _contient(m, _MOTS_ABSENCE) or _contient(m, _MOTS_DEPART))
        and not _contient(m, _MOTS_CLASSEMENT_RETARD)
        and "qui" not in m
        and "quel agent" not in m
    ):
        return "anomalies"
    if m in ("", "aide", "help", "?", "que peux-tu faire", "que peux tu faire"):
        return "aide"
    return "question_rh"


def detecter_intention(message: str) -> str:
    """
    Détection d'intention : le classifieur ML (TF-IDF + régression
    logistique, entraîné en local sur un petit jeu de phrases types) est
    utilisé en premier, car il reconnaît des formulations qui ne contiennent
    pas exactement les mots-clés attendus. En cas de confiance insuffisante
    (`intent_classifier.SEUIL_CONFIANCE`) ou de message vide, on retombe sur
    le moteur à mots-clés, plus prévisible.
    """
    m = message.strip()
    if not m:
        return "aide"

    intention_ml, confiance = intent_classifier.predire(m)
    if confiance >= intent_classifier.SEUIL_CONFIANCE:
        return intention_ml
    return _detecter_intention_mots_cles(m)


def _a_permission(utilisateur: Utilisateur, nom_permission: str) -> bool:
    return nom_permission in {p.nom_permission for p in utilisateur.role.permissions}


_ACTIONS_PAR_DEFAUT = [
    {"libelle": "Résumé des anomalies", "intention": "anomalies"},
    {"libelle": "Prévisions de présence", "intention": "prevision"},
    {"libelle": "Agents à risque", "intention": "risque"},
    {"libelle": "Générer un rapport", "intention": "rapport"},
    {"libelle": "Poser une question RH", "intention": "question_rh"},
]


# ----------------------------------------------------------------------
# 1. Détection d'anomalies
# ----------------------------------------------------------------------

def _repondre_anomalies(db: Session, id_service: Optional[int]) -> dict:
    aujourdhui = date_.today()
    anomalies_recentes, total_recentes = anomalie_service.lister_anomalies(
        db, id_service=id_service, date_debut=aujourdhui - timedelta(days=30), date_fin=aujourdhui, limit=200
    )
    en_attente, total_en_attente = anomalie_service.lister_anomalies(
        db, id_service=id_service, statut_justification=StatutJustification.EN_ATTENTE, limit=200
    )

    compte_par_type = {TypeAnomalie.RETARD: 0, TypeAnomalie.ABSENCE: 0, TypeAnomalie.DEPART_ANTICIPE: 0}
    for a in anomalies_recentes:
        compte_par_type[a.type_anomalie] = compte_par_type.get(a.type_anomalie, 0) + 1

    lignes = [
        f"- {compte_par_type[TypeAnomalie.RETARD]} retard(s)",
        f"- {compte_par_type[TypeAnomalie.ABSENCE]} absence(s)",
        f"- {compte_par_type[TypeAnomalie.DEPART_ANTICIPE]} départ(s) anticipé(s)",
    ]
    perimetre = f" pour ce service" if id_service is not None else ""
    texte = (
        f"Sur les 30 derniers jours{perimetre}, {total_recentes} anomalie(s) ont été détectées :\n"
        + "\n".join(lignes)
        + f"\n\n{total_en_attente} anomalie(s) sont actuellement en attente de traitement"
        + (" (toutes périodes confondues)." if total_en_attente else ".")
    )
    if total_en_attente:
        texte += " Je vous conseille de les traiter depuis le module Anomalies."

    return {
        "reponse": texte,
        "donnees": {
            "periode_debut": (aujourdhui - timedelta(days=30)).isoformat(),
            "periode_fin": aujourdhui.isoformat(),
            "total_periode": total_recentes,
            "par_type": {k.value: v for k, v in compte_par_type.items()},
            "total_en_attente": total_en_attente,
        },
    }


# ----------------------------------------------------------------------
# 2. Prévisions
# ----------------------------------------------------------------------

def _repondre_prevision(db: Session, utilisateur: Utilisateur, id_service: Optional[int]) -> dict:
    if not _a_permission(utilisateur, "consulter_bi"):
        return {
            "reponse": (
                "Les prévisions font partie du tableau de bord décisionnel (BI), "
                "réservé aux profils Administrateur et Chef de service. "
                "Rapprochez-vous d'un chef de service pour consulter cette information."
            ),
            "donnees": None,
        }

    # Même fonction que celle utilisée par le tableau de bord BI
    # (`biService.previsionMl` côté frontend) : évite qu'une même période
    # donne deux tendances différentes selon qu'on la consulte via le
    # dashboard ou via l'assistant (l'ancienne version de l'assistant
    # appelait `bi_service.prevision`, la régression linéaire simple, alors
    # que le dashboard utilise déjà la variante gradient boosting avec repli
    # automatique — source d'incohérence entre les deux vues).
    resultat = bi_service.prevision_ml(
        db, TypePeriode.MOIS, id_service=id_service, nombre_periodes_historique=6, horizon=3
    )
    points = resultat["prevision"]
    taux_valides = [p["taux_presence_estime"] for p in points if p.get("taux_presence_estime") is not None]

    if not taux_valides:
        texte = (
            "Historique insuffisant pour établir une prévision fiable sur ce périmètre "
            "(il faut au moins 2 périodes complètes avec des données)."
        )
    else:
        premier, dernier = taux_valides[0] * 100, taux_valides[-1] * 100
        if dernier > premier + 1:
            tendance = "en hausse"
        elif dernier < premier - 1:
            tendance = "en baisse"
        else:
            tendance = "stable"
        details = "; ".join(
            f"{p['periode_debut']} → {round(p['taux_presence_estime'] * 100, 1)}%"
            for p in points
            if p.get("taux_presence_estime") is not None
        )
        methode_lisible = (
            "un modèle de gradient boosting entraîné sur l'historique récent"
            if resultat["methode"] == "gradient_boosting_ml"
            else "une régression linéaire simple (repli, historique insuffisant pour le modèle ML)"
        )
        texte = (
            f"D'après {methode_lisible} sur les 6 derniers mois, le taux de présence "
            f"est estimé {tendance} sur les 3 prochains mois : {details}.\n\n"
            f"{resultat['avertissement']}"
        )

    return {"reponse": texte, "donnees": resultat}


# ----------------------------------------------------------------------
# 2 bis. Score de risque par agent (machine learning)
# ----------------------------------------------------------------------

def _repondre_risque(db: Session, utilisateur: Utilisateur, id_service: Optional[int]) -> dict:
    if not _a_permission(utilisateur, "consulter_bi"):
        return {
            "reponse": (
                "Le score de risque par agent fait partie du tableau de bord décisionnel (BI), "
                "réservé aux profils Administrateur et Chef de service. "
                "Rapprochez-vous d'un chef de service pour consulter cette information."
            ),
            "donnees": None,
        }

    scores = bi_service.score_risque_agents(db, id_service=id_service)
    if not scores:
        return {
            "reponse": "Aucun agent avec un historique suffisant sur ce périmètre pour établir un score de risque.",
            "donnees": None,
        }

    top = scores[:5]
    lignes = [
        f"{i+1}. {a['prenom']} {a['nom']} — risque estimé : {round(a['score_risque'] * 100, 1)} %"
        for i, a in enumerate(top)
    ]
    methode_brute = top[0]["methode"]
    if methode_brute == "gradient_boosting_ml":
        methode_lisible = "modèle prédictif (gradient boosting) entraîné sur l'historique du périmètre"
    else:
        methode_lisible = (
            "estimation simplifiée sans apprentissage automatique, faute d'historique suffisant "
            "sur ce périmètre pour entraîner un modèle fiable"
        )
    reste = len(scores) - len(top)
    complement = f" ({reste} autre(s) agent(s) évalué(s) avec un score plus faible.)" if reste > 0 else ""
    texte = (
        f"Sur {len(scores)} agent(s) évalué(s), voici ceux avec le risque estimé le plus élevé "
        "de retard ou d'absence sur la période à venir :\n"
        + "\n".join(lignes)
        + f"{complement}\n\n(méthode : {methode_lisible})"
    )
    return {"reponse": texte, "donnees": {"scores": scores}}


# ----------------------------------------------------------------------
# 3. Rapport auto
# ----------------------------------------------------------------------

_PERIODES_MOTS = {
    TypePeriode.JOUR: ("jour", "journalier", "quotidien", "aujourd'hui", "aujourd hui"),
    TypePeriode.SEMAINE: ("semaine", "hebdomadaire"),
    TypePeriode.MOIS: ("mois", "mensuel"),
    TypePeriode.ANNEE: ("année", "annee", "annuel"),
}


def _detecter_periode(message: str) -> TypePeriode:
    for periode, mots in _PERIODES_MOTS.items():
        if _contient(message, mots):
            return periode
    return TypePeriode.MOIS


def _detecter_format(message: str) -> FormatRapport:
    if "excel" in message or "xlsx" in message or "tableur" in message:
        return FormatRapport.EXCEL
    return FormatRapport.PDF


def _repondre_rapport(db: Session, utilisateur: Utilisateur, message: str, id_service: Optional[int]) -> dict:
    if not _a_permission(utilisateur, "generer_rapports"):
        return {
            "reponse": (
                "La génération de rapports est réservée aux profils Secrétaire et Administrateur. "
                "Je ne peux pas générer ce rapport avec votre profil actuel."
            ),
            "donnees": None,
        }

    type_periode = _detecter_periode(message)
    format_rapport = _detecter_format(message)

    rapport = rapport_service.generer_rapport(
        db,
        type_periode=type_periode,
        format_rapport=format_rapport,
        id_service=id_service,
        id_utilisateur=utilisateur.id_utilisateur,
    )
    periode_debut, periode_fin = rapport_service.bornes_depuis_rapport(rapport)

    texte = (
        f"Rapport {type_periode.value} généré au format {format_rapport.value.upper()} "
        f"({periode_debut.isoformat()} → {periode_fin.isoformat()})."
        " Vous pouvez le télécharger depuis le module Rapports."
    )
    return {
        "reponse": texte,
        "donnees": {
            "id_rapport": rapport.id_rapport,
            "type_periode": type_periode.value,
            "format": format_rapport.value,
            "periode_debut": periode_debut.isoformat() if periode_debut else None,
            "periode_fin": periode_fin.isoformat() if periode_fin else None,
            "url_telechargement": f"/rapports/{rapport.id_rapport}/telecharger",
        },
    }


# ----------------------------------------------------------------------
# 4. Question RH (questions libres)
# ----------------------------------------------------------------------

def _repondre_question_rh(db: Session, utilisateur: Utilisateur, message: str, id_service: Optional[int]) -> dict:
    m = message.lower()
    hier = date_.today() - timedelta(days=1)

    if _contient(m, _MOTS_EFFECTIF):
        _, total = agent_service.list_agents(db, id_service=id_service, limit=1)
        perimetre = "sur ce service" if id_service is not None else "au total"
        return {"reponse": f"{total} agent(s) enregistré(s) {perimetre}.", "donnees": {"total_agents": total}}

    if _contient(m, _MOTS_PRESENCE):
        tdb = bi_service.tableau_de_bord_temps_reel(db, id_service=id_service)
        taux = tdb["taux_presence"]
        taux_pct = round(taux * 100, 1) if taux is not None else None
        texte = (
            f"Le {tdb['jour'].isoformat()}, le taux de présence est de "
            f"{taux_pct if taux_pct is not None else 'indisponible (aucun agent attendu)'}"
            f"{'%' if taux_pct is not None else ''} "
            f"({tdb['nombre_presents']} présent(s), {tdb['nombre_absents']} absent(s), "
            f"{tdb['nombre_retardataires']} retardataire(s) sur {tdb['nombre_agents_attendus']} agent(s) attendu(s))."
        )
        return {"reponse": texte, "donnees": tdb}

    if _contient(m, _MOTS_CLASSEMENT_RETARD) or (_contient(m, _MOTS_RETARD) and "qui" in m):
        if not _a_permission(utilisateur, "consulter_bi"):
            return {
                "reponse": "Ce classement fait partie du tableau de bord BI, réservé aux Administrateurs et Chefs de service.",
                "donnees": None,
            }
        classement = bi_service.classement_agents(
            db, hier.replace(day=1), hier, id_service=id_service, critere="retards", limite=5
        )
        if not classement:
            return {"reponse": "Aucune donnée suffisante ce mois-ci pour établir ce classement.", "donnees": None}
        lignes = [f"{i+1}. {a['prenom']} {a['nom']} ({a['nombre_retards']} retard(s))" for i, a in enumerate(classement)]
        return {
            "reponse": "Agents les plus souvent en retard ce mois-ci :\n" + "\n".join(lignes),
            "donnees": {"classement": classement},
        }

    if _contient(m, _MOTS_CLASSEMENT_PONCTUEL):
        if not _a_permission(utilisateur, "consulter_bi"):
            return {
                "reponse": "Ce classement fait partie du tableau de bord BI, réservé aux Administrateurs et Chefs de service.",
                "donnees": None,
            }
        classement = bi_service.classement_agents(
            db, hier.replace(day=1), hier, id_service=id_service, critere="ponctualite", limite=5
        )
        if not classement:
            return {"reponse": "Aucune donnée suffisante ce mois-ci pour établir ce classement.", "donnees": None}
        lignes = [
            f"{i+1}. {a['prenom']} {a['nom']} "
            f"(taux de présence : {round(a['taux_presence'] * 100, 1) if a['taux_presence'] is not None else 'n/d'}%)"
            for i, a in enumerate(classement)
        ]
        return {
            "reponse": "Agents les plus ponctuels ce mois-ci :\n" + "\n".join(lignes),
            "donnees": {"classement": classement},
        }

    if _contient(m, _MOTS_SERVICE_COMPARAISON):
        if not _a_permission(utilisateur, "consulter_bi"):
            return {
                "reponse": "La comparaison entre services fait partie du tableau de bord BI, réservé aux Administrateurs et Chefs de service.",
                "donnees": None,
            }
        comparaison = bi_service.comparaison_services(db, TypePeriode.MOIS)
        services = comparaison["services"]
        if not services:
            return {"reponse": "Aucun service avec des données suffisantes ce mois-ci.", "donnees": None}
        meilleur = services[0]
        taux_meilleur = (
            round(meilleur["taux_presence"] * 100, 1) if meilleur["taux_presence"] is not None else "n/d"
        )
        return {
            "reponse": (
                f"Le service le plus assidu ce mois-ci est « {meilleur['nom_service']} » "
                f"(taux de présence : {taux_meilleur}%)."
            ),
            "donnees": {"comparaison": comparaison},
        }

    # Repli : question non reconnue — on rappelle les capacités disponibles.
    return {
        "reponse": (
            "Je n'ai pas identifié précisément votre question RH. Je peux répondre par exemple à : "
            "« combien d'agents avons-nous », « quel est le taux de présence aujourd'hui », "
            "« quels agents sont les plus souvent en retard » ou « quel service a le meilleur taux de présence »."
        ),
        "donnees": None,
    }


# ----------------------------------------------------------------------
# Point d'entrée
# ----------------------------------------------------------------------

def _reponse_aide() -> dict:
    return {
        "reponse": (
            "Je suis l'assistant du système de pointage. Je peux :\n"
            "- résumer les anomalies récentes (retards, absences, départs anticipés) ;\n"
            "- donner une prévision de présence sur les prochains mois ;\n"
            "- signaler les agents à risque de retard/absence (score prédictif) ;\n"
            "- générer un rapport (jour/semaine/mois/année, PDF ou Excel) ;\n"
            "- répondre à des questions RH sur les effectifs et la présence.\n"
            "Posez votre question, ou utilisez les boutons ci-dessous."
        ),
        "donnees": None,
    }


def traiter_message(db: Session, utilisateur: Utilisateur, message: str, id_service: Optional[int] = None) -> dict:
    intention = detecter_intention(message)

    if intention == "anomalies":
        resultat = _repondre_anomalies(db, id_service)
    elif intention == "prevision":
        resultat = _repondre_prevision(db, utilisateur, id_service)
    elif intention == "risque":
        resultat = _repondre_risque(db, utilisateur, id_service)
    elif intention == "rapport":
        resultat = _repondre_rapport(db, utilisateur, message, id_service)
    elif intention == "question_rh":
        resultat = _repondre_question_rh(db, utilisateur, message, id_service)
    else:
        intention = "aide"
        resultat = _reponse_aide()

    journal_audit_service.log_action(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        action="assistant_ia",
        details=f"intention={intention} message={message[:200]!r}",
    )

    return {
        "intention": intention,
        "reponse": resultat["reponse"],
        "donnees": resultat["donnees"],
        "actions_suggerees": [a for a in _ACTIONS_PAR_DEFAUT if a["intention"] != intention],
    }