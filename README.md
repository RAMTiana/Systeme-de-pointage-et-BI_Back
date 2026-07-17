# Systeme-de-pointage-et-BI_Back
# SRB Haute Matsiatra — Backend API

Backend FastAPI du système de pointage électronique et d'aide à la décision (BI)
du Service Régional du Budget (SRB) — Haute Matsiatra.

Stack : **FastAPI · SQLAlchemy 2.0 · PostgreSQL · Alembic**

## 1. Structure du projet

```
srb_backend/
├── alembic/                   # Migrations de base de données
│   ├── versions/               # Fichiers de migration générés
│   ├── env.py                  # Config Alembic branchée sur app.core.config
│   └── script.py.mako
├── alembic.ini
├── app/
│   ├── main.py                 # Point d'entrée FastAPI
│   ├── core/
│   │   └── config.py           # Settings (lues depuis .env)
│   ├── db/
│   │   ├── base_class.py       # Base déclarative SQLAlchemy
│   │   └── session.py          # Engine, SessionLocal, dépendance get_db
│   ├── models/                  # Modèles ORM (1 fichier par table/regroupement)
│   │   ├── enums.py             # Enums Python <-> types PostgreSQL
│   │   ├── service.py, agent.py, affectation.py, biometrie.py
│   │   ├── rbac.py              # Role, Permission, role_permission
│   │   ├── utilisateur.py, code_verification.py
│   │   ├── pointage.py, anomalie.py, justificatif.py, alerte.py
│   │   ├── rapport.py, journal_audit.py
│   │   └── horaire_reference.py, parametre_systeme.py
│   ├── api/                     # Routeurs (à venir : auth, agents, pointage...)
│   ├── schemas/                 # Schémas Pydantic (à venir)
│   └── services/                # Logique métier (à venir)
├── requirements.txt
└── .env.example
```

Cette organisation suit une séparation classique **modèles / schémas / services / API**,
qui facilite l'ajout des prochains modules (authentification JWT, CRUD agents,
pointage, anomalies, BI...) sans toucher à l'existant.

## 2. Installation

```bash
cd srb_backend
python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# -> éditer .env avec les identifiants de votre PostgreSQL local
```

## 3. Base de données

Créer la base et l'utilisateur PostgreSQL (adapter selon votre environnement) :

```sql
CREATE USER srb_user WITH PASSWORD 'changeme';
CREATE DATABASE srb_haute_matsiatra OWNER srb_user;
GRANT ALL ON SCHEMA public TO srb_user;
```

Appliquer les migrations (crée les 17 tables + 13 types ENUM du schéma) :

```bash
alembic upgrade head
```

Peupler les données de référence — rôles, permissions et paramètres système
par défaut (cf. §15, obligatoire : sans cette étape, aucun compte
utilisateur ne peut être créé faute de rôle existant) :

```bash
python -m scripts.seed_reference_data
```

## 4. Lancer l'API

```bash
uvicorn app.main:app --reload
```

- Documentation interactive : http://localhost:8000/api/v1/docs
- Health check : http://localhost:8000/api/v1/health

## 5. Workflow des migrations (Alembic)

Le principe retenu : **les modèles SQLAlchemy (`app/models/`) sont la source de
vérité**. On ne réimporte pas le script `.sql` fourni tel quel — chaque
évolution du modèle de données passe par une migration générée automatiquement :

```bash
# Après avoir modifié un modèle (ajout de colonne, nouvelle table, etc.)
alembic revision --autogenerate -m "description du changement"

# Relire le fichier généré dans alembic/versions/ avant de l'appliquer
# (l'autogénération n'est jamais fiable à 100%, notamment pour les
#  renommages de colonnes ou certaines contraintes CHECK)

alembic upgrade head        # appliquer
alembic downgrade -1        # revenir en arrière d'un cran si besoin
```

### Point d'attention : les types ENUM PostgreSQL

Alembic autogenerate crée bien les `CREATE TYPE` automatiquement à l'`upgrade`,
mais **n'émet pas les `DROP TYPE` correspondants au `downgrade`**. La migration
initiale (`init schema`) contient donc un bloc explicite de nettoyage des types
enum en fin de `downgrade()`. Pensez à faire de même pour toute future migration
qui ajoute un nouveau type enum.

## 6. Ce qui a déjà été vérifié dans cet environnement

- Import de l'application et des 17 modèles ORM sans erreur.
- Génération de la migration initiale par autogénération (`alembic revision --autogenerate`).
- Application (`upgrade head`) contre une vraie instance PostgreSQL : 17 tables
  + 13 types enum créés, correspondant exactement au schéma SQL d'origine.
- Rollback complet (`downgrade base`) : tables et types enum supprimés proprement.

## 7. Module Authentification JWT + RBAC (livré)

```
app/core/security.py          # hachage bcrypt + émission/validation JWT
app/schemas/token.py           # Token, TokenPayload, RefreshRequest
app/schemas/utilisateur.py     # UtilisateurOut, RoleOut, PermissionOut, GoogleLoginRequest
app/services/utilisateur_service.py    # lecture utilisateur (+ rôle/permissions préchargés)
app/services/journal_audit_service.py  # écriture dans journal_audit
app/services/auth_service.py           # login local, login Google, refresh
app/api/deps.py                        # get_current_user, get_current_active_user, require_permission
app/api/v1/auth.py                     # routeur /auth
```

### Endpoints

| Méthode | Route                  | Description |
|---------|-------------------------|--------------|
| POST    | `/api/v1/auth/login`    | Connexion locale (form OAuth2 `username`/`password` — accepte login ou email) |
| POST    | `/api/v1/auth/google`   | Connexion via Google Sign-In (`{"id_token": "..."}`) |
| POST    | `/api/v1/auth/refresh`  | Renouvelle l'access token à partir d'un refresh token |
| GET     | `/api/v1/auth/me`       | Profil courant (rôle + permissions), protégé par JWT |

### Choix de conception

- **bcrypt direct plutôt que passlib** : passlib n'est plus maintenu et sa
  détection de backend est cassée par les versions récentes de `bcrypt`
  (`bcrypt.__about__` supprimé). `app/core/security.py` appelle `bcrypt`
  directement.
- **Permissions rechargées depuis la base à chaque requête** (pas embarquées
  dans le JWT) : une révocation de permission par un administrateur est donc
  effective immédiatement, sans attendre l'expiration du token.
