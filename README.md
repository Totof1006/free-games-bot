# **🎮 Free Games Bot \- L'escouade DO**

Un bot Discord bilingue (Français/Anglais) conçu pour traquer les jeux gratuits sur plusieurs plateformes en temps réel. Optimisé pour un déploiement facile sur **Railway**.

## **✨ Fonctionnalités**

* **Tracking Multi-plateformes** : Surveille 9 boutiques différentes (Epic Games, Steam, GOG, Nintendo, etc.).  
* **Système Bilingue Dynamique** : Choisissez la langue par salon (\!lang fr, \!lang en, ou mode bilingue par défaut).  
* **Filtrage Intelligent** : Ignore automatiquement les démos, bêtas et essais temporaires pour ne garder que les cadeaux permanents.  
* **Évaluation par Score** : Attribue un score de ⭐ à ⭐⭐⭐⭐⭐ pour chaque offre selon sa valeur.  
* **Notifications Propres** : Gestion optimisée des mentions @everyone ou rôles spécifiques sans doublons.

---

## **🛠️ Commandes Disponibles**

### **Utilisateurs**

| Commande | Action |
| :---- | :---- |
| \!aide / \!help | Affiche le menu d'aide bilingue. |
| \!freegames | Affiche une liste des jeux gratuits actuellement disponibles. |
| \!plateformes | Liste toutes les boutiques surveillées. |

### **Administrateurs**

| Commande | Action |
| :---- | :---- |
| \!lang \[fr/en/both\] | Définit la langue d'affichage pour le salon actuel. |
| \!check | Force un scan immédiat des sources. |
| \!reset | Réinitialise l'historique pour republier les offres en cours. |

---

## **🛰️ Plateformes Surveillées**

Le bot surveille les stocks de :

* **Epic Games**  
* **Steam**  
* **GOG**  
* **Nintendo eShop**  
* **Prime Gaming**  
* **PlayStation & Xbox**  
* **Ubisoft & Indiegala**

---

## **🚀 Installation & Déploiement**

### **1\. Variables d'environnement**

Pour fonctionner sur Railway, le bot nécessite les variables suivantes :

* BOT\_TOKEN : Le jeton de votre bot Discord.  
* CHANNEL\_ID : L'ID du salon où les annonces seront postées par défaut.  
* ROLE\_ID : L'ID du rôle à mentionner (ou everyone).

### **2\. Déploiement Railway**

1. Liez votre compte GitHub à Railway.  
2. Créez un nouveau projet à partir de votre dépôt.  
3. Ajoutez les variables d'environnement ci-dessus.  
4. Railway déploiera automatiquement le bot via le service worker.

---

## **📝 Licence**

Projet réalisé pour la communauté **L'escouade DO**. Libre d'utilisation.

---

### **💡 Astuce**

Si vous redémarrez le bot, utilisez la commande \!lang dans vos salons spécifiques pour restaurer votre préférence linguistique, le bot étant réglé sur "Bilingue" par défaut.

