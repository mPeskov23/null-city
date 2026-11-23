# 2FA par e-mail et conteneur SMTP (`smtp`)

Ce document a deux parties, écrites pour pouvoir défendre chaque choix en soutenance :

* **Partie 1 (§1 à §9)** : le conteneur d'envoi d'e-mails (Mailpit) et sa configuration.
* **Partie 2 (§10 à §21)** : la double authentification (2FA) elle-même : activation,
  connexion, désactivation, renvoi, codes de secours, chaîne d'appels de bout en bout,
  limites, comportement quand le SMTP est en panne, tests manuels, limites assumées et
  questions probables.

Tout ce qui est écrit ici a été relu dans le code (`server/app.py`, `server/mailer.py`,
`server/schema.sql`, `src/hub.js`, `src/menu.js`, `hub.html`, `compose.yaml`,
`deploy/smtp-init.sh`, `deploy/entrypoint.sh`) ; en cas de désaccord, le code fait foi.

# Partie 1 — Le conteneur SMTP

## 1. Vue d'ensemble

Un seul conteneur, **Mailpit** (`axllent/mailpit:v1.34.0`, version épinglée),
fait office de serveur SMTP **et** de boîte de réception web. Il fonctionne en
deux modes avec la même image :

1. **Mode capture (par défaut)** : aucun mail ne sort. Les messages sont
   stockés en mémoire et consultables dans l'interface web. Reproductible hors
   ligne, c'est le mode d'évaluation.