- **Google Sign-In sans auto-inscription** : un `id_token` Google valide doit
  correspondre à un compte déjà créé par un administrateur (correspondance par
  email, puis liaison du `google_id` pour les connexions suivantes). Cela
  respecte le RBAC : un rôle est toujours attribué explicitement, jamais
  déduit d'un fournisseur d'identité externe. Nécessite `GOOGLE_CLIENT_ID`
  dans `.env` ; sans lui, `/auth/google` répond `501 Not Implemented`.
- **`require_permission("nom_permission")`** protège un endpoint selon le RBAC
  (table `role_permission`) :
  ```python
  from app.api.deps import require_permission

  @router.post("/agents")
  def creer_agent(utilisateur = Depends(require_permission("gerer_agents"))):
      ...
  ```
- Chaque connexion (réussie, échouée, désactivée) est journalisée dans
  `journal_audit`, conformément au cahier des charges (traçabilité complète).

### Vérifié dans cet environnement

- Hachage/vérification bcrypt et cycle de vie complet des JWT (access + refresh,
  y compris rejet d'un token corrompu ou du mauvais type sur `/refresh`).
- Flux complet testé de bout en bout (base SQLite en mémoire + `TestClient`) :
  login par login ou email, échec sur mauvais mot de passe, `/me` avec et sans
  token, refresh, rejet d'un compte désactivé (403).
- `require_permission` : accès refusé (403) à un rôle sans la permission,
  accès autorisé pour un rôle qui la possède.
- Endpoint `/auth/google` : réponses correctes en configuration absente (501),
  jeton invalide (401), et erreur réseau/transport lors de la vérification
  auprès de Google (503, distinct d'un jeton invalide).

## 8. Module Agents/Services (livré)

```
app/schemas/service.py           # ServiceCreate, ServiceUpdate, ServiceOut, ServiceLight
app/schemas/agent.py             # AgentCreate, AgentUpdate, AgentOut, AgentDetailOut,
                                  # AffectationCreate, AffectationOut, ConsentementFacialUpdate
app/schemas/common.py            # Page[T] — pagination générique réutilisable
app/services/service_service.py  # CRUD Service (+ comptage d'agents rattachés)
app/services/agent_service.py    # CRUD Agent, recherche paginée, statut, consentement facial,
                                  # gestion des affectations secondaires
app/api/v1/services.py           # routeur /services
app/api/v1/agents.py             # routeur /agents
```

### Endpoints

| Méthode | Route | Description | Protection |
|---------|-------|--------------|------------|
| GET | `/api/v1/services` | Liste des services (filtre `recherche`) | utilisateur actif |
| GET | `/api/v1/services/{id}` | Détail d'un service | utilisateur actif |
| POST | `/api/v1/services` | Créer un service | `gerer_services` |
| PATCH | `/api/v1/services/{id}` | Modifier un service | `gerer_services` |
| DELETE | `/api/v1/services/{id}` | Supprimer un service | `gerer_services` |
| GET | `/api/v1/agents` | Recherche paginée (`recherche`, `id_service`, `statut`, `skip`, `limit`) | utilisateur actif |
| GET | `/api/v1/agents/{id}` | Fiche complète (service + historique des affectations) | utilisateur actif |
| POST | `/api/v1/agents` | Créer un agent | `gerer_agents` |
| PATCH | `/api/v1/agents/{id}` | Modifier un agent | `gerer_agents` |
| POST | `/api/v1/agents/{id}/desactiver` | Désactiver un agent | `gerer_agents` |
| POST | `/api/v1/agents/{id}/reactiver` | Réactiver un agent | `gerer_agents` |
| PUT | `/api/v1/agents/{id}/consentement-facial` | Enregistrer le consentement RGPD à la reconnaissance faciale | `gerer_agents` |
| DELETE | `/api/v1/agents/{id}` | Suppression physique (cas exceptionnel) | `gerer_agents` |
| POST | `/api/v1/agents/{id}/affectations` | Ajouter un rattachement secondaire à un service | `gerer_agents` |
| DELETE | `/api/v1/agents/{id}/affectations/{id_affectation}` | Clôturer un rattachement (renseigne `date_fin`) | `gerer_agents` |

### Choix de conception

- **Lecture ouverte, écriture protégée** : tout utilisateur authentifié et actif
  peut lister/consulter agents et services (utile pour peupler des formulaires
  côté Angular, ex. un menu déroulant "service"). Seules les opérations
  d'écriture exigent la permission RBAC dédiée (`gerer_agents` / `gerer_services`).
- **Désactivation plutôt que suppression** : conformément au cahier des charges
  (traçabilité), le cycle de vie normal d'un agent passe par `statut = desactive`,
  qui conserve tout l'historique (pointages, anomalies). La suppression physique
  (`DELETE /agents/{id}`) reste possible mais est documentée comme un cas
  exceptionnel (erreur de saisie), car elle supprime en cascade l'empreinte
  biométrique, les pointages et les anomalies de l'agent.
- **`service_principal` vs affectations secondaires** : `agent.id_service` porte
  le service principal de l'agent (modifiable via `PATCH /agents/{id}`) ; la
  table `affectation` sert uniquement aux rattachements **secondaires**
  multi-services évoqués dans le cahier des charges ("affecter les agents à un
  ou plusieurs services"). Clôturer une affectation renseigne `date_fin` sans
  supprimer la ligne, pour conserver l'historique.
- **Suppression d'un service** : les agents rattachés ne sont pas supprimés,
  `id_service` repasse à `NULL` (`ON DELETE SET NULL`) ; en revanche les
  affectations secondaires vers ce service sont bien supprimées en cascade
  (`ON DELETE CASCADE`), cohérence assurée côté ORM par
  `cascade="all, delete-orphan"` sur `Service.affectations`.
- **Pagination générique** (`app/schemas/common.py::Page[T]`) réutilisable par
  les futurs modules (pointage, anomalies, rapports...).

### Correctif appliqué en marge de ce module

- `app/models/service.py` : ajout de `cascade="all, delete-orphan"` sur la
  relation `Service.affectations`. Sans cette cascade, SQLAlchemy tentait par
  défaut de mettre `affectation.id_service` à `NULL` lors de la suppression
  d'un service, ce qui violait la contrainte `NOT NULL` du schéma SQL (la
  cascade `ON DELETE CASCADE` est bien définie en base, mais l'ORM ne la
  déduit pas automatiquement sans cette annotation).
- `requirements.txt` : ajout de `requests` (dépendance transitive non
  déclarée de `google.auth.transport.requests`, utilisée par le module
  d'authentification Google) — l'import de l'application échouait sans elle.

### Vérifié dans cet environnement

- Import complet de l'application avec les nouveaux routeurs.
- Flux de bout en bout testé (SQLite en mémoire + `TestClient`) : CRUD complet
  services et agents, doublons (`matricule`, `nom_service` → 409), RBAC
  (403 sans permission, lecture ouverte), recherche/filtres/pagination,
  activation/désactivation, consentement facial, ajout et clôture d'une
  affectation secondaire, suppression d'un service (agents détachés en
  `NULL`, affectations supprimées), suppression physique d'un agent.

## 9. Module Utilisateurs (livré)

```
app/schemas/utilisateur.py              # + RoleLight, UtilisateurCreate, UtilisateurUpdate,
                                          #   UtilisateurAdminOut, RoleChangeRequest, MotDePasseAdminUpdate
app/services/utilisateur_service.py     # + CRUD comptes, pagination/recherche, rôles,
                                          #   activation/désactivation, réinitialisation mot de passe
app/api/v1/utilisateurs.py               # routeur /utilisateurs
```

### Endpoints

Tous protégés par la permission RBAC `valider_roles` (module réservé aux administrateurs).

| Méthode | Route | Description |
|---------|-------|--------------|
| GET | `/api/v1/utilisateurs` | Liste paginée (`recherche`, `id_role`, `actif`, `skip`, `limit`) |
| GET | `/api/v1/utilisateurs/roles` | Rôles disponibles (+ permissions), pour un menu déroulant |
| GET | `/api/v1/utilisateurs/{id}` | Détail d'un compte |
| POST | `/api/v1/utilisateurs` | Créer un compte local (login + mot de passe + rôle) |
| PATCH | `/api/v1/utilisateurs/{id}` | Modifier login / email / nom complet |
| PUT | `/api/v1/utilisateurs/{id}/role` | Changer le rôle attribué |
| POST | `/api/v1/utilisateurs/{id}/desactiver` | Désactiver un compte |
| POST | `/api/v1/utilisateurs/{id}/reactiver` | Réactiver un compte |
| PUT | `/api/v1/utilisateurs/{id}/mot-de-passe` | Réinitialiser le mot de passe (compte local) |
| DELETE | `/api/v1/utilisateurs/{id}` | Suppression physique (cas exceptionnel) |

### Choix de conception

- **Comptes toujours créés en local** : un administrateur crée un compte avec un
  mot de passe ; le rattachement Google (`google_id`) ne se fait qu'à la première
  connexion Google Sign-In réussie sur un compte dont l'email correspond déjà
  (cf. `auth_service.login_google`, module Authentification) — jamais d'auto-inscription.
- **Désactivation plutôt que suppression**, comme pour les agents : `actif = false`
  conserve tout l'historique (journal d'audit, rapports générés, anomalies traitées
  par ce compte). La suppression physique reste possible pour une erreur de saisie,
  et détache proprement les données liées (`ON DELETE SET NULL` sur `rapport` et
  `anomalie.id_utilisateur_traitant`).
- **Garde-fous d'auto-administration** : un administrateur ne peut pas désactiver,
  changer le rôle de, ou supprimer son propre compte — évite qu'il se verrouille
  hors de l'application sans qu'un autre administrateur puisse le rétablir.
- **Réinitialisation de mot de passe réservée aux comptes locaux** : un compte
  `auth_provider = 'google'` n'a pas de mot de passe SRB (contrainte `chk_auth_provider`
  du schéma) ; tenter une réinitialisation renvoie `400`. La procédure libre-service
  ("mot de passe oublié" par email + code à usage unique via `code_verification`)
  reste un module distinct, à venir.
