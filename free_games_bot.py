"""
FREE GAMES BOT – V2 ENTREPRISE (single file, multi-plateformes fusionnées)
Stack: discord.py, aiosqlite, aiohttp
Features:
- Persistent async DB (aiosqlite) + in-memory cache
- Multi-fetchers: Epic (officiel) + GamerPower (multi-plateformes)
- Fusion par jeu (titre) + agrégation des plateformes
- Zéro doublon, même si un jeu est sur plusieurs plateformes
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Set

import aiohttp
import aiosqlite
import discord
from discord.ext import commands, tasks
from discord import app_commands

# ─────────────────────────────────────────────
# 📋 LOGGING & CONFIG
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("FreeGamesBot")

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ROLE_ID_RAW = os.getenv("ROLE_ID", "everyone")
CHECK_INTERVAL = max(60, int(os.getenv("CHECK_INTERVAL", "3600")))  # seconds

if not BOT_TOKEN or CHANNEL_ID == 0:
    raise RuntimeError("❌ Configuration ENV manquante (BOT_TOKEN ou CHANNEL_ID)")

ROLE_ID: int | str
ROLE_ID = int(ROLE_ID_RAW) if ROLE_ID_RAW.isdigit() else ROLE_ID_RAW.lower()

DB_PATH = os.getenv("DB_PATH", "data.db")

# ─────────────────────────────────────────────
# 🌍 LOCALISATION & CONSTANTES
# ─────────────────────────────────────────────
LOCALES: Dict[str, Dict[str, str]] = {
    "fr": {
        "NEW_GAME": "📢 **Nouveau jeu gratuit détecté !**",
        "PLATFORM": "🎮 Plateforme(s)",
        "TYPE": "🏷️ Type",
        "SCORE": "⭐ Score",
        "FOOTER": "Tracking Temps Réel • Escouade DO",
        "HELP_TITLE": "🎮 Aide - Free Games Bot",
        "LANG_CONFIRM_FR": "✅ La langue de ce salon est : **Français**.",
        "LANG_CONFIRM_EN": "✅ Language for this channel is: **Anglais**.",
        "LANG_CONFIRM_BOTH": "✅ Mode bilingue activé pour ce salon.",
    },
    "en": {
        "NEW_GAME": "📢 **New free game detected!**",
        "PLATFORM": "🎮 Platform(s)",
        "TYPE": "🏷️ Type",
        "SCORE": "⭐ Score",
        "FOOTER": "Real-time Tracking • DO Squad",
        "HELP_TITLE": "🎮 Help - Free Games Bot",
        "LANG_CONFIRM_FR": "✅ Language for this channel is: **French**.",
        "LANG_CONFIRM_EN": "✅ Language for this channel is: **English**.",
        "LANG_CONFIRM_BOTH": "✅ Bilingual mode enabled for this channel.",
    },
}

PLATFORM_COLORS: Dict[str, int] = {
    "Epic Games": 0x2ECC71,
    "Steam": 0x1B2838,
    "GOG": 0xA12B2E,
    "PlayStation": 0x003791,
    "Xbox": 0x107C10,
    "Prime Gaming": 0xFF9900,
    "EA": 0x00A4EA,
    "Nintendo eShop": 0xE60012,
    "Ubisoft": 0x0070D1,
    "Battle.net": 0x148EFF,
    "Other": 0x34495E,
}

# ─────────────────────────────────────────────
# 🗄️ ASYNC DATA MANAGER (PERSISTENT DB + CACHE)
# ─────────────────────────────────────────────
class DataManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.settings_cache: Dict[str, str] = {}
        self.sent_cache: Set[str] = set()
        self.db: Optional[aiosqlite.Connection] = None

    async def setup(self) -> None:
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS sent_games (id TEXT PRIMARY KEY)"
        )
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS settings (channel_id TEXT PRIMARY KEY, lang TEXT)"
        )
        await self.db.commit()

        async with self.db.execute("SELECT id FROM sent_games") as cursor:
            async for (gid,) in cursor:
                self.sent_cache.add(gid)

        async with self.db.execute("SELECT channel_id, lang FROM settings") as cursor:
            async for row in cursor:
                self.settings_cache[row[0]] = row[1]

        log.info(
            f"🗄️ DB prête • {len(self.sent_cache)} jeux en cache • "
            f"{len(self.settings_cache)} salons configurés."
        )

    async def close(self) -> None:
        if self.db:
            await self.db.close()

    async def is_reported(self, game_id: str) -> bool:
        return game_id in self.sent_cache

    async def mark_as_reported(self, game_id: str) -> None:
        if game_id in self.sent_cache:
            return
        self.sent_cache.add(game_id)
        await self.db.execute(
            "INSERT OR IGNORE INTO sent_games (id) VALUES (?)", (game_id,)
        )
        await self.db.commit()

    async def set_lang(self, channel_id: int, lang: str) -> None:
        self.settings_cache[str(channel_id)] = lang
        await self.db.execute(
            "INSERT OR REPLACE INTO settings (channel_id, lang) VALUES (?, ?)",
            (str(channel_id), lang),
        )
        await self.db.commit()

    def get_lang(self, channel_id: int) -> str:
        return self.settings_cache.get(str(channel_id), "both")

    def get_text(self, channel_id: int, key: str) -> str:
        lang = self.get_lang(channel_id)
        fr = LOCALES["fr"].get(key, key)
        en = LOCALES["en"].get(key, key)
        if lang == "fr":
            return fr
        if lang == "en":
            return en
        return f"{fr} / {en}"


# ─────────────────────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────────────────────
async def fetch_epic(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    games: List[Dict[str, Any]] = []
    try:
        async with session.get(url, timeout=15) as r:
            r.raise_for_status()
            data = await r.json()
        elements = (
            data.get("data", {})
            .get("Catalog", {})
            .get("searchStore", {})
            .get("elements", [])
        )
        for el in elements:
            promos = el.get("promotions") or {}
            offers_list = promos.get("promotionalOffers", [])
            if not offers_list:
                continue
            offers = offers_list[0].get("promotionalOffers", [])
            if not any(
                o.get("discountSetting", {}).get("discountPercentage") == 0
                for o in offers
            ):
                continue

            slug = el.get("productSlug") or el.get("urlSlug")
            games.append(
                {
                    "title": el.get("title", "Sans titre"),
                    "platform": "Epic Games",
                    "url": f"https://store.epicgames.com/fr/p/{slug}"
                    if slug
                    else "https://store.epicgames.com/fr/free-games",
                    "image": next(
                        (
                            i.get("url")
                            for i in el.get("keyImages", [])
                            if i.get("type") in ["OfferImageWide", "Thumbnail"]
                        ),
                        None,
                    ),
                }
            )
        log.info(f"Epic Games: {len(games)} jeu(x) gratuit(s) trouvé(s)")
    except Exception as e:
        log.error(f"Erreur Epic Fetch: {e}", exc_info=True)
    return games


def _normalize_platform_from_gamerpower(platforms_raw: str) -> str:
    p = (platforms_raw or "").lower()
    if "steam" in p:
        return "Steam"
    if "epic" in p:
        return "Epic Games"
    if "playstation" in p or "ps4" in p or "ps5" in p:
        return "PlayStation"
    if "xbox" in p:
        return "Xbox"
    if "origin" in p or "ea app" in p or "ea" in p:
        return "EA"
    if "switch" in p or "nintendo" in p:
        return "Nintendo eShop"
    if "gog" in p:
        return "GOG"
    if "ubisoft" in p or "uplay" in p:
        return "Ubisoft"
    if "battlenet" in p or "battle.net" in p:
        return "Battle.net"
    if "prime" in p or "amazon" in p:
        return "Prime Gaming"
    return "Other"


async def fetch_gamerpower(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    url = "https://www.gamerpower.com/api/giveaways?type=game"
    games: List[Dict[str, Any]] = []
    try:
        async with session.get(url, timeout=15) as r:
            r.raise_for_status()
            data = await r.json()
        for item in data:
            if item.get("status", "").lower() == "expired":
                continue
            platform = _normalize_platform_from_gamerpower(item.get("platforms", ""))
            games.append(
                {
                    "title": item.get("title") or "Sans titre",
                    "platform": platform,
                    "url": item.get("open_giveaway_url") or item.get("gamerpower_url"),
                    "image": item.get("image"),
                }
            )
        log.info(f"GamerPower: {len(games)} jeu(x) trouvé(s)")
    except Exception as e:
        log.error(f"Erreur GamerPower Fetch: {e}", exc_info=True)
    return games


async def fetch_games(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    try:
        epic, gp = await asyncio.gather(
            fetch_epic(session),
            fetch_gamerpower(session),
            return_exceptions=False,
        )
        all_games = epic + gp
        log.info(f"Total jeux récupérés (toutes sources): {len(all_games)}")
        return all_games
    except Exception as e:
        log.error(f"Erreur globale fetch_games: {e}", exc_info=True)
        return []


# ─────────────────────────────────────────────
# 🎁 FUSION MULTI-PLATEFORMES & EMBEDS
# ─────────────────────────────────────────────
def aggregate_games_by_title(raw_games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Regroupe les jeux par titre (case-insensitive) et fusionne les plateformes.
    Choisit une URL et une image "best effort" (Epic prioritaire).
    """
    by_title: Dict[str, Dict[str, Any]] = {}

    for g in raw_games:
        title = str(g.get("title", "")).strip()
        if not title:
            continue
        key = title.lower()

        platform = g.get("platform", "Other") or "Other"
        url = g.get("url", "")
        image = g.get("image")

        if key not in by_title:
            by_title[key] = {
                "title": title,
                "platforms": set([platform]),
                "url": url,
                "image": image,
                "sources": [g],
            }
        else:
            by_title[key]["platforms"].add(platform)
            by_title[key]["sources"].append(g)

            # Priorité URL / image : Epic > GamerPower > autres
            current_url = by_title[key]["url"]
            current_image = by_title[key]["image"]

            if platform == "Epic Games" and url:
                by_title[key]["url"] = url
            elif not current_url and url:
                by_title[key]["url"] = url

            if platform == "Epic Games" and image:
                by_title[key]["image"] = image
            elif not current_image and image:
                by_title[key]["image"] = image

    # Convertir les sets en listes
    aggregated: List[Dict[str, Any]] = []
    for key, data in by_title.items():
        aggregated.append(
            {
                "title": data["title"],
                "platforms": sorted(list(data["platforms"])),
                "url": data["url"],
                "image": data["image"],
            }
        )
    return aggregated