2. **Mode relais (optionnel)** : Mailpit transmet les messages à un vrai
   fournisseur SMTP (ex. Gmail avec mot de passe d'application) via les
   variables `MP_SMTP_RELAY_*` du `.env`.

```
                      réseau compose "default" (jamais publié sur l'hôte pour le SMTP)
   ┌───────────────┐   STARTTLS (cert vérifié, SAN "smtp") + AUTH    ┌─────────────────────────┐
   │   nullcity    │ ───────────────────────────────────────────────▶│  smtp (Mailpit)         │
   │ (app, uid     │            smtp:1025                            │  SMTP :1025  (interne)  │
   │  10001)       │                                                 │  UI   :8025  (HTTPS)    │
   └──────┬────────┘                                                 └───┬──────────┬──────────┘
          │ /run/smtp (ro)                          /pki (ro)            │          │ MP_SMTP_RELAY_*
          │ ca.pem + user + password                key+cert+auth        │          ▼ (optionnel)
   ┌──────┴──────────┐                                   ┌───────────────┴──┐   Internet : smtp.gmail.com:587
   │ volume          │◀────── écrit par ────────┐        │ volume smtp-pki  │
   │ smtp-shared     │                          │        └──────────────────┘
   └─────────────────┘                  ┌───────┴─────────┐ (job one-shot, root,
                                        │   smtp-init     │  CAP_CHOWN uniquement)
   Hôte : 127.0.0.1:${MAIL_UI_PORT}  ──▶│ génère CA+cert+ │
   (UI HTTPS, loopback uniquement)      │ login aléatoire │
                                        └─────────────────┘
```

Trois services dans `compose.yaml` :

| Service | Rôle | Durée de vie |
|---|---|---|
| `smtp-init` | Génère la CA privée, le certificat serveur (SAN `smtp`) et le login SMTP aléatoire. | Job unique (`restart: "no"`), idempotent |
| `smtp` | Mailpit : SMTP (1025, interne) + interface web HTTPS (8025). | Permanent |
| `nullcity` | L'application. Lit la CA publique et le login en lecture seule. | Permanent |

## 2. Pourquoi ces choix

| Choix | Raison |
|---|---|
| Mailpit plutôt que Postfix/MailHog | Un seul service pour SMTP + UI + API + relais intégrés, maintenu. MailHog est abandonné. |
| Image `v1.34.0` épinglée | Immuabilité : pas de `latest` (règle de reproductibilité). Le tag a été vérifié par `docker pull`. |
| Mode capture par défaut | Aucun compte externe, aucun secret, fonctionne hors ligne : l'évaluateur voit le code OTP dans l'UI. |
| Port 1025 non publié | Seule l'application, sur le réseau compose, peut parler au SMTP. Impossible de l'atteindre depuis le LAN ou l'hôte (vérifié : `nc -z 127.0.0.1 1025` échoue). |
| UI sur `127.0.0.1` uniquement | Les OTP y sont lisibles en clair : on ne l'expose pas sur le LAN (contrairement à l'app, qui peut l'être pour le multijoueur). |
| UI en HTTPS | Même certificat que le SMTP (SAN `localhost`, `127.0.0.1`). Pas de code OTP en clair sur le réseau, même en loopback. |
| `MP_ALLOWED_HOSTS` | Refuse tout en-tête `Host` autre que `localhost`/`127.0.0.1` : protection contre le DNS rebinding (testé : `Host: evil.example` donne 403). |
| STARTTLS obligatoire + certificat vérifié | `MP_SMTP_REQUIRE_STARTTLS=true` : toute session sans TLS est refusée (`530 Must issue a STARTTLS command first`). L'application vérifie le certificat avec la CA du projet : un homme du milieu sur le réseau compose ne peut pas se faire passer pour `smtp`. |
| Authentification SMTP | Le mot de passe est généré aléatoirement au premier démarrage (`openssl rand -hex 24`) : rien n'est commité, rien n'est à saisir dans `.env`. AUTH n'est proposé qu'après STARTTLS (pas de `--smtp-auth-allow-insecure`). C'est de la défense en profondeur en plus de l'isolation réseau. |
| CA privée + certificat feuille | La clé privée du serveur n'est montée que dans `smtp`. L'application ne reçoit que `ca.pem` (public). La clé de la CA n'existe que dans un `tmpfs` du job d'init et disparaît à sa fin. |
| Deux volumes (`smtp-pki`, `smtp-shared`) | Séparation des audiences : `smtp-pki` (clé, cert, fichier d'auth) est vu par Mailpit seul ; `smtp-shared` (CA publique, login) est vu par l'application seule, en lecture seule. |
| Job `smtp-init` plutôt que script dans `smtp` | Deux raisons réelles : (1) **séparation des secrets** : le job écrit dans deux volumes aux destinataires différents (clé et fichier d'auth pour Mailpit seul ; CA publique et login pour l'application seule) et la clé de la CA n'existe que dans son `tmpfs` ; (2) **les secrets doivent exister avant le démarrage de Mailpit**, qui lit son certificat et son fichier d'auth au lancement (`depends_on: service_completed_successfully`). Le job réutilise l'image de l'application, qui contient `openssl` : aucune image supplémentaire à télécharger. On ne prétend rien sur le contenu de l'image Mailpit (elle n'est pas prouvée « vide » : `ls` s'y exécute) ; on ne dépend simplement pas d'elle pour générer les certificats. |
| Durcissement | `cap_drop: ALL`, `no-new-privileges`, `read_only: true`, utilisateur non-root `10002` pour Mailpit. `smtp-init` tourne en root mais avec la seule capacité `CHOWN` (nécessaire pour donner la clé à l'uid 10002 et le mot de passe à l'uid 10001), rootfs en lecture seule. |
| Boîte aux lettres en RAM | `/tmp` en `tmpfs` (128 Mo) : la base temporaire de Mailpit ne touche jamais le disque ; `MP_MAX_AGE=1d` et 500 messages max. Les OTP ne persistent pas. |
| `MP_DISABLE_VERSION_CHECK=true` | Mailpit ne contacte pas GitHub au démarrage : fonctionne hors ligne. |
| Réseau par défaut (non `internal`) | Le mode relais exige un accès Internet sortant, et un réseau `internal` interdit aussi la publication de l'UI sur l'hôte. L'isolation vient donc de l'absence de `ports:` pour 1025, pas du réseau. |
| `depends_on` | `smtp-init` : `service_completed_successfully` (obligatoire : sans CA l'application ne peut pas vérifier le serveur). `smtp` : `service_healthy` avec `required: false`, donc le jeu démarre même si le mail ne démarre pas (testé : avertissement, pas d'échec). Une panne ultérieure de `smtp` n'arrête jamais `nullcity` (testé). |
| `restart: unless-stopped` sur `smtp` | Redémarre seul après un crash ; le healthcheck (`mailpit readyz --https`) reflète l'état réel. |

## 3. Variables d'environnement

### Côté application (lues par le backend)

| Variable | Défaut (compose) | Rôle |
|---|---|---|
| `SMTP_HOST` | `smtp` | Nom DNS du service (résolu par le DNS interne de compose). Doit rester `smtp` : c'est le SAN du certificat. |
| `SMTP_PORT` | `1025` | Port SMTP interne. |
| `SMTP_USER` | vide | Vide = valeur générée par `smtp-init`, lue dans `/run/smtp/user` par `deploy/entrypoint.sh`. Une valeur dans `.env` prend le dessus (et est utilisée par `smtp-init` pour créer le compte). |
| `SMTP_PASSWORD` | vide | Idem (`/run/smtp/password`). Laisser vide = mot de passe aléatoire non commité. |
| `SMTP_FROM` | `Null City <no-reply@nullcity.local>` | Expéditeur. En mode Gmail, utiliser `MP_SMTP_RELAY_OVERRIDE_FROM` (voir §7). |
| `SMTP_TLS` | `starttls` | `starttls` \| `ssl` \| `none`. Mailpit n'accepte que `starttls` ici (plaintext refusé). |
| `SMTP_CA` | `/run/smtp/ca.pem` | CA publique servant à vérifier le certificat du serveur. |

### Côté Mailpit (`smtp`)

| Variable | Valeur | Rôle |
|---|---|---|
| `MAIL_UI_PORT` (`.env`) | `8025` | Port hôte de l'UI (lié à `127.0.0.1`). |
| `MP_SMTP_BIND_ADDR` | `0.0.0.0:1025` | Écoute SMTP dans le conteneur (non publié). |
| `MP_UI_BIND_ADDR` | `0.0.0.0:8025` | Écoute UI dans le conteneur ; la restriction à la loopback se fait par la publication du port. |
| `MP_SMTP_TLS_CERT` / `MP_SMTP_TLS_KEY` | `/pki/...` | Certificat/clé STARTTLS. |
| `MP_SMTP_REQUIRE_STARTTLS` | `true` | Refuse les sessions non chiffrées. |
| `MP_SMTP_AUTH_FILE` | `/pki/auth.txt` | Fichier `utilisateur:mot_de_passe` généré par `smtp-init`. |
| `MP_UI_TLS_CERT` / `MP_UI_TLS_KEY` | `/pki/...` | HTTPS de l'UI. |
| `MP_ALLOWED_HOSTS` | `localhost:PORT,127.0.0.1:PORT` | Anti DNS-rebinding. |
| `MP_MAX_MESSAGES`, `MP_MAX_AGE` | `500`, `1d` | Rétention limitée. |
| `MP_DISABLE_VERSION_CHECK` | `true` | Pas d'appel sortant au démarrage. |
| `MP_SMTP_DISABLE_RDNS` | `true` | Pas de résolution DNS inverse des clients (inutile, plus rapide). |

### Mode relais (toutes vides par défaut, noms vérifiés dans la doc Mailpit « SMTP relaying »)

| Variable | Rôle |
|---|---|
| `MP_SMTP_RELAY_HOST` | Serveur SMTP distant. **Seule variable requise** : si elle est vide, toutes les autres sont ignorées (mode capture, testé). |
| `MP_SMTP_RELAY_PORT` | Port (587 pour STARTTLS). |
| `MP_SMTP_RELAY_STARTTLS` / `MP_SMTP_RELAY_TLS` | `true` pour STARTTLS (587) ou TLS implicite (465). |
| `MP_SMTP_RELAY_AUTH` | `plain` \| `login` \| `cram-md5` \| `none`. |
| `MP_SMTP_RELAY_USERNAME` / `MP_SMTP_RELAY_PASSWORD` | Identifiants du fournisseur (ne jamais commiter `.env`, il est dans `.gitignore`/`.dockerignore`). |
| `MP_SMTP_RELAY_OVERRIDE_FROM` | Réécrit l'expéditeur (Gmail impose l'adresse du compte). |
| `MP_SMTP_RELAY_RETURN_PATH` | Adresse de retour (bounces). |
| `MP_SMTP_RELAY_ALL` | `true` = chaque message capturé est aussi envoyé au vrai destinataire. Sinon, envoi manuel avec le bouton « Release » de l'UI. |

## 4. Lancement

```sh
make up        # construit et démarre tout, affiche les adresses du jeu et de la boîte mail
make down      # arrête
make fclean    # DESTRUCTIF : supprime aussi tous les volumes
```

> **Attention, perte de données.** `docker compose down -v`, `make fclean` et
> `make re` (= `fclean` puis `up`) suppriment **tous** les volumes du projet, donc
> aussi `city-data` : **les comptes**, les sauvegardes de partie, l'historique, la base
> SQLite, ses snapshots automatiques **et `twofa.key`** (voir §12). Le nouveau
> certificat SMTP et le nouveau mot de passe ne sont qu'un effet secondaire. Pour ne
> régénérer que les secrets SMTP, ne supprimer que `smtp-pki` et `smtp-shared`
> (`docker compose down`, puis `docker volume ls --filter name=smtp` et
> `docker volume rm` sur ces deux noms), jamais `city-data`. `make down` et
> `docker compose down` (sans `-v`) conservent les volumes.

Un `.env` existant sans variable SMTP fonctionne tel quel : tous les défauts
sont dans `compose.yaml` (`${VAR:-défaut}`).

Boîte de réception : <https://localhost:8025> (ou `MAIL_UI_PORT`). Le
navigateur affichera un avertissement car la CA est privée : acceptez-le
(accès local uniquement) ou importez la CA :

```sh
docker compose cp nullcity:/run/smtp/ca.pem ./ca.pem   # CA publique
curl --cacert ca.pem https://localhost:8025/api/v1/info  # UI vérifiée, sans -k
```

(importer `ca.pem` dans le navigateur/trousseau pour supprimer l'avertissement ; ne pas la commiter inutilement.)

## 5. Vérifications

```sh
docker compose ps                         # smtp, nullcity : healthy ; smtp-init : Exited (0)
docker compose port smtp 8025             # 127.0.0.1:8025
nc -z 127.0.0.1 1025 || echo "1025 non exposé"   # doit échouer
curl -sk https://localhost:8025/api/v1/messages | head -c 300   # API Mailpit
docker compose exec nullcity ls -l /run/smtp     # ca.pem, user, password ; PAS de key.pem
```

Envoi d'un message de test depuis le conteneur de l'application, avec STARTTLS
et vérification du certificat :

```sh
docker compose exec -T nullcity sh -c 'export SMTP_USER=$(cat /run/smtp/user) SMTP_PASSWORD=$(cat /run/smtp/password); python3 -' <<'EOF'
import smtplib, ssl, os
ctx = ssl.create_default_context(cafile="/run/smtp/ca.pem")   # vérification stricte
s = smtplib.SMTP("smtp", 1025, timeout=10)
s.ehlo(); s.starttls(context=ctx); s.ehlo()
s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
s.sendmail("no-reply@nullcity.local", ["joueur@example.com"],
           "From: Null City <no-reply@nullcity.local>\r\nTo: joueur@example.com\r\n"
           "Subject: Code 2FA\r\n\r\nCode: 123456\r\n")
s.quit(); print("envoye")
EOF
```

Le message doit apparaître dans l'UI. Les tests négatifs attendus : sans
`starttls()` l'envoi échoue en `530 Must issue a STARTTLS command first` ; avec la CA système, `CERTIFICATE_VERIFY_FAILED` ; avec un mauvais mot de
passe, `SMTPAuthenticationError`.

## 6. Particularités à connaître

* Le login est injecté dans le **processus de l'application** par
  `deploy/entrypoint.sh` (export avant `exec`). Un `docker compose exec`
  n'hérite pas de ces variables : lire `/run/smtp/user` et `/run/smtp/password`
  comme ci-dessus.
* `smtp-init` ne régénère rien tant que le certificat vaut plus de 30 jours.
  Après régénération (nouvelle CA, nouveau mot de passe), **redémarrer
  `nullcity`** avec `docker compose restart nullcity` : le login n'est lu par
  `deploy/entrypoint.sh` qu'au démarrage du conteneur, et le fichier `ca.pem` n'est
  relu que par un nouveau processus. Ne pas supposer que `docker compose up -d`
  recrée `nullcity` : sans changement de configuration ou d'image, Compose le laisse
  tel quel. Si l'authentification échoue encore, redémarrer aussi `smtp`
  (`docker compose restart smtp nullcity`).
* Mettre `SMTP_USER`/`SMTP_PASSWORD` dans `.env` impose ce compte à l'init
  (utile si un secret doit être connu à l'avance) ; à ne faire qu'avec un `.env` non versionné. Éviter `:` dans le nom d'utilisateur.

## 7. Mode relais avec Gmail (mot de passe d'application)

1. Activer la validation en deux étapes sur le compte Google.
2. Créer un « mot de passe d'application » (16 caractères) dans les paramètres de sécurité. Ne jamais utiliser le mot de passe du compte.
3. Dans `.env` (jamais commité) :

```sh
MP_SMTP_RELAY_HOST=smtp.gmail.com
MP_SMTP_RELAY_PORT=587
MP_SMTP_RELAY_STARTTLS=true
MP_SMTP_RELAY_AUTH=plain
MP_SMTP_RELAY_USERNAME=votre.adresse@gmail.com
MP_SMTP_RELAY_PASSWORD=xxxxxxxxxxxxxxxx
MP_SMTP_RELAY_OVERRIDE_FROM=votre.adresse@gmail.com
MP_SMTP_RELAY_ALL=true
```

4. `docker compose up -d smtp`. Les messages restent visibles dans l'UI **et**
   sont envoyés au vrai destinataire. Pour revenir en capture : vider ces
   variables et relancer `smtp`.

Remarques : l'accès sortant est fourni par le réseau par défaut ; la vérification
du certificat de `smtp.gmail.com` par Mailpit n'a pas été observée (aucun test
avec un vrai fournisseur) ; ne pas utiliser
`MP_SMTP_RELAY_ALLOW_INSECURE`. Le relais vers un vrai fournisseur n'a **pas**
pu être testé (aucun identifiant) ; la chaîne a été validée en relayant vers
un second Mailpit jetable (message reçu par la cible).

## 8. Dépannage

| Symptôme | Cause / solution |
|---|---|
| `smtp` redémarre en boucle, `read-only file system` | Le `tmpfs: /tmp` a été retiré : Mailpit y crée sa base temporaire. |
| `CERTIFICATE_VERIFY_FAILED` | `SMTP_CA` ne pointe pas vers `/run/smtp/ca.pem`, ou la CA a été régénérée sans `docker compose restart nullcity`. Vérifier aussi `SMTP_HOST=smtp` (SAN). |
| `530 Must issue a STARTTLS command first` | Le client n'appelle pas `starttls()` : positionner `SMTP_TLS=starttls`. |
| `535 Authentication failed` | Mot de passe désynchronisé entre le volume `smtp-shared` et le processus de l'application : `docker compose restart nullcity` (voir §6). **Ne pas** utiliser `docker compose down -v` ni `make fclean` : ils suppriment aussi `city-data`, donc les comptes. |
| `Connection refused` sur `smtp:1025` | `docker compose ps smtp` ; `docker compose logs smtp-init smtp`. Le jeu continue de fonctionner, seul l'envoi d'e-mail échoue. |
| `smtp-init` échoue (`Operation not permitted`) | Capacités retirées en trop : il lui faut `CHOWN`. |
| UI : 403 | En-tête `Host` non autorisé : utiliser `localhost` ou `127.0.0.1` avec le bon port (`MAIL_UI_PORT`). |
| Port déjà utilisé | Changer `MAIL_UI_PORT` dans `.env`. |
| Relais : rien n'arrive | `MP_SMTP_RELAY_HOST` vide (ignoré) ; `MP_SMTP_RELAY_ALL` absent (utiliser « Release ») ; mauvais mot de passe d'application ; consulter `docker compose logs smtp-init smtp`. |

## 9. Questions probables à l'évaluation

**Pourquoi un conteneur SMTP séparé ?** Séparation des responsabilités :
l'application ne contient pas de serveur de mail, on peut le remplacer (Gmail,
Postfix) sans toucher au code, et l'évaluation ne dépend d'aucun service externe.

**Où voir le code OTP ?** Dans l'UI Mailpit, <https://localhost:8025>, accessible
uniquement depuis la machine.

**Le SMTP est-il exposé ?** Non : aucun `ports:` pour 1025 ; seul le réseau
compose y accède.

**Pourquoi chiffrer et authentifier un SMTP interne ?** Défense en profondeur :
si un autre conteneur est compromis sur le même réseau, il ne peut ni lire
ni forger les mails sans la CA et le mot de passe. Les OTP ne circulent jamais en clair.

**Où sont les secrets ?** Aucun dans le dépôt. Mot de passe SMTP aléatoire dans un
volume (lisible par l'application seule) ; identifiants de relais dans `.env`
local ignoré par git et par `.dockerignore`.

**La clé privée est-elle partagée avec l'application ?** Non : l'application ne voit que
`ca.pem`. La clé de la CA est détruite à la fin de `smtp-init`.

**Que se passe-t-il si le SMTP tombe ?** Le jeu reste disponible
(`required: false`, et `depends_on` n'agit qu'au démarrage) ; seul l'envoi d'e-mails
échoue, il reprend après le redémarrage de `smtp` (testé). Ce que voit alors
l'utilisateur d'un compte protégé par 2FA est détaillé au §15.

**Pourquoi Mailpit tourne-t-il sans base persistante ?** Les OTP sont éphémères et
sensibles ; la mémoire (tmpfs) évite les traces sur disque.

**Pourquoi `smtp-init` est-il root ?** Pour attribuer des fichiers à deux
uids différents, avec la seule capacité `CHOWN`, rootfs en lecture seule, et il se termine
aussitôt. Mailpit et l'application ne sont pas root.

**Comment passer en envoi réel ?** Renseigner `MP_SMTP_RELAY_*` (§7), sans modifier le code.
Ce relais n'a pas été essayé avec un vrai fournisseur.

---

# Partie 2 — La double authentification (2FA)

## 10. Vue d'ensemble

* **Facultative (opt-in)** : un compte est créé sans 2FA ; l'utilisateur l'active
  depuis son profil. Rien n'oblige à l'activer.
* **Un second facteur = la boîte e-mail** : à chaque connexion d'un compte protégé,
  un code à 6 chiffres (10 minutes, 5 essais, usage unique) est envoyé à l'adresse du compte.
* **Activation prouvant l'accès à la boîte** : on n'active la 2FA qu'en saisissant un
  code reçu par e-mail. Il n'y a **pas** de vérification d'e-mail à l'inscription et
  **pas** de réinitialisation de mot de passe.
* **Aucune session avant le code** : `POST /api/login` d'un compte protégé ne pose
  pas de cookie ; il renvoie un `challenge`. La session n'existe qu'après
  `POST /api/login/2fa`.
* **8 codes de secours** à usage unique, affichés une seule fois à l'activation.
* **Activation et désactivation : profil du hub uniquement** (`hub.html`, section
  « Двухфакторная защита »). **L'étape de connexion** (saisie du code, renvoi, code de
  secours) existe **dans le hub et dans le menu du jeu** (`index.html`, boîte
  « Вход в аккаунт », `src/menu.js`).
* Les codes sont envoyés par `server/mailer.py` au conteneur `smtp` (Partie 1), en
  STARTTLS avec certificat vérifié.

## 11. Parcours, étape par étape

### 11.1 Activation

1. Profil du hub, bouton « Включить 2FA », saisie du mot de passe.
   `POST /api/2fa/setup {password, lang}` : le serveur limite à 5 essais par 5 minutes
   et par compte, vérifie le mot de passe (scrypt ; `403` s'il est faux), contrôle les
   budgets de mails (§13), crée un challenge de but `enable` et envoie le code.
   Réponse : `{challenge, expiresIn: 600, emailHint: "j***@domaine"}`.
2. Saisie du code (6 chiffres exigés côté client).
   `POST /api/2fa/enable {challenge, code}`. Si le code est bon, **dans la même
   transaction** : `users.twofa = 1`, génération de 8 codes de secours (anciennes
   lignes supprimées), suppression des challenges du compte, **révocation de toutes les
   autres sessions du compte** (la session courante est conservée).
3. L'écran affiche les 8 codes (`xxxxx-xxxxx`) avec trois boutons : « Скопировать коды »
   (presse-papiers), « Скачать .txt » (fichier `null-city-recovery-codes.txt`, un code par
   ligne) et « Я сохранил коды ». Le fichier est fabriqué **dans le navigateur**
   (`Blob`, `src/hub.js`, fonction `download`) : il n'est jamais envoyé au serveur. Les
   codes ne vivent qu'en mémoire de la page et ne sont **jamais ré-affichables** : le
   serveur n'en garde que l'empreinte HMAC.

### 11.2 Connexion

1. `POST /api/login {email, password, lang}`. Mot de passe faux : `401`, comme avant.
2. Compte sans 2FA : réponse inchangée (profil + cookie `__Host-nullcity`).
3. Compte avec 2FA : **aucun cookie**. Réponse `200` :
   `{twofa: true, challenge, expiresIn: 600, emailHint}` et le mail part. Si le mail ne
   part pas : `mailFailed`, éventuellement `mailLimited` et `retryAfter` (§15).
4. L'interface (hub ou menu) montre l'étape « Подтверждение входа » :
   `POST /api/login/2fa {challenge, code}`. Succès : profil complet + cookie.
5. Le bouton « Использовать резервный код » bascule le champ en mode code de secours
   (texte libre au lieu de 6 chiffres).

### 11.3 Renvoi

« Отправить код ещё раз » : `POST /api/login/2fa/resend {challenge, lang}`.
L'interface désactive le bouton 30 s (ou `retryAfter`). Côté serveur :

* uniquement pour un challenge de but `login` encore vivant (sinon `410`) ;
* respecte le délai de 30 s et le budget de 6 mails par heure (`429`) ;
* la ligne de budget est **réservée avant** l'envoi SMTP (deux renvois simultanés :
  un seul mail) ;
* l'ancien code reste valable tant que le **nouveau mail n'a pas été accepté** par le
  SMTP ; ensuite le nouveau remplace l'ancien, remet le compteur d'essais à 0 et
  repousse l'expiration de 10 minutes. Si l'envoi échoue : `503` + `retryAfter`, et
  l'ancien code continue de fonctionner.

### 11.4 Désactivation

Profil, bouton « Отключить 2FA » (en deux étapes, toutes deux avec le mot de passe).

1. `POST /api/2fa/disable {password, lang}` : mot de passe vérifié (5 essais / 5 min
   et par compte), challenge de but `disable`, mail envoyé. Ici le serveur
   **« ouvre »** : même si le mail ne peut pas partir, le challenge est renvoyé avec
   `mailFailed`, pour qu'un code de secours puisse conclure.
2. `POST /api/2fa/disable {password, challenge, code}` : le code est celui du mail
   **ou un code de secours** (10 essais mot de passe / 5 min et par compte). Effet :
   `twofa = 0`, codes de secours et challenges supprimés. Les sessions ne sont **pas**
   révoquées à la désactivation.

L'étape 2 est reconnue par la simple présence de la clé `challenge` dans le corps.

### 11.5 Codes de secours

| Propriété | Valeur |
|---|---|
| Nombre | 8, tous remplacés à chaque activation |
| Forme | 10 caractères tirés avec `secrets.choice` dans `abcdefghjkmnpqrstuvwxyz23456789` (31 symboles, sans `i l o 0 1`), affichés `xxxxx-xxxxx` ; environ 49 bits chacun |
| Saisie | espaces et tirets ignorés, casse ignorée (`recovery_normal`) |
| Stockage | `HMAC-SHA256(twofa.key, "rec" \0 id_utilisateur \0 code)` dans `twofa_recovery` ; jamais en clair |
| Usage | unique : `UPDATE … SET used = 1 WHERE … AND used = 0` et `rowcount == 1` (deux requêtes simultanées : une seule gagne) |
| Où | connexion et désactivation (jamais à l'activation : il n'en existe pas encore) |
| Budgets | un code de secours faux consomme un des 5 essais du challenge, **mais pas** le budget « 10 mauvais codes à 6 chiffres » du compte : il reste utilisable pendant un verrouillage |
| Régénération | pas de route dédiée : désactiver puis réactiver |

## 12. La clé HMAC (`twofa.key` / `TWOFA_SECRET`)

Un code à 6 chiffres n'a que 10^6 valeurs : une empreinte non secrète (SHA-256 seul)
serait cassée en une fraction de seconde si la base fuyait. Toutes les empreintes
sont donc des **HMAC-SHA256 avec une clé qui n'est jamais dans la base**
(`mac(kind, *parts)` dans `server/app.py` : `HMAC(clé, kind \0 part1 \0 part2 …)`).

| Empreinte | Entrée | Colonne |
|---|---|---|
| `mac('tok', token)` | jeton du challenge (`secrets.token_urlsafe(32)`, 256 bits, renvoyé au client) | `twofa_challenges.token` |
| `mac('code', empreinte_du_jeton, code)` | code à 6 chiffres envoyé par mail | `twofa_challenges.code` |
| `mac('rec', id_utilisateur, code_normalisé)` | code de secours | `twofa_recovery.code` |

Origine de la clé (fonction `twofa_key`) :

1. `TWOFA_SECRET`, s'il est défini : au moins 32 caractères, sinon `RuntimeError` au démarrage.
2. Sinon le fichier `twofa.key` **à côté de la base** (`DATABASE_PATH`) :
   `/app/data/twofa.key` en Docker (donc dans le volume `city-data`), `data/twofa.key`
   en natif. Créé au premier démarrage : 32 octets aléatoires en hexadécimal, mode
   `0600`, écrits dans un fichier temporaire puis liés atomiquement (un crash ne laisse
   jamais un fichier vide à l'emplacement final). Un fichier vide, trop court ou non
   hexadécimal fait **refuser le démarrage** (`init_db` appelle `twofa_key()` : l'erreur
   apparaît au lancement, pas à la première connexion).

Points d'attention, vérifiés dans le code :

* **En Docker, `TWOFA_SECRET` n'est pas transmis.** `compose.yaml` énumère les
  variables du service `nullcity` (pas d'`env_file`) et n'inclut pas `TWOFA_SECRET` ;
  `.env.example` ne le liste pas non plus. Dans le conteneur, seule la clé fichier
  est donc utilisée. `TWOFA_SECRET` ne joue que pour un lancement natif
  (`scripts/serve.py` le lit dans `.env`) ou si l'on ajoute soi-même la variable à
  `compose.yaml`.
* **Les snapshots automatiques ne contiennent pas la clé** : ils copient la base
  (`server/recovery.py`). Voir `docs/RECOVERY.md` pour la sauvegarde de la clé et les
  effets de sa perte.
* Si `twofa.key` est **absent** (et `TWOFA_SECRET` non défini) au démarrage, une
  **nouvelle clé est générée sans erreur** : les comptes existants gardent la 2FA, les
  nouveaux codes envoyés par mail fonctionnent, mais **tous les codes de secours déjà
  émis cessent de fonctionner**, sans message d'alerte.
* Le message d'erreur du code pour un fichier invalide dit « this invalidates pending
  codes » ; c'est incomplet : cela invalide aussi les codes de secours.

## 13. Limites et budgets

| Garde | Portée | Valeur | Réponse en cas de dépassement |
|---|---|---|---|
| Durée de vie d'un challenge | challenge | 600 s (`TWOFA_TTL`) | `410` |
| Essais par challenge | challenge | 5 (`TWOFA_ATTEMPTS`), codes à 6 chiffres **et** codes de secours | `403` aux essais 1 à 5 s'ils sont faux, puis `410` |
| Délai entre deux mails | compte | 30 s (`TWOFA_COOLDOWN`) | `429` + `retryAfter` (ou challenge « non envoyé », §15) |
| Mails par heure | compte | 6 par 3600 s (`TWOFA_MAILS`) ; une tentative d'envoi échouée compte aussi | `429` (ou `mailFailed` + `mailLimited`) |
| Mauvais codes **à 6 chiffres** | compte, tous challenges confondus | 10 par 900 s (`TWOFA_TRIES`) | `429` pour tout code à 6 chiffres, même le bon ; les codes de secours restent acceptés |
| Débit global de l'API | IP | 300 requêtes / 60 s | `429` |
| Inscription et connexion | IP | 10 / 300 s | `429` |
| Mails 2FA (connexion 2FA, renvoi, setup, disable étape 1) | IP | 10 / 300 s | `429` |
| `login/2fa` et `login/2fa/resend` | IP | 20 / 300 s | `429` |
| `/api/2fa/*` | compte | 20 / 300 s | `429` |
| Mot de passe de `setup` et `disable` étape 1 | compte | 5 / 300 s | `429` |
| Mot de passe de `disable` étape 2 | compte | 10 / 300 s | `429` |
| SMTP | envoi | 5 s par opération, 8 s au total (`mailer.TIMEOUT`, `DEADLINE`) | `MailError` (→ §15) |

Précisions :

* Les compteurs « budget » (mails et mauvais codes) sont dans la table `twofa_log`
  (persistants, résistent à un redémarrage). Le limiteur de débit par IP/compte
  (`rate()`) est **en mémoire** et repart de zéro au redémarrage.
* Une requête refusée par le verrouillage « 10 mauvais codes » ne consomme rien et ne
  prolonge pas le verrouillage (le contrôle est fait avant l'incrémentation).
* Un code **bon** vérifié lève le délai de 30 s (`twofa_relax`) mais **conserve** le
  compte horaire : se connecter juste après l'activation n'attend pas 30 s, mais les
  6 mails par heure restent comptés.
* Les purges (`twofa_purge`) suppriment les challenges expirés et les lignes de
  budget plus vieilles que 3600 s, au démarrage et à chaque émission de challenge.
* Les réponses `429` des limiteurs `rate()` n'ont pas de `retryAfter` ; seuls le délai
  de 30 s et l'échec d'envoi en ont un.

## 14. Routes

Détail des corps, réponses et codes : [API.md](API.md).

| Méthode | Route | Session | Rôle |
|---|---|:---:|---|
| POST | `/api/login` | non | si le compte a la 2FA : renvoie un `challenge` au lieu d'une session |
| POST | `/api/login/2fa` | non | `{challenge, code}` : code du mail ou de secours ; ouvre la session |
| POST | `/api/login/2fa/resend` | non | `{challenge, lang}` : renvoie un code |
| POST | `/api/2fa/setup` | oui | `{password, lang}` : démarre l'activation |
| POST | `/api/2fa/enable` | oui | `{challenge, code}` : active, renvoie les 8 codes |
| POST | `/api/2fa/disable` | oui | `{password, lang}` puis `{password, challenge, code}` |
| GET | `/api/me`, `/api/export` | oui | exposent le booléen `twofa`, jamais de secret |
| DELETE | `/api/me` | oui | supprime le compte (cascade sur les tables 2FA), voir §20 |

## 15. Quand le SMTP est en panne

La règle : **fail-closed**. Sans code valide, un compte protégé ne se connecte jamais ;
le seul contournement légitime est un code de secours. Le serveur ne garde aucune
transaction SQLite ouverte pendant l'envoi SMTP (le test
`fake_send` vérifie qu'une autre connexion peut écrire pendant l'envoi).

| Situation | Ce qui se passe |
|---|---|
| Connexion d'un compte protégé | `200`, `twofa: true`, **`mailFailed: true`**, `retryAfter` (30 s ou temps restant du délai) ; `mailLimited: true` si le budget horaire est épuisé (pas de `retryAfter`). Pas de cookie. L'interface écrit « Письмо с кодом не отправлено… » et propose le code de secours ou le renvoi après le délai |
| Challenge dont le mail n'est pas parti | Son empreinte de code est un **aléa** : aucun code à 6 chiffres ne peut correspondre. Seul un code de secours (ou un renvoi réussi) permet de continuer |
| Renvoi pendant la panne | `503` + `retryAfter: 30`, l'ancien code reste valable |
| Activation (`setup`) | `503` + `retryAfter: 30`. Aucun challenge renvoyé, la 2FA reste désactivée. La tentative compte dans les 6 mails par heure |
| Désactivation, étape 1 | `200` avec `mailFailed` : la désactivation reste possible avec un code de secours |
| Compte sans 2FA, jeu, chat, matchs | aucun effet |
| `SMTP_HOST` vide (typiquement lancement natif sans variables SMTP) | traité comme une panne (`MailError: SMTP is not configured`) : l'activation répond `503` |
| Adresse invalide ou multiple | `MailError`, aucun envoi (anti-injection d'en-têtes) |

Journal du serveur : `2FA mail failed: <type d'erreur>` (par exemple `Timeout`,
`SSLCertVerificationError`) ; ni le code, ni le mot de passe, ni l'adresse n'y figurent.

Redémarrage du SMTP : `docker compose start smtp` ; le prochain envoi réussit, sans
toucher à `nullcity`.

## 16. Chaîne d'appels de bout en bout

Légende : `hub.js` = `src/hub.js`, `menu.js` = `src/menu.js`, `app.py` = `server/app.py`.
Toutes les routes passent par `Handler.dispatch`, qui applique le limiteur global, la
vérification d'`Origin`, l'en-tête `X-NullCity: 1` et le JSON (`Handler.body`).

### 16.1 Activation

| # | Fichier | Fonction / élément | Rôle |
|---|---|---|---|
| 1 | `hub.html` | `#twofa-manage-form` | formulaire de mot de passe (profil) |
| 2 | `hub.js` | `$('#twofa-manage-form').onsubmit` | `api('2fa/setup', 'POST', {password, lang})` |
| 3 | `app.py` | `Handler.dispatch` → `Handler.user` → `Handler.api` → `Handler.twofa` (étape `setup`) | session obligatoire ; `409` si déjà actif |
| 4 | `app.py` | `rate()`, `hash_password()` | 5 essais / 5 min ; `403` si mot de passe faux |
| 5 | `app.py` | `twofa_issue(c, u, 'enable', lang)` | `twofa_mail_budget` (délai, budget horaire), `INSERT` dans `twofa_log` et `twofa_challenges` (empreintes `mac()`), `commit` |
| 6 | `app.py` → `mailer.py` | `twofa_deliver` → `mailer.send` → `mailer.build` | envoi STARTTLS vérifié, message texte + HTML localisé (ru/en/es) |
| 7 | `hub.js` | `#twofa-confirm-form` `.onsubmit` | `api('2fa/enable', 'POST', {challenge, code})` |
| 8 | `app.py` | `Handler.twofa` (étape `enable`) → `twofa_check('enable', …)` | verrou d'écriture, essais, budget de mauvais codes, marque le challenge `used` |
| 9 | `app.py` | suite de `enable` | `twofa = 1`, 8 codes de secours (`secrets.choice`), suppression des autres sessions ; réponse `recoveryCodes` |
| 10 | `hub.js` | `showRecovery`, `#twofa-copy`, `#twofa-download`, `#twofa-saved` | affichage, copie, fichier `.txt` local |

### 16.2 Connexion (hub)

| # | Fichier | Fonction / élément | Rôle |
|---|---|---|---|
| 1 | `hub.js` | `$('#auth-form').onsubmit` | `api('login', 'POST', {…, lang})` ; si la réponse contient `challenge` → `show2fa` |
| 2 | `app.py` | `Handler.auth` | scrypt (`hash_password` + `compare_digest`), puis `twofa_begin` (verrou) et relecture du compte |
| 3 | `app.py` | `twofa_issue(c, u, 'login', lang, fail_open=True)` | challenge toujours renvoyé, mail envoyé si les budgets le permettent |
| 4 | `hub.js` | `show2fa`, `startCooldown` | affiche `#twofa-step`, `emailHint`, minuterie |
| 5 | `hub.js` | `$('#twofa-form').onsubmit` | `api('login/2fa', 'POST', {challenge, code})` |
| 6 | `app.py` | `Handler.login_2fa` → `twofa_check('login', …)` | vérifie code ou code de secours, consomme le challenge |
| 7 | `app.py` | `Handler.start_session` | insère la session (hash SHA-256 du jeton) et pose le cookie `__Host-nullcity` |
| 8 | `hub.js` | `signedIn(profile)` | ouvre le club |
| — | `hub.js` | `#twofa-resend` `.onclick` → `Handler.login_2fa` → `twofa_resend` | renvoi (§11.3) |

### 16.3 Connexion (menu du jeu)

`menu.js` : `showAccount` → `form.onsubmit` → `request('login', …)` ; si `result.challenge`
→ `twoFactor(d, stage, form, result, status)` (boîte à l'intérieur du même dialogue,
challenge gardé dans la fermeture JavaScript et effacé à la fermeture) →
`request('login/2fa', …)` ou `request('login/2fa/resend', …)` → `finish(profile, d)`.
Les routes serveur sont les mêmes qu'en 16.2. Un `410` ramène au formulaire de connexion.
L'activation n'existe pas dans ce menu : le lien « Профиль, друзья и результаты » mène au hub.

### 16.4 Désactivation

`hub.js` `#twofa-manage-form.onsubmit` (mode `disable` si `me.twofa`) →
`api('2fa/disable', {password, lang})` → `Handler.twofa` (étape 1, `twofa_issue(…, 'disable',
fail_open=True)`) → `#twofa-confirm-form.onsubmit` →
`api('2fa/disable', {password, challenge, code})` → `twofa_check('disable', …)` →
`UPDATE users SET twofa = 0`, suppression de `twofa_recovery` et de `twofa_challenges`.

## 17. Tables

| Table | Contenu | Clés |
|---|---|---|
| `users.twofa` | 0 ou 1 (colonne ajoutée par migration additive dans `init_db`, défaut 0) | |
| `twofa_challenges` | `token` (HMAC), `user_id`, `purpose` (`login`/`enable`/`disable`), `code` (HMAC), `attempts`, `expires`, `used` | `ON DELETE CASCADE` |
| `twofa_recovery` | `user_id`, `code` (HMAC), `used` | clé primaire `(user_id, code)` ; cascade |
| `twofa_log` | `user_id`, `kind` (`mail`/`try`), `at` | index `(user_id, kind, at)` ; cascade |

Un challenge est lié à son compte **et à son but** : un challenge `enable` ne peut pas
ouvrir une session, un challenge `login` ne peut pas désactiver la 2FA, et un challenge
d'un autre compte est refusé (`410`).

## 18. Tests manuels (à faire devant l'évaluateur)

Prérequis : `make up`, puis ouvrir `https://localhost:8443/hub.html` (jeu) et
`https://localhost:8025` (boîte Mailpit, accessible uniquement depuis la machine hôte ;
un second appareil du réseau local n'y a pas accès).

1. **Inscription** : créer `alice@test.local` (mot de passe de 12 caractères ou plus).
2. **Activation** : profil, « Включить 2FA », saisir le mot de passe. Dans Mailpit,
   ouvrir le message « Confirm two-factor authentication » (ou sa version russe/espagnole
   selon la langue) et relever le code. Attendu : après le code, 8 codes `xxxxx-xxxxx`.
3. **Fichier de secours** : cliquer « Скачать .txt ». Attendu : téléchargement de
   `null-city-recovery-codes.txt` avec 8 lignes. Cliquer aussi « Скопировать коды »,
   puis « Я сохранил коды ». Les codes ne s'affichent plus jamais.
4. **Connexion** : se déconnecter, se reconnecter. Attendu : étape « Подтверждение входа »,
   `emailHint` du type `a***@test.local`, nouveau mail.
5. **Mauvais code** : saisir `000000`. Attendu : « Неверный код. » Après 5 essais faux le
   challenge est mort : le 6e envoi reçoit `410` et l'interface revient au formulaire
   de connexion.
6. **Renvoi** : bouton « Отправить код ещё раз » après la minuterie de 30 s.
   Attendu : nouveau mail ; l'ancien code ne fonctionne plus.
7. **Bon code** : saisir le dernier code. Attendu : accès au club.
8. **Code de secours** : se reconnecter, « Использовать резервный код », saisir un code
   (avec ou sans tiret, majuscules acceptées). Attendu : accès. Le même code, réutilisé :
   `403`.
9. **Menu du jeu** : sur `https://localhost:8443/`, « Войти / Регистрация » : l'étape du
   code doit apparaître dans la boîte de dialogue.
10. **SMTP en panne** : `docker compose stop smtp`, se connecter. Attendu : message
    « Письмо с кодом не отправлено… », pas de session ; un code de secours fonctionne.
    Puis `docker compose start smtp`.
11. **Désactivation** : profil, « Отключить 2FA », mot de passe, code du mail (ou de
    secours). Attendu : statut « выключена » et connexion sans code.
12. **Stockage** (lecture seule) :
    `docker compose exec nullcity python3 -c "import sqlite3;c=sqlite3.connect('/app/data/nullcity.sqlite3');print(c.execute('select token,code,purpose from twofa_challenges').fetchall())"`
    montre uniquement des empreintes hexadécimales de 64 caractères.

Ces étapes n'ont pas été rejouées intégralement à la main dans un navigateur au moment
de la rédaction (voir `docs/VERIFICATION.md`).

## 19. Tests automatiques

* `tests/test_twofa.py` : 49 tests (`npm run test:server`) : cycle complet, rejeu,
  concurrence (une seule session sur 6 requêtes simultanées), plafond d'essais,
  verrouillage par compte, codes de secours à usage unique, SMTP en panne, budgets,
  clé (création atomique, fichier invalide), migration d'une base existante, et le
  module `mailer` (en-têtes, un seul destinataire, délai total, TLS réel avec
  certificat vérifié et mauvais nom d'hôte refusé).
* `tests/twofa.test.mjs` : 10 tests (`npm test`) : identifiants du DOM, champs
  `one-time-code`, traductions, aucune persistance du challenge/mot de passe/codes,
  comportement de `src/hub.js` face aux réponses `403`, `410`, `mailFailed`, `retryAfter`.

## 20. Limites assumées

Ce qui suit est **volontairement dit tel quel** ; ce ne sont pas des oublis cachés.

1. **La boîte e-mail est le second facteur.** Le code prouve le contrôle de la boîte,
   pas la possession d'un appareil. Mot de passe **et** boîte compromis = compte
   compromis. En mode capture (par défaut), la « boîte » est l'interface Mailpit locale :
   adapté à la démonstration, pas à une vraie protection.
2. **Facultative** : un compte sans 2FA reste protégé par le seul mot de passe.
3. **Pas de vérification d'e-mail à l'inscription, pas de réinitialisation de mot de
   passe, pas de changement d'adresse** (`PUT /api/me` ne modifie que nom et biographie).
   Perdre à la fois la boîte et les codes de secours d'un compte protégé verrouille le
   compte ; il n'existe pas de procédure de récupération dans l'application.
4. **`DELETE /api/me` demande seulement le mot de passe** (avec une session valide) : pas
   de code 2FA. Les essais sont limités à 5 par 5 minutes et par compte. Un jeton de session volé plus le mot
   de passe suffit donc à supprimer un compte protégé.
5. **Réponse d'activation perdue.** `enable` active la 2FA et génère les codes dans une
   seule transaction ; si la réponse n'arrive pas (réseau, onglet fermé), la 2FA est
   active mais l'utilisateur n'a jamais vu ses codes, et ils ne sont pas ré-affichables.
   Solution : se connecter avec un code reçu par e-mail, désactiver, puis réactiver.
6. **Codes de secours** : 8 fixes, sans notification à l'usage, sans régénération sans
   désactivation.
7. **Verrouillage ciblable** : quelqu'un qui connaît le mot de passe peut obtenir un
   challenge et épuiser les 10 mauvais codes à 6 chiffres pendant 15 minutes ; l'accès
   par code de secours reste possible. Cela exige le mot de passe.
8. **Limiteur `rate()` en mémoire** : remis à zéro au redémarrage ; mono-processus.
9. **Relais réel jamais testé** avec un fournisseur (§7) ; seule la chaîne
   `nullcity` → Mailpit en STARTTLS vérifié et la relève vers un second Mailpit
   jetable ont été exercées.
10. **Clé** : sa perte invalide les codes de secours (§12, `docs/RECOVERY.md`).
11. **Perte de données** : `docker compose down -v`, `make fclean` et `make re`
    suppriment aussi les comptes (§4).

## 21. Questions probables (2FA)

**Pourquoi la 2FA est-elle facultative ?** Choix de périmètre : on ne casse pas les
comptes existants et le jeu reste utilisable sans boîte mail. Le coût est connu : un
compte sans 2FA n'est protégé que par son mot de passe.

**À quoi sert vraiment l'e-mail ?** À prouver le contrôle de la boîte au moment de
l'activation puis à chaque connexion. Ce n'est pas indépendant du mot de passe si
l'attaquant contrôle la boîte ; sans réinitialisation de mot de passe, la boîte seule
ne suffit cependant pas.

**Pourquoi un HMAC et pas un simple hachage ?** 10^6 codes possibles : un hachage
public se casse instantanément. La clé est hors de la base (fichier `twofa.key` ou
`TWOFA_SECRET`), donc une fuite de la base seule ne permet pas de retrouver un code.

**Comment éviter qu'un code serve deux fois ?** Le challenge passe à `used = 1` par un
`UPDATE … WHERE used = 0` dans la même transaction (`BEGIN IMMEDIATE`) que la
vérification ; sur 6 requêtes simultanées, une seule réussit (test dédié). Même principe
pour les codes de secours.

**Que se passe-t-il si on devine ?** 5 essais par challenge, 10 mauvais codes à
6 chiffres par compte et par 15 minutes (tous challenges confondus), limiteurs par IP.
Probabilité de réussite sur 5 essais : environ 5 sur 10^6 par challenge.

**Pourquoi ne pas tenir de transaction SQLite pendant l'envoi du mail ?** Un SMTP lent
(jusqu'à 8 s) bloquerait toutes les écritures. Le code est écrit, validé, puis le mail
est envoyé hors transaction ; un test vérifie qu'une autre connexion peut écrire
pendant l'envoi.

**Et si le SMTP est en panne ?** Fail-closed : jamais de connexion sans code valide ;
les codes de secours restent utilisables (§15).

**Que se passe-t-il si on perd `twofa.key` ?** Les codes de secours émis ne
fonctionnent plus ; les codes par e-mail continuent (§12, `docs/RECOVERY.md`).

**Pourquoi l'activation n'est-elle que dans le profil du hub ?** Pour garder un seul
parcours sensible (mot de passe, code, affichage des codes) ; le menu du jeu gère en
revanche la connexion pour que la 2FA ne bloque pas l'accès depuis la page d'accueil.

**Pourquoi un bouton « Скачать .txt » ?** Les codes ne sont affichés qu'une fois ; le
fichier est généré localement dans le navigateur (aucun envoi au serveur). Le fichier
est en clair sur le poste de l'utilisateur : à ranger comme un mot de passe.

**Quelles sont les faiblesses connues ?** §20, en particulier : boîte = second facteur,
2FA facultative, suppression de compte sans code 2FA, réponse d'activation perdue.