- **Rôle et mot de passe changés via des endpoints dédiés** (`PUT .../role`,
  `PUT .../mot-de-passe`) plutôt que dans le `PATCH` générique : chaque action
  s'écrit avec son propre libellé dans `journal_audit` (`changement_role`,
  `reinitialisation_mot_de_passe`), plus explicite qu'un `modification_compte` générique.
- **Pagination via `Page[UtilisateurAdminOut]`** (`app/schemas/common.py`), déjà
  utilisée en prévision par le module Agents/Services.

## 10. Module Pointage (livré)

Implémente le Processus 1 du BPMN — "Pointage d'un agent" (étapes 1 à 18) :
identification par QR code / badge ou reconnaissance faciale, détection des
doublons, horodatage, journalisation, et calcul immédiat des anomalies
horaires (retard, départ anticipé) par comparaison aux horaires de référence
du service.

```
app/schemas/empreinte.py         # EmpreinteFacialeCreate, EmpreinteFacialeOut
app/schemas/pointage.py          # PointageQrBadgeCreate, PointageFacialCreate, PointageOut, PointageResultat
app/services/parametre_service.py  # lecture typée de parametre_systeme (avec valeurs par défaut)
app/services/empreinte_service.py  # enrôlement / suppression de l'empreinte faciale
app/services/pointage_service.py   # cœur métier du Processus 1
app/api/v1/pointage.py             # routeur /pointage
app/api/v1/agents.py               # + PUT/DELETE /agents/{id}/empreinte-faciale
app/api/deps.py                    # + verify_device_key (clé de dispositif)
```

### Endpoints

| Méthode | Route | Description | Protection |
|---------|-------|--------------|------------|
| POST | `/api/v1/pointage/qr` | Pointage par QR code dynamique | `X-Device-Key` |
| POST | `/api/v1/pointage/badge` | Pointage par badge | `X-Device-Key` |
| POST | `/api/v1/pointage/facial` | Pointage par reconnaissance faciale | `X-Device-Key` |
| GET | `/api/v1/pointage` | Historique paginé (`id_agent`, `id_service`, `type_pointage`, `statut`, `date_debut`, `date_fin`) | utilisateur actif |
| GET | `/api/v1/pointage/{id}` | Détail d'un pointage | utilisateur actif |
| PUT | `/api/v1/agents/{id}/empreinte-faciale` | Enregistrer/remplacer l'empreinte faciale de référence | `gerer_agents` |
| DELETE | `/api/v1/agents/{id}/empreinte-faciale` | Supprimer l'empreinte (désactive le pointage facial) | `gerer_agents` |