def build_embed(game: Dict[str, Any], channel_id: int, db: DataManager) -> Optional[discord.Embed]:
    title = str(game.get("title", "")).strip()
    platforms: List[str] = game.get("platforms", []) or []
    url = game.get("url", "")

    if not title or not url:
        return None

    lower_title = title.lower()
    if any(x in lower_title for x in ["demo", "beta", "playtest", "trial"]):
        return None

    # Score basé sur la "qualité" des plateformes
    score = 1
    premium_platforms = {"Epic Games", "Steam", "GOG", "PlayStation", "Xbox", "Nintendo eShop"}
    if any(p in premium_platforms for p in platforms):
        score += 1
    if any(x in lower_title for x in ["edition", "bundle", "collection"]):
        score += 2
    if len(platforms) >= 3:
        score += 1

    # Couleur : si une plateforme "connue" est présente, on prend la première
    color = PLATFORM_COLORS["Other"]
    for p in platforms:
        if p in PLATFORM_COLORS:
            color = PLATFORM_COLORS[p]
            break

    embed = discord.Embed(
        title=f"🎁 {title}",
        url=url,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    # Plateformes : phrase adaptée
    if len(platforms) == 1:
        platforms_text = platforms[0]
    else:
        platforms_text = ", ".join(platforms)

    embed.add_field(
        name=db.get_text(channel_id, "PLATFORM"),
        value=platforms_text,
        inline=False,
    )
    embed.add_field(
        name=db.get_text(channel_id, "TYPE"),
        value="🎁 Gratuit",
        inline=True,
    )
    embed.add_field(
        name=db.get_text(channel_id, "SCORE"),
        value="⭐" * min(score, 5),
        inline=True,
    )
    embed.set_footer(text=db.get_text(channel_id, "FOOTER"))

    image = game.get("image")
    if image:
        embed.set_image(url=image)

    return embed


# ─────────────────────────────────────────────
# 🤖 BOT CORE
# ─────────────────────────────────────────────
class UltimateBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="/", intents=intents, help_command=None)
        self.db = DataManager()
        self.session: Optional[aiohttp.ClientSession] = None
        self.scan_lock = asyncio.Lock()

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(headers={"User-Agent": "FreeGamesBot/2.0"})
        await self.db.setup()
        scan_loop.start()

    async def on_ready(self) -> None:
        log.info(f"🚀 Connecté : {self.user} (ID: {self.user.id})")
        try:
            synced = await self.tree.sync()
            log.info(f"🔗 {len(synced)} commandes slash synchronisées")
        except Exception as e:
            log.error(f"Erreur de synchronisation des commandes: {e}", exc_info=True)

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await self.db.close()
        await super().close()


