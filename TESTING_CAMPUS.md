# Tester Nova Campus

Ce guide sépare les tests de plateforme sans GPU des tests d'inférence. Il peut
être suivi dans Windows PowerShell.

## 1. Préparer le serveur

1. Ouvrir Docker Desktop et attendre l'état **Engine running**.
2. Préparer la configuration :

```powershell
cd C:\Users\sash\Documents\nova-server
Copy-Item .env.example .env
notepad .env
docker info
```

Pour un test local simple, conserver :

```dotenv
NOVA_ADMIN_TOKEN=nova-admin-local
NOVA_ORGANIZATION_NAME=IPSA
NOVA_ORGANIZATION_SHORT_NAME=IPSA
```

Le jeton `nova-admin-local` est réservé au développement. Avant toute exposition
réseau, le remplacer par un secret long et aléatoire.

## 2. Démarrer selon le test

Plateforme sans GPU :

```powershell
docker compose up -d --build --no-deps api
docker compose logs -f api
```

Plateforme complète avec GPU NVIDIA :

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f api
```

Vérifier l'API :

```powershell
Invoke-RestMethod http://localhost:8080/api/health
Invoke-RestMethod http://localhost:8080/api/config | ConvertTo-Json -Depth 6
```

## 3. Accéder à l'administration

1. Ouvrir `http://localhost:8080/admin`.
2. Saisir la valeur `NOVA_ADMIN_TOKEN` du fichier `.env`.
3. Avec la configuration locale d'exemple : `nova-admin-local`.

Le panneau gère sièges, compteurs, rôles, cohortes, suspensions et appareils.
Il ne permet jamais de consulter le contenu des dictées.

## 4. Tester la connexion par email

1. Lancer Nova Campus et saisir `http://localhost:8080` comme serveur.
2. Utiliser une adresse appartenant à `NOVA_DOMAINS`.
3. Sans SMTP, lire le code avec `docker compose logs -f api`.
4. Saisir le code à six chiffres dans Nova.
5. Vérifier que Campus indique **connecté** et que l'utilisateur apparaît dans
   le panneau admin.

Pour un email réel, renseigner `NOVA_SMTP_HOST`, `NOVA_SMTP_PORT`,
`NOVA_SMTP_USER`, `NOVA_SMTP_PASS` et `NOVA_SMTP_FROM`, puis recréer l'API.

## 5. Configurer et tester Microsoft Entra ID

Nova utilise OAuth 2.0 Device Code, adapté à une application Windows publique.
Aucun secret client Microsoft ne doit être placé dans Nova Desktop.

Dans le centre d'administration Microsoft Entra :

1. Ouvrir **Identity > Applications > App registrations > New registration**.
2. Créer une application mono-tenant pour l'établissement.
3. Dans **Authentication**, activer **Allow public client flows**.
4. Dans **API permissions**, ajouter la permission déléguée Microsoft Graph
   `User.Read`. Les scopes `openid`, `profile` et `email` sont demandés par le
   serveur. Accorder le consentement administrateur si nécessaire.
5. Copier les identifiants dans `.env` :

```dotenv
NOVA_ENTRA_TENANT_ID=00000000-0000-0000-0000-000000000000
NOVA_ENTRA_CLIENT_ID=11111111-1111-1111-1111-111111111111
```

6. Relancer l'API et vérifier que `entra` est annoncé :

```powershell
docker compose up -d --build --no-deps api
(Invoke-RestMethod http://localhost:8080/api/config).auth_methods
```

7. Dans Nova, choisir **Continuer avec Microsoft**. Le navigateur s'ouvre ; se
   connecter avec le compte école et saisir le code affiché si Microsoft le
   demande.
8. Vérifier dans l'admin que l'adresse issue de Microsoft Graph `/me` a créé une
   session Nova.
9. Tester l'annulation, l'expiration, un domaine refusé et la déconnexion.

Cette intégration valide l'identité Microsoft et le profil Graph minimal. Elle
ne lit pas les emails Outlook, les calendriers ou les conversations Teams.

## 6. Matrice fonctionnelle

| Fonction | Où | Résultat attendu |
|---|---|---|
| Personnalisation | Navigation > Personnalisation | thème, langue, orbe et bulle changent immédiatement |
| Debug | Réglages > Avancé | l'activation révèle niveau de logs et journal en direct |
| Dictionnaire | Réglages > Écriture | ajout, suppression, import/export CSV et analyse |
| Snippets | Réglages > Écriture | l'expression enregistrée est développée |
| Règles | Réglages > Écriture | la règle est transmise à la reformulation |
| Styles | Navigation > Styles | sélection, duplication et création |
| Notes d'ingénierie | Bas de Styles | sections utiles, mesures préservées, aucun fait inventé |
| Nova Campus Welcome | Après la première connexion | AI Essentials, Smart Setup et sortie immédiate sont clairement proposés |
| Compétences IA | Navigation > AI Skills | six modules interactifs et progression locale |
| Mode commande | Texte sélectionné | transformation par le serveur |
| Fichier audio | Action fichier | résultat copié et ajouté à l'historique |
| Déconnexion | Réglages > Campus | token révoqué et retour à l'onboarding |
| Expiration | TTL réduit | session refusée puis nouvel onboarding |
| Mode évaluation | `NOVA_EDUCATION_MODE=assessment` | fonctions IA désactivées |

Transcription, Styles, commande et notes d'ingénierie exigent Whisper et le LLM.
Auth, admin, configuration, préférences et cours IA fonctionnent avec l'API seule.

Tester les trois chemins de première utilisation :

1. **AI Essentials** → un module → Continuer plus tard → Smart Setup → Nova.
2. **Configurer Nova maintenant** → configuration recommandée → première dictée.
3. **Passer pour le moment** → Home immédiatement.

Fermer l'application au milieu d'un module, la relancer et vérifier que le même
module reprend. Couper ensuite le serveur avant Smart Setup : Nova doit afficher
le traitement local et permettre de continuer.

## 7. Tests automatisés

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest -v test_main.py
```

## 8. Arrêter le serveur

```powershell
docker compose down
```

Le volume `nova-data` reste conservé. Ne pas utiliser `docker compose down -v`
sauf si la suppression complète des données de test est volontaire.