### Choix de conception

- **Authentification par clé de dispositif, pas par JWT** : un agent n'a pas
  de compte `utilisateur` dans ce système (RBAC réservé au back-office). Les
  trois endpoints de saisie (`/qr`, `/badge`, `/facial`) sont donc appelés
  par le poste de pointage lui-même et protégés par une clé partagée envoyée
  en en-tête `X-Device-Key` (`DEVICE_API_KEY` dans `.env`), en attendant un
  mécanisme d'authentification de poste plus abouti (mTLS, certificat
  matériel...). La consultation (`GET /pointage`) reste réservée aux
  utilisateurs authentifiés du back-office.
- **Reconnaissance faciale — séparation capture / comparaison** : conformément
  à la conception BPMN ("les tâches automatiques... recevront leurs
  connecteurs techniques... lors de la phase d'implémentation"), ce backend
  ne fait pas l'acquisition caméra ni l'encodage du visage (bibliothèque
  type OpenCV / face-recognition, côté dispositif de capture) : il reçoit un
  **vecteur de caractéristiques** déjà calculé et le compare à l'empreinte de
  référence par distance euclidienne, avec un seuil configurable
  (`seuil_distance_faciale`, paramètre non fourni par défaut → valeur de
  repli 0.6 dans le code). L'image brute n'est jamais transmise ni stockée.
- **Empreinte biométrique jamais exposée** : `EmpreinteFacialeOut` ne renvoie
  que les métadonnées (id, date) — jamais le vecteur — et l'endpoint
  d'enrôlement exige le consentement explicite préalable de l'agent
  (`consentement_facial = true`), conformément au cahier des charges.
- **Traçabilité des tentatives refusées** : une identité non vérifiée ou un
  doublon sont malgré tout persistés (`statut = 'rejete'` / `'doublon'`)
  plutôt qu'ignorés — utile pour repérer des tentatives de fraude répétées —
  puis l'API répond respectivement `401` et `409` à l'appelant.
- **Détection d'anomalie intégrée au pointage (étape 14 du Processus 1)** :
  retard (`entree` après `heure_debut + seuil_retard_minutes`) ou départ
  anticipé (`sortie` avant `heure_fin`), à partir de `horaire_reference` du
  service principal de l'agent. La ligne `anomalie` créée (statut
  `en_attente`) matérialise le message BPMN «MessageAnomalie» vers le
  Processus 3 : dans ce backend monolithique, le lien inter-processus est
  réalisé par une écriture directe en base dans la même transaction plutôt
  que par un événement asynchrone séparé. La *qualification/le traitement*
  de l'anomalie (justificatif, alerte de récidive) reste le module suivant.
  Sans horaire de référence défini pour le service/jour, aucune anomalie
  n'est calculée (pas d'erreur).
- **Doublon** : un second pointage du même type (`entree`/`sortie`) le même
  jour calendaire pour un agent est rejeté (`409`), quel que soit le mode.
- **Agent désactivé** : tout pointage est refusé (`403`) — cohérent avec le
  cycle de vie défini au module Agents.

### Vérifié dans cet environnement

- Import complet de l'application avec le nouveau routeur.
- Flux de bout en bout testé (SQLite en mémoire + `TestClient`) : rejet sans
  clé de dispositif, pointage QR valide avec détection automatique de
  retard, doublon (`409`, persisté), agent inconnu (`404`), refus du
  pointage/de l'enrôlement facial sans consentement ou empreinte (`403`),
  échec d'identité faciale persisté et refusé (`401`), succès facial sans
  anomalie, historique paginé et protégé par JWT (`401` sans jeton).

## 11. Module Anomalies (livré)