bot = UltimateBot()

# ─────────────────────────────────────────────
# 🔎 SCAN ENGINE
# ─────────────────────────────────────────────
def _build_mention() -> str:
    if isinstance(ROLE_ID, str) and ROLE_ID == "everyone":
        return "@everyone "
    if isinstance(ROLE_ID, int):
        return f"<@&{ROLE_ID}> "
    return ""


async def run_scan() -> None:
    if bot.scan_lock.locked():
        log.info("Scan déjà en cours, skip.")
        return

    async with bot.scan_lock:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            log.error(f"Channel introuvable (ID={CHANNEL_ID})")
            return

        if not bot.session:
            log.error("Session HTTP non initialisée.")
            return

        try:
            raw_games = await fetch_games(bot.session)
        except Exception as e:
            log.error(f"Erreur lors du fetch des jeux: {e}", exc_info=True)
            return

        aggregated_games = aggregate_games_by_title(raw_games)
        log.info(f"Jeux agrégés (par titre): {len(aggregated_games)}")

        mention = _build_mention()
        new_count = 0

        for g in aggregated_games:
            title = g.get("title", "")
            if not title:
                continue

            # Hash basé uniquement sur le titre normalisé → un seul message par jeu
            key = title.lower().strip()
            game_hash = hashlib.sha256(key.encode()).hexdigest()

            if await bot.db.is_reported(game_hash):
                continue

            embed = build_embed(g, channel.id, bot.db)
            if embed is None:
                continue

            try:
                await channel.send(
                    content=f"{mention}{bot.db.get_text(channel.id, 'NEW_GAME')}",
                    embed=embed,
                )
                await bot.db.mark_as_reported(game_hash)
                new_count += 1
                log.info(
                    f"✅ Notifié : {title} (plateformes: {', '.join(g.get('platforms', []))})"
                )
                await asyncio.sleep(2)
            except discord.Forbidden:
                log.error(f"Permission manquante pour envoyer dans #{channel.name}")
                break
            except discord.HTTPException as e:
                log.error(f"Erreur HTTP Discord lors de l'envoi: {e}", exc_info=True)
            except Exception as e:
                log.error(f"Erreur inattendue lors de l'envoi: {e}", exc_info=True)

        log.info(f"Scan terminé. Nouveaux jeux envoyés: {new_count}")


