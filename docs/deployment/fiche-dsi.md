# Fiche DSI : Nova sans aucune saisie pour l'élève

Une page. Deux actions pour la DSI : publier un enregistrement DNS, déployer
l'application avec le domaine de l'école. L'élève ouvre Nova, se connecte avec
son compte Microsoft, et c'est tout : il ne tape ni adresse de serveur ni code.

## 1. DNS (une fois par domaine d'e-mail)

Publier, dans la zone publique de l'école :

```
_nova.ecole.fr.   IN   TXT   "v=nova1; endpoint=https://nova.ecole.fr"
```

Conditions, toutes vérifiées par le poste avant de faire confiance :

- l'adresse est en **HTTPS** avec un certificat valide ;
- son nom d'hôte est **sous le domaine** (`nova.ecole.fr` pour `ecole.fr`) ;
- le serveur répond `serves: true` à
  `GET https://nova.ecole.fr/api/discovery/email-domain?domain=ecole.fr`.

Nova Admin, page **Déploiement**, affiche l'enregistrement tout rempli et un
bouton **Vérifier**. Contrôle en ligne de commande :

```
nslookup -type=TXT _nova.ecole.fr 1.1.1.1
```

Publier le serveur en HTTPS : voir
[`https-and-email-discovery.md`](./https-and-email-discovery.md).

## 2. Déploiement silencieux (MSI)

Le `.msi` est publié avec chaque version de Nova (installation par machine,
droits administrateur ou compte SYSTEM).

**Commande de base** (SCCM, script, ligne de commande) :

```
msiexec /i Nova.msi /qn /norestart
reg add "HKLM\SOFTWARE\Policies\Nova" /v EmailDomain /t REG_SZ /d ecole.fr /f
```

La valeur `EmailDomain` est lue au premier lancement : Nova interroge
`_nova.ecole.fr`, joint le serveur trouvé et saute l'écran de saisie. Le MSI ne
porte aucun paramètre d'adresse : le serveur vient toujours du DNS de l'école.

**Intune** : application Win32 (`.intunewin` du MSI).

| Champ | Valeur |
| --- | --- |
| Commande d'installation | `powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process msiexec -ArgumentList '/i Nova.msi /qn /norestart' -Wait; New-Item -Path HKLM:\SOFTWARE\Policies\Nova -Force \| Out-Null; Set-ItemProperty HKLM:\SOFTWARE\Policies\Nova -Name EmailDomain -Value ecole.fr"` |
| Commande de désinstallation | `msiexec /x Nova.msi /qn /norestart` |
| Contexte | Système |
| Règle de détection | valeur de registre `HKLM\SOFTWARE\Policies\Nova` / `EmailDomain` égale à `ecole.fr` |

**GPO** : Configuration ordinateur > Préférences > Registre > nouvel élément
de registre : ruche `HKEY_LOCAL_MACHINE`, clé `SOFTWARE\Policies\Nova`, valeur
`EmailDomain`, type `REG_SZ`, données `ecole.fr`. Le MSI se distribue par la
stratégie « Installation de logiciel » habituelle.

Un utilisateur standard ne peut pas modifier cette clé.

## 3. Vérifier un poste

1. `reg query HKLM\SOFTWARE\Policies\Nova /v EmailDomain` renvoie le domaine.
2. Lancer Nova : l'écran de connexion ne demande aucune adresse de serveur et
   propose directement la connexion de l'école.
3. Si l'écran de saisie réapparaît : le DNS n'est pas publié ou pas encore
   propagé, le certificat est invalide, ou le serveur ne déclare pas ce domaine
   (réponse `serves: false`). Corriger puis relancer Nova ; rien d'autre à
   faire sur le poste.

## 4. Ce que Nova ne fait pas

- Aucun secret dans le registre ni dans le DNS : l'enregistrement dit où aller,
  la connexion SSO prouve qui l'on est.
- Si plusieurs domaines d'e-mail existent (élèves, personnel), ne déployer que
  celui des postes élèves ; l'autre se découvre avec l'adresse tapée.
- Deployment géré avancé (profil `deployment_id`, ACL, canal de mise à jour) :
  voir [`windows-enterprise.md`](./windows-enterprise.md).