Implémente le Processus 3 du BPMN — "Traitement des anomalies" (étapes 1 à
13). La qualification (retard/départ anticipé) et la consignation en base
(étapes 2 et 7) sont déjà réalisées par le module Pointage ; ce module
couvre la suite : seuils et récidive, alerte automatique à la hiérarchie,
examen et justificatif par la secrétaire, ainsi que la détection des
absences (agent sans pointage d'entrée), qui alimente le même processus
mais nécessite un déclenchement planifié plutôt qu'un pointage.

```
app/core/notifications.py         # connecteurs techniques email (SMTP) / SMS (webhook)
app/services/alerte_service.py    # destinataires + contenu + journalisation des alertes
app/services/anomalie_service.py  # seuils/récidive, examen/justificatif, détection des absences
app/schemas/anomalie.py           # AnomalieOut/DetailOut, JustificatifOut, AlerteOut, AnomalieExamenRequest...
app/api/v1/anomalies.py           # routeur /anomalies
app/api/deps.py                   # + verify_job_key (clé de job planifié)
app/services/pointage_service.py  # + appel à anomalie_service.qualifier_et_alerter après consignation
```

### Endpoints

| Méthode | Route | Description | Protection |
|---------|-------|--------------|------------|
| GET | `/api/v1/anomalies` | Historique paginé (`id_agent`, `id_service`, `type_anomalie`, `statut_justification`, `date_debut`, `date_fin`) | utilisateur actif |
| GET | `/api/v1/anomalies/{id}` | Détail (agent, justificatif, alertes envoyées) | utilisateur actif |
| PUT | `/api/v1/anomalies/{id}/examen` | Examen par la secrétaire : justifier (+motif) ou maintenir non justifiée | `traiter_anomalies` |
| POST | `/api/v1/anomalies/detecter-absences` | Job planifié : détecte les absences du jour donné (par défaut hier) | `X-Job-Key` |

### Choix de conception

- **Récidive plutôt qu'alerte systématique** : un retard ou départ anticipé
  isolé n'alerte pas la hiérurchie ; l'alerte se déclenche quand le nombre
  d'anomalies non justifiées du même agent, sur la fenêtre glissante
  configurée (`periode_glissante_jours`), atteint le seuil configuré
  (`seuil_recidive`) — cohérent avec le cahier des charges ("alertes
  automatiques ... en cas de retard répété"). Une **absence**, jugée plus
  sérieuse, alerte en revanche dès la première occurrence. Les anomalies
  déjà justifiées ne comptent pas dans le calcul de récidive.
- **Destinataires des alertes sans lien service → responsable dans le
  schéma d'origine** : le schéma ne relie aucun `utilisateur` à un
  `service` (un chef de service n'est pas rattaché à "son" service en
  base). L'alerte email est donc diffusée à **tous** les comptes actifs de
  rôle "Chef de service" plutôt qu'à un seul destinataire supposé. Le canal
  SMS reste optionnel et se configure via le paramètre système
  `telephone_hierarchie` (liste de numéros séparés par des virgules) —
  réutilisation de la table `parametre_systeme` déjà prévue pour ce type de
  réglage, plutôt qu'un ajout de colonne téléphone au schéma d'origine pour
  un canal facultatif.
- **Aucun envoi simulé** : si `SMTP_HOST` ou `SMS_WEBHOOK_URL` ne sont pas
  configurés, l'alerte correspondante est tout de même consignée en base
  (traçabilité) mais avec `statut = 'echec'`, jamais `'envoyee'` — cohérent
  avec le choix déjà fait pour `/auth/google` (ne jamais prétendre un succès
  que l'infrastructure ne peut pas garantir).
- **Examen non répétable** : `PUT .../examen` renvoie `409` si l'anomalie a
  déjà un statut différent de `en_attente`, pour conserver un historique de
  traitement fiable (pas de changement d'avis silencieux sur une anomalie
  déjà tranchée).
- **Détection des absences en job planifié, pas en tâche synchrone** : un
  agent absent ne pointe jamais, donc rien ne déclenche sa détection au fil
  de l'eau (contrairement au retard/départ anticipé, calculés au moment du
  pointage). L'endpoint est donc pensé pour un ordonnanceur externe (cron
  quotidien), protégé par une clé partagée `X-Job-Key` (même logique que
  `X-Device-Key` pour le poste de pointage) plutôt que par une session
  utilisateur. Un service sans horaire de référence pour le jour contrôlé
  est considéré non travaillé (ex. week-end) et n'est pas contrôlé. Une
  absence déjà consignée pour un agent/jour n'est jamais dupliquée si le job
  est relancé.

### Vérifié dans cet environnement

- Import complet de l'application avec le nouveau routeur.
- Flux de bout en bout testé (SQLite en mémoire + `TestClient`) : pas
  d'alerte à la 1re anomalie isolée, alerte déclenchée à la récidive
  (statut `echec` cohérent en l'absence de SMTP configuré), alerte
  immédiate sur une absence, listing paginé, justification avec
  justificatif enregistré, `409` sur un second examen, `422` si justifiée
  sans motif, `403` sans la permission `traiter_anomalies`, détection des
  absences (agent sans pointage d'entrée), non-duplication en cas de
  relance du job le même jour, `401` sans `X-Job-Key`.
- Intégration avec le module Pointage : un pointage en retard déclenche
  bien `anomalie_service.qualifier_et_alerter` dans la même transaction,
  sans régression sur la réponse de `/pointage/qr`.

## 12. Module Mot de passe oublié (livré)

Procédure libre-service, distincte de la réinitialisation par un
administrateur (module Utilisateurs, §9) : un utilisateur d'un compte local
peut redéfinir lui-même son mot de passe via un code à usage unique envoyé
par email, en utilisant la table `code_verification` déjà prévue au schéma
d'origine.

```
app/services/code_verification_service.py  # génération/validation générique des codes OTP
app/services/mot_de_passe_service.py       # orchestration métier (anti-énumération)
app/schemas/mot_de_passe.py                # MotDePasseOublieRequest, ReinitialiserMotDePasseRequest, MessageResponse
app/api/v1/auth.py                         # + POST /auth/mot-de-passe-oublie, /auth/reinitialiser-mot-de-passe
```

### Endpoints

| Méthode | Route | Description | Protection |
|---------|-------|--------------|------------|
| POST | `/api/v1/auth/mot-de-passe-oublie` | Envoie un code à 6 chiffres par email | publique |
| POST | `/api/v1/auth/reinitialiser-mot-de-passe` | Applique le nouveau mot de passe à partir du code | publique |

### Choix de conception

- **Anti-énumération des comptes** : `POST /auth/mot-de-passe-oublie`
  renvoie systématiquement le même message générique ("si un compte existe
  avec cet identifiant, un code vient de lui être envoyé"), que l'identifiant
  corresponde à un compte inexistant, désactivé, Google, ou à un compte
  local valide. Seul le journal d'audit distingue ces cas côté back-office
  (`demande_reinitialisation_mot_de_passe_refusee` vs
  `demande_reinitialisation_mot_de_passe`).
- **Code jamais stocké en clair** : réutilisation de `hash_password`/
  `verify_password` (bcrypt), comme pour les mots de passe, conformément au
  commentaire du schéma SQL d'origine sur `code_verification`.
- **Un seul code actif à la fois** : générer un nouveau code invalide
  automatiquement tout code non utilisé précédemment émis pour le même
  utilisateur et le même type — évite qu'un ancien code oublié dans une
  boîte mail reste exploitable en parallèle d'un plus récent.
- **Expiration et tentatives configurables par l'administrateur** via
  `parametre_systeme` (`code_verification_expiration_minutes`,
  `code_verification_tentatives_max`, déjà présents dans les données de
  référence du schéma d'origine) — cohérent avec la "personnalisation des
  seuils et règles métier" du cahier des charges.
- **Aucune information exploitable en cas d'échec** : `POST
  /auth/reinitialiser-mot-de-passe` répond toujours la même erreur 400
  générique ("identifiant, code ou mot de passe invalide"), que
  l'identifiant soit inconnu, le compte soit Google, ou le code soit
  incorrect/expiré/épuisé/déjà utilisé. Un code atteint son nombre maximal
  de tentatives est immédiatement "brûlé" (marqué utilisé) même s'il aurait
  fini par être saisi correctement — protection contre la force brute plutôt
  qu'un simple ralentissement.
- **Réutilisation de `utilisateur_service.reinitialiser_mot_de_passe`** :
  l'application effective du nouveau mot de passe (avec son garde-fou
  "compte Google, pas de mot de passe SRB") est la même fonction que celle
  déjà utilisée par le module Utilisateurs administrateur — un seul endroit
  qui sait écrire un hash de mot de passe en base.

### Vérifié dans cet environnement

- Import complet de l'application avec les nouvelles routes.
- Flux de bout en bout testé (SQLite en mémoire + `TestClient`) : message
  générique identique pour un identifiant inconnu, un compte Google et un
  compte local valide (aucun code créé dans les deux premiers cas) ; mauvais
  code refusé ; bon code accepté et mot de passe effectivement changé
  (ancien mot de passe rejeté ensuite) ; réutilisation d'un code déjà
  consommé refusée ; épuisement des tentatives brûlant le code même
  correct ; tentative sur un compte Google toujours refusée.

## 13. Module Rapports (livré)

Implémente le Processus 4 du BPMN — "Génération de rapports" : production de
rapports journaliers, hebdomadaires, mensuels et annuels, exportés en PDF ou
Excel, à la demande ou de façon planifiée.

```
app/services/rapport_service.py   # bornes de période, calcul des indicateurs, rendu PDF/Excel
app/schemas/rapport.py            # RapportGenerateRequest, RapportPlanifieRequest, RapportOut, RapportContenu...
app/api/v1/rapports.py            # routeur /rapports
app/core/config.py                # + REPORTS_DIR (répertoire de stockage des fichiers générés)
requirements.txt                  # + reportlab (PDF), openpyxl (Excel)
```

### Endpoints

| Méthode | Route | Description | Protection |
|---------|-------|--------------|------------|
| GET | `/api/v1/rapports` | Historique paginé (`type_periode`, `format`, `id_service`, `date_debut`, `date_fin` sur la date de génération) | utilisateur actif |
| GET | `/api/v1/rapports/{id}` | Détail d'un rapport (période couverte déduite du fichier) | utilisateur actif |
| GET | `/api/v1/rapports/{id}/indicateurs` | Aperçu des indicateurs en JSON, sans télécharger le fichier | utilisateur actif |
| GET | `/api/v1/rapports/{id}/telecharger` | Télécharge le fichier PDF/Excel | utilisateur actif |
| POST | `/api/v1/rapports/generer` | Génération à la demande (période, format, service optionnel) | `generer_rapports` |
| POST | `/api/v1/rapports/generation-planifiee` | Job planifié : un rapport consolidé + un par service actif, pour chaque format demandé | `X-Job-Key` |

### Choix de conception

- **Indicateurs calculés par agrégation en lecture**, jamais dupliqués en
  base : le module s'appuie sur `pointage` et `anomalie`, déjà alimentées
  par les Processus 1 et 3, conformément au principe de conception BPMN
  ("Processus 1 et 3 alimentent les Processus 4 et 5" par dépendance de
  données plutôt que par événement).
- **Deux granularités de détail selon le périmètre demandé** : un rapport
  restreint à un service (`id_service` fourni) détaille chaque agent ; un
  rapport consolidé (`id_service` omis, "tous services") détaille chaque
  service plutôt que de lister tous les agents du SRB — cohérent avec le
  besoin "Vue consolidée multi-services" du cahier des charges, la
  comparaison agent par agent au niveau global restant du ressort du futur
  module BI (Processus 5).
- **Le schéma SQL d'origine ne conserve pas les bornes de la période
  couverte par un rapport** (seulement `date_generation`, qui peut différer
  de la période elle-même — ex. un rapport mensuel généré le 1er du mois
  suivant). Plutôt que de modifier le schéma fourni, la date de début de
  période est encodée dans le nom du fichier généré ; comme le calcul des
  bornes est déterministe et idempotent sur une date de début canonique
  (lundi de la semaine, 1er du mois, 1er janvier), la borne de fin s'en
  déduit exactement (`rapport_service.bornes_depuis_rapport`), sans perte
  d'information ni migration.
- **Taux de présence rapporté aux jours réellement travaillés** : le
  dénominateur (`jours_ouvres`) compte, sur la période, les seuls jours où
  le service a un horaire de référence défini (table `horaire_reference`
  du module Pointage) — un week-end ou jour férié sans horaire n'est jamais
  compté comme une absence. Un service sans aucun horaire défini est
  considéré travaillé tous les jours de la période (pas de référence
  disponible pour en exclure certains), plutôt que de bloquer le calcul.
- **Heures travaillées** : approximation volontairement simple (première
  entrée / dernière sortie valides du jour), suffisante pour un indicateur
  de synthèse ; la gestion de pauses multiples intra-journée n'est pas dans
  le périmètre du cahier des charges.
- **Fichiers stockés sur disque local** (`REPORTS_DIR`, `storage/rapports/`
  par défaut), chemin relatif conservé dans `rapport.chemin_fichier` plutôt
  qu'un chemin absolu — permet de déplacer le stockage (ex. bucket S3) sans
  migration de données. `GET .../telecharger` renvoie `410 Gone` si le
  fichier a été supprimé du disque indépendamment de son entrée en base.
- **Génération planifiée protégée par `X-Job-Key`**, même logique que
  `/anomalies/detecter-absences` : un ordonnanceur externe déclenche le
  Timer Start Event du Processus 4, pas une session utilisateur
  (`id_utilisateur` reste `NULL` sur les rapports ainsi générés).
- **Lecture ouverte, génération protégée** : comme pour les modules
  Agents/Services, tout utilisateur authentifié actif peut consulter et
  télécharger les rapports déjà générés (étape 11 du Processus 4,
  consultation non bloquante par le chef de service) ; seule la génération
  à la demande exige la permission `generer_rapports`.
- **Non couvert dans ce module** : l'interface d'échange avec les systèmes
  de paie/RH mentionnée au cahier des charges reste un connecteur à
  brancher lors de l'implémentation (le format et le protocole de ces
  systèmes externes n'étant pas figés au cahier des charges) ; `GET
  /rapports` expose déjà les métadonnées nécessaires pour qu'un système
  tiers vienne récupérer les rapports générés.

### Vérifié dans cet environnement

- Analyse du cahier des charges et de la conception BPMN (Processus 4) pour
  cadrer le périmètre du module.
- Calcul des bornes de période (`bornes_periode`) : jour, semaine
  (lundi-dimanche), mois (y compris passage d'année en décembre), année ;
  idempotence vérifiée (une date de début canonique redonne les mêmes
  bornes), ce qui fonde `bornes_depuis_rapport`.
- Rendu effectif d'un PDF (reportlab) et d'un classeur Excel (openpyxl) à
  partir d'indicateurs simulés, pour un rapport de service (détail par
  agent) et un rapport consolidé (détail par service) — fichiers générés
  inspectés (aperçu visuel du PDF, lecture programmatique des feuilles
  Excel).
- Un bug de rendu Excel (attribut invalide sur une cellule openpyxl) détecté
  et corrigé grâce à ce test.
- Import Python (`ast.parse`) de tous les fichiers nouveaux/modifiés : pas
  d'erreur de syntaxe.
- **Non exécuté dans cet environnement** (accès réseau désactivé, `pip
  install fastapi`/`sqlalchemy` impossible ici) : import complet de
  l'application FastAPI avec le nouveau routeur, et flux de bout en bout
  via `TestClient` (RBAC, pagination, téléchargement, génération planifiée)
  comme cela avait été fait pour les modules précédents. À rejouer avant
  mise en production.

## 14. Module BI — Tableau de bord décisionnel (livré)

Implémente le Processus 5 du BPMN — "Consultation du tableau de bord
décisionnel (BI)" : vue opérationnelle temps réel, tendances, classement des
agents, comparaison entre services, et estimation prédictive simple.

```
app/services/bi_service.py   # temps réel, tendances, classement, comparaison, prévision
app/schemas/bi.py            # TableauBordTempsReel, PointTendance, ClassementAgentOut, PrevisionOut...
app/api/v1/bi.py             # routeur /bi
```

### Endpoints

Tous protégés par la permission `consulter_bi` (RBAC — élément 2 du
Processus 5, "cohérence entre les cinq processus" via la même table
`role_permission` que les autres modules).

| Méthode | Route | Description |
|---------|-------|--------------|
| GET | `/api/v1/bi/temps-reel` | Présents/sortis/absents/retardataires du jour, global ou par service |
| GET | `/api/v1/bi/tendances` | Série d'indicateurs sur une plage de dates, à la granularité choisie (jour/semaine/mois/année) |
| GET | `/api/v1/bi/classement` | Agents les plus ponctuels ou les plus souvent en retard |
| GET | `/api/v1/bi/comparaison-services` | Indicateurs par service pour une période, classés par taux de présence |
| GET | `/api/v1/bi/prevision` | Projection du taux de présence par régression linéaire simple |

### Choix de conception

- **Aucune donnée dupliquée** : comme le module Rapports, tout est recalculé
  par agrégation en lecture sur `pointage` et `anomalie`. Les fonctions
  unitaires de `rapport_service` (`jours_ouvres_service`,
  `heures_travaillees_agent`, `compter_anomalies_par_type`,
  `indicateurs_agent`, `bornes_periode`, `calculer_indicateurs`) sont
  directement réutilisées plutôt que réécrites, cohérent avec la note de
  conception BPMN "Processus 1 et 3 alimentent les Processus 4 et 5" : les
  deux processus de restitution partagent la même source de données
  agrégées.
- **Temps réel basé sur le dernier pointage du jour**, pas sur la table
  `anomalie` pour la notion d'absence : les anomalies 'absence' ne sont
  consignées que par le job planifié du lendemain (module Anomalies), donc
  un agent qui n'a pas encore pointé ce matin n'a pas encore d'anomalie —
  le tableau de bord le déduit directement des pointages du jour
  (`nombre_absents` = agents attendus aujourd'hui sans aucun pointage
  valide), pour rester réellement "temps réel". Les retards, eux, sont déjà
  qualifiés de façon synchrone au pointage et donc lus directement dans
  `anomalie`.
- **Boucle d'exploration sans état côté serveur** (étapes 8→9→10 du BPMN,
  "Filtrer/explorer" → "Nouvelle exploration demandée ?" → "Recalculer
  selon le filtre") : chaque endpoint accepte ses propres filtres
  (`id_service`, plage de dates, granularité, critère de tri) et recalcule
  intégralement à chaque appel — aucune session d'exploration n'est
  conservée en mémoire, cohérent avec une API REST sans état ; c'est au
  frontend de rappeler l'endpoint concerné à chaque changement de filtre.
- **Journalisation limitée au point d'entrée** (`GET /bi/temps-reel`) plutôt
  qu'à chaque endpoint d'exploration : consigner l'étape 11
  ("Export/journaliser") sur chaque changement de filtre saturerait le
  journal d'audit pour un simple clic de filtrage ; seule la consultation
  initiale du tableau de bord est journalisée.
- **Classement et comparaison inter-services** : `GET /bi/classement` sans
  `id_service` couvre le besoin "identifier les agents les plus ponctuels
  ou les plus souvent en retard" à l'échelle de tout le SRB, pas seulement
  d'un service — chaque entrée porte son propre `id_service`/`nom_service`
  pour rester lisible dans une liste consolidée.
- **Prévision = régression linéaire simple**, explicitement qualifiée
  d'"estimation indicative" dans la réponse (`avertissement`) : conforme à
  la formulation du cahier des charges ("méthodes statistiques simples"),
  sans sur-promettre un modèle prédictif avancé hors périmètre. Un
  historique de moins de deux périodes exploitables renvoie une prévision
  vide plutôt qu'une erreur, avec le même champ `avertissement` expliquant
  pourquoi.
- **Garde-fou sur la taille de plage** (`GET /bi/tendances`) : au-delà de 60
  périodes calculées, l'endpoint renvoie une erreur 422 explicite plutôt que
  d'enchaîner un nombre incontrôlé de requêtes d'agrégation (ex. demander
  une granularité "jour" sur plusieurs années).

### Vérifié dans cet environnement

- Analyse du cahier des charges ("Tableau de bord opérationnel", "Système
  d'aide à la décision (BI)") et de la conception BPMN (Processus 5,
  éléments et variables) pour cadrer le périmètre.
- Découpage d'une plage de dates en périodes (`_buckets_dans_plage`) et
  sélection des N dernières périodes complètes (`_buckets_recents`) :
  bornes de chaque période vérifiées (mois avec truncature de fin de mois,
  semaines glissantes), ordre chronologique et garde-fou de taille (422
  au-delà de 60 périodes) contrôlés.
- Régression linéaire simple (`_regression_lineaire`) : coefficients
  retrouvés exactement sur une tendance linéaire construite (pente et
  ordonnée à l'origine), cas dégénérés vérifiés (tendance plate, historique
  insuffisant renvoyant `None`).
- Import Python (`ast.parse`) de tous les fichiers nouveaux/modifiés : pas
  d'erreur de syntaxe.
- **Non exécuté dans cet environnement** (accès réseau désactivé, comme
  pour le module Rapports) : les fonctions interrogeant la base
  (`tableau_de_bord_temps_reel`, `tendances`, `classement_agents`,
  `comparaison_services`, `prevision` dans leur intégralité avec de vraies
  requêtes SQLAlchemy) n'ont pas pu être exercées faute de pouvoir
  installer FastAPI/SQLAlchemy ici ; seule leur logique arithmétique pure a
  été validée en isolant les fonctions concernées. À rejouer via
  `TestClient` avec une base de données de test avant mise en production,
  comme pour les modules précédents.

## 15. Script de données de référence (livré)

Comble l'écart identifié plus haut : les `INSERT` de la section 5 du script
SQL d'origine (rôles, permissions, paramètres système) ne sont pas rejoués
par Alembic, qui ne gère que le schéma. `scripts/seed_reference_data.py`
les applique séparément, sur n'importe quelle installation.

```
scripts/seed_reference_data.py   # rôles, permissions, affectations, paramètres
scripts/__init__.py              # package, pour `python -m scripts.seed_reference_data`
```

```bash
python -m scripts.seed_reference_data
```

### Contenu

- **3 rôles** (`Administrateur`, `Chef de service`, `Secretaire`) et
  **6 permissions**, recopiés à l'identique de la section 5 du schéma SQL
  d'origine.
- **Affectations `role_permission`** : absentes du script SQL d'origine
  (qui peuple `role` et `permission` séparément, sans les relier). Le choix
  retenu ici découle directement des lanes de la conception BPMN et du
  chapitre IV du cahier des charges ("Acteurs du système") :
  - `Administrateur` — "Gestion globale du système" : toutes les
    permissions.
  - `Chef de service` — absent des lanes d'exécution opérationnelle
    (Processus 1, 3, 4, où seule la Secrétaire agit), présent uniquement
    dans les lanes de validation/consultation : `valider_roles` (Processus
    2, "rôles ... validés par le chef de service") et `consulter_bi`
    (Processus 5, seul acteur métier de sa lane).
  - `Secretaire` — porte toutes les tâches utilisateur des Processus 1 à 4
    (gestion agents/services, traitement des anomalies, génération des
    rapports) ; absente des lanes du Processus 5, donc pas de
    `consulter_bi`.
- **7 paramètres système** : les 5 de la section 5 d'origine
  (`seuil_retard_minutes`, `seuil_recidive`, `periode_glissante_jours`,
  `code_verification_expiration_minutes`, `code_verification_tentatives_max`)
  plus deux paramètres apparus au fil de l'implémentation et déjà consommés
  par le code avec une valeur de repli, mais jamais encore formellement
  documentés en base : `seuil_distance_faciale` (reconnaissance faciale,
  cf. `pointage_service._identite_verifiee`) et `telephone_hierarchie`
  (canal SMS optionnel, cf. `alerte_service._telephones_hierarchie`, laissé
  vide par défaut — aucun numéro configuré tant que l'administrateur ne l'a
  pas renseigné).

### Choix de conception

- **Idempotent** : peut être rejoué sans risque sur une base déjà peuplée
  (utile en recette comme après un correctif ajoutant une permission).
  - Rôles/permissions manquants : créés. Permission déjà présente : sa
    description est resynchronisée (texte d'affichage uniquement).
  - Associations `role_permission` : uniquement complétées, jamais
    retirées — une permission ajoutée manuellement par un administrateur
    depuis l'installation initiale n'est pas effacée par un rejeu du
    script.
  - Paramètres système déjà présents : leur `valeur` n'est **jamais**
    écrasée, seule la description peut être resynchronisée — un
    administrateur a pu ajuster un seuil depuis l'installation initiale
    (cf. besoin "Personnalisation des seuils et règles métier par
    l'administrateur"), le seed ne doit pas revenir dessus.
- **Script autonome plutôt que migration Alembic de données** : une
  migration de schéma n'a pas vocation à embarquer des données métier
  figées (surtout des permissions dont l'affectation relève d'un choix
  fonctionnel documenté, pas d'une évolution de structure) ; un script
  explicite, relançable à la demande et non lié au cycle
  upgrade/downgrade, est plus approprié ici — cohérent avec le choix déjà
  fait pour `detecter_absences` et `generation-planifiee` (job externe
  plutôt que logique embarquée dans le cycle de vie de l'application).
- **Noms de rôles figés à l'identique du schéma SQL d'origine** (notamment
  `Secretaire` sans accent) : `alerte_service.NOM_ROLE_CHEF_SERVICE` et
  l'ensemble du RBAC comparent ces chaînes telles quelles ; les modifier
  casserait silencieusement le routage des alertes et les contrôles de
  permission.

### Vérifié dans cet environnement

- Recensement exhaustif de tous les paramètres système effectivement lus
  par le code (`grep` sur `parametre_service.get_int/get_float/get_valeur`
  dans tout `app/`), pour s'assurer qu'aucun n'est resté non documenté.
- Logique d'idempotence testée avec une session simulée (rejeu à
  l'identique sans doublons de rôles/permissions/paramètres ; une valeur de
  paramètre modifiée "manuellement" entre deux exécutions n'est pas
  écrasée ; une permission ajoutée "manuellement" à un rôle entre deux
  exécutions est conservée).
- Import Python (`ast.parse`) du script : pas d'erreur de syntaxe.
- **Non exécuté dans cet environnement** (accès réseau désactivé, comme
  pour les modules Rapports et BI) : exécution réelle contre une base
  PostgreSQL via `alembic upgrade head` puis `python -m
  scripts.seed_reference_data`, suivie d'une vérification qu'un compte
  `utilisateur` peut effectivement être créé avec chacun des trois rôles.
  À rejouer avant mise en production.

---

Tous les modules du cahier des charges sont désormais livrés (Processus 1 à
5, authentification, RBAC, mot de passe oublié, données de référence). Les
points signalés comme "non exécuté dans cet environnement" dans chaque
section — flux `TestClient` de bout en bout contre une vraie base
PostgreSQL — restent à rejouer avant mise en production, faute d'accès
réseau pour installer FastAPI/SQLAlchemy dans cet environnement de travail.