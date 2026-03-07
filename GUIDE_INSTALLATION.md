# 🎮 Free Games Bot — Guide d'installation

## 📋 Prérequis
- Python 3.10+
- Un compte Discord Developer

---

## 🔧 Étape 1 — Créer le bot Discord

1. Va sur https://discord.com/developers/applications
2. Clique **"New Application"** → donne-lui un nom (ex: Free Games Bot)
3. Va dans **"Bot"** → clique **"Add Bot"**
4. Copie le **Token** (garde-le secret !)
5. Dans **"Privileged Gateway Intents"**, active :
   - ✅ Server Members Intent
   - ✅ Message Content Intent
6. Va dans **"OAuth2 > URL Generator"** :
   - Coche `bot` + `applications.commands`
   - Dans les permissions : `Send Messages`, `Embed Links`, `Read Message History`
   - Copie l'URL générée et ouvre-la pour inviter le bot sur ton serveur

---

## ⚙️ Étape 2 — Configurer le bot

Ouvre `free_games_bot.py` et modifie ces deux lignes :

```python
BOT_TOKEN = "TON_TOKEN_ICI"       # ← Colle ton token ici
CHANNEL_ID = 123456789012345678   # ← ID du salon Discord (clic droit > Copier l'ID)
```

> **Pour obtenir l'ID du salon** : Active le mode développeur dans Discord
> (Paramètres > Avancé > Mode développeur), puis fais clic droit sur le salon → "Copier l'identifiant"

---

## 📦 Étape 3 — Installer les dépendances

```bash
pip install -r requirements.txt
```

---

## 🚀 Étape 4 — Lancer le bot

```bash
python free_games_bot.py
```

---

## 🕹️ Commandes disponibles

| Commande | Description | Permissions |
|----------|-------------|-------------|
| `!freegames` | Affiche tous les jeux gratuits actuels | Tout le monde |
| `!check` | Force une vérification immédiate | Admin |
| `!reset` | Réinitialise l'historique des jeux envoyés | Admin |
| `!aide` | Affiche l'aide | Tout le monde |

---

## 🌐 Plateformes surveillées

| Plateforme | Source API |
|------------|-----------|
| 🟢 Epic Games | API officielle Epic |
| 🟩 Xbox Game Pass | GamerPower API |
| 🔵 PlayStation Plus | GamerPower API |
| 🖥️ Steam | Steam Store + GamerPower |
| 💙 Blizzard / Battle.net | GamerPower API |
| 🔴 EA Play / Origin | GamerPower API |

---

## ☁️ Hébergement 24/7 (optionnel)

Pour que le bot tourne en permanence :

- **Railway** (gratuit) → https://railway.app
- **Render** (gratuit) → https://render.com
- **VPS** (Contabo, OVH…)

---

## ❓ Problèmes courants

- **Bot ne répond pas** → Vérifie que "Message Content Intent" est activé dans le portail développeur
- **Aucun jeu affiché** → Utilise `!reset` puis `!check` pour forcer une mise à jour
- **Erreur de token** → Régénère le token sur le portail et mets-le à jour dans le fichier
