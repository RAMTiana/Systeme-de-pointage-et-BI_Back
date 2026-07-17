# Déploiement production — Backend SRB Haute Matsiatra

Ce document liste ce qui est **activé automatiquement** par le code et ce qui
reste à faire côté infrastructure pour un déploiement production sûr.

## 1. Ce que le backend applique automatiquement

| Contrôle | Où | Comment le régler |
|---|---|---|
| Hachage **argon2id** (OWASP 2024) avec rehash transparent des hashs bcrypt legacy | `app/core/security.py` | — |
| **Rotation stricte** des refresh tokens (jti à usage unique) + détection de réutilisation → révocation de toute la famille | `app/services/auth_service.py` + `app/core/token_store.py` | — |
| **Blacklist** des access tokens à la déconnexion (`POST /auth/logout`) | `app/core/token_store.py` | — |
| **Verrouillage compte** après N tentatives échouées (Redis, fenêtre glissante) | `app/core/lockout.py` | `LOCKOUT_MAX_ATTEMPTS`, `LOCKOUT_WINDOW_SECONDS`, `LOCKOUT_DURATION_SECONDS` |
| **Rate limiting** IP sur `/auth/*` (slowapi + Redis) | `app/core/rate_limit.py` | `RATE_LIMIT_LOGIN`, `RATE_LIMIT_PASSWORD_RESET` |
| **Politique mot de passe** (12+ car., maj/min/chiffre/spécial, ≠ login/email) | `app/core/password_policy.py` | `PASSWORD_MIN_LENGTH` |
| **En-têtes de sécurité** HSTS / CSP / X-Frame-Options / Referrer-Policy / Permissions-Policy | `app/core/security_headers.py` | — |
| **CORS strict** (liste explicite, pas de wildcard avec credentials) | `app/main.py` | `CORS_ORIGINS` |
| JWT avec **iss + aud** vérifiés | `app/core/security.py` | `JWT_ISSUER`, `JWT_AUDIENCE` |
| **Refus de démarrage** si `APP_ENV=production` avec des valeurs par défaut faibles (`SECRET_KEY`, `DEVICE_API_KEY`, `JOB_API_KEY`, `POSTGRES_PASSWORD`, `DEBUG=true`, CORS HTTP…) | `app/core/config.py` | Corriger le `.env` |
| **Docs Swagger désactivées** en production | `app/main.py` | `APP_ENV=production` |
| Journal d'audit sur chaque événement d'auth (connexion, échec, refresh, réutilisation détectée, déconnexion) | `journal_audit` | — |

## 2. À faire côté exploitation avant mise en production

1. **Secrets** — regénérer et stocker hors du dépôt (Vault, sops, Docker
   secrets…) :
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
   python -c "import secrets; print(secrets.token_urlsafe(32))"   # DEVICE_API_KEY, JOB_API_KEY, REDIS_PASSWORD
   ```
2. **TLS** — placer le stack derrière un reverse proxy (Traefik / Nginx /
   Caddy) qui termine HTTPS. Le middleware HSTS ne s'active qu'en
   `APP_ENV=production`.
3. **CORS_ORIGINS** — n'y mettre QUE les origines HTTPS du frontend
   (ex: `https://srb.hautematsiatra.mg`).
4. **Base de données** — utilisateur PostgreSQL dédié, mot de passe fort,
   backups quotidiens chiffrés + `pg_dump` testé.
5. **Redis** — mot de passe (`REDIS_PASSWORD` déjà pris en compte dans
   `docker-compose.yml`), TLS si trafic hors hôte (`rediss://`).
6. **Keycloak** — bascule automatique en mode `start` (production).
   Configurer un realm dédié `srb`, un client `srb-backend`
   (`confidential`, flow standard désactivé, service accounts activés) et
   mapper les rôles Keycloak vers le RBAC applicatif (`gerer_agents`,
   `traiter_anomalies`, …). Ne PAS laisser `admin/admin`.
7. **Gunicorn** — 2 × nb_cœurs workers, timeout > 30s pour DeepFace :
   ```bash
   gunicorn app.main:app -k uvicorn.workers.UvicornWorker \
     --workers 4 --bind 0.0.0.0:8000 --forwarded-allow-ips="*" \
     --timeout 60 --access-logfile - --error-logfile -
   ```
8. **Sauvegardes** — `postgres_data`, `redis_data`, `storage/rapports`.
9. **Monitoring** — surveiller `GET /api/v1/health`, les événements
   `refresh_reutilisation_detectee` (indicateur d'incident), et les 429
   sur `/auth/login` (attaque brute-force en cours).
10. **Rotation des jetons** — en cas de suspicion de fuite du `SECRET_KEY`,
    il suffit de le régénérer : tous les JWT existants deviennent
    immédiatement invalides.

## 3. Endpoints d'authentification exposés

| Méthode | Chemin | Contrôles appliqués |
|---|---|---|
| `POST /api/v1/auth/login` | connexion locale (form OAuth2) | rate limit + verrouillage compte + argon2/bcrypt |
| `POST /api/v1/auth/google` | Google Sign-In (id_token) | rate limit + vérif OIDC |
| `POST /api/v1/auth/refresh` | rotation refresh token | rate limit + rotation stricte + détection réutilisation |
| `POST /api/v1/auth/logout` | déconnexion | authentifié + blacklist access + révocation refresh |
| `GET  /api/v1/auth/me` | profil courant | JWT valide + non révoqué |
| `POST /api/v1/auth/mot-de-passe-oublie` | demande de code | rate limit + anti-énumération |
| `POST /api/v1/auth/reinitialiser-mot-de-passe` | réinit via code email | rate limit + politique mot de passe |