@tasks.loop(seconds=CHECK_INTERVAL)
async def scan_loop() -> None:
    try:
        await run_scan()
    except Exception as e:
        log.error(f"Crash dans la loop de scan: {e}", exc_info=True)


@scan_loop.error
async def scan_loop_error(error: Exception) -> None:
    log.error(f"Erreur dans scan_loop: {error}", exc_info=True)


# ─────────────────────────────────────────────
# 🛡️ ERREURS SLASH
# ─────────────────────────────────────────────
@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Permissions insuffisantes.", ephemeral=True
        )
    else:
        log.error(f"Erreur commande slash {interaction.command}: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Une erreur interne est survenue.", ephemeral=True
            )


# ─────────────────────────────────────────────
# 🛰️ COMMANDES SLASH
# ─────────────────────────────────────────────
@bot.tree.command(name="lang", description="Change la langue du bot pour ce salon")
@app_commands.describe(choice="fr, en, both")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_lang(interaction: discord.Interaction, choice: str) -> None:
    choice = choice.lower().strip()
    if choice not in ("fr", "en", "both"):
        return await interaction.response.send_message(
            "❌ Valeurs acceptées : `fr`, `en`, `both`", ephemeral=True
        )

    await bot.db.set_lang(interaction.channel.id, choice)

    if choice == "both":
        confirm = LOCALES["fr"]["LANG_CONFIRM_BOTH"]
    elif choice == "fr":
        confirm = LOCALES["fr"]["LANG_CONFIRM_FR"]
    else:
        confirm = LOCALES["en"]["LANG_CONFIRM_EN"]

    await interaction.response.send_message(confirm)


@bot.tree.command(name="aide", description="Menu d'aide")
async def cmd_aide(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title=bot.db.get_text(interaction.channel.id, "HELP_TITLE"),
        color=0x3498DB,
    )
    embed.add_field(
        name="/aide",
        value="Menu d'aide / Help menu",
        inline=False,
    )
    embed.add_field(
        name="/lang [fr/en/both]",
        value="*(Admin)* Change la langue / Change language",
        inline=False,
    )
    embed.add_field(
        name="/check",
        value="*(Admin)* Scan manuel / Manual scan",
        inline=False,
    )
    embed.add_field(
        name="/plateformes",
        value="Liste des boutiques surveillées / Monitored stores",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="plateformes", description="Liste des boutiques surveillées")
async def cmd_plateformes(interaction: discord.Interaction) -> None:
    lang = bot.db.get_lang(interaction.channel.id)
    if lang == "fr":
        header = "🛰️ **Boutiques surveillées :**"
    elif lang == "en":
        header = "🛰️ **Monitored stores:**"
    else:
        header = "🛰️ **Boutiques surveillées / Monitored stores:**"
    liste = ", ".join(sorted(PLATFORM_COLORS.keys()))
    await interaction.response.send_message(f"{header} {liste}")


@bot.tree.command(name="check", description="Force un scan manuel")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_check(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("🔎 Scan manuel lancé...", ephemeral=True)
    await run_scan()


# ─────────────────────────────────────────────
# 🚀 LANCEMENT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
