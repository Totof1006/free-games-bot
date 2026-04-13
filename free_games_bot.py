# ─────────────────────────────────────────────
# FREE GAMES BOT – V2 ENTREPRISE (single file)
# ─────────────────────────────────────────────

import asyncio
import hashlib
import logging
import os
import re
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
# 🌐 ENRICHISSEMENTS STORES (Steam / GOG / Xbox / PSN)
# ─────────────────────────────────────────────

STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
GOG_PRODUCT_URL = "https://api.gog.com/products/{id}?expand=downloads,expanded_dlcs"
XBOX_PRODUCT_URL = (
    "https://storeedgefd.dsx.mp.microsoft.com/v9.0/products"
    "?productIds={id}&market=FR&locale=fr-FR"
)
PSN_PRODUCT_URL = (
    "https://store.playstation.com/store/api/chihiro/00_09_000/container/FR/fr/999/{id}"
)


def _extract_steam_appid(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/app/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/sub/(\d+)", url)
    if m:
        return m.group(1)
    return None


def _extract_gog_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/game/([A-Za-z0-9_\-]+)", url)
    if m:
        return m.group(1)
    return None


def _extract_xbox_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/([A-Z0-9]{12})", url)
    if m:
        return m.group(1)
    return None


def _extract_psn_id(url: str) -> str | None:
    if not url:
        return None
    m = re.search(r"/product/([A-Z0-9\-_.]+)", url)
    if m:
        return m.group(1)
    return None


async def enrich_with_steam(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    appid = _extract_steam_appid(url)
    if not appid:
        return game

    params = {"appids": appid, "l": "english"}
    try:
        async with session.get(STEAM_APPDETAILS_URL, params=params, timeout=10) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception as e:
        log.warning(f"[STEAM] Échec appdetails pour appid={appid}: {e}")
        return game

    app_data = data.get(appid, {})
    if not app_data.get("success"):
        return game

    details = app_data.get("data", {}) or {}

    short_desc = details.get("short_description")

    genres_raw = details.get("genres") or []
    genres = [g.get("description") for g in genres_raw if g.get("description")]

    categories_raw = details.get("categories") or []
    categories = [c.get("description") for c in categories_raw if c.get("description")]

    tags_raw = details.get("tags") or {}
    tags = list(tags_raw.keys())

    price_info = details.get("price_overview") or {}
    initial = price_info.get("initial")
    final = price_info.get("final")
    discount = price_info.get("discount_percent")

    game["steam"] = {
        "appid": appid,
        "short_description": short_desc,
        "genres": genres,
        "categories": categories,
        "tags": tags,
        "price_initial": initial,
        "price_final": final,
        "discount_percent": discount,
        "currency": price_info.get("currency"),
    }
    return game


async def enrich_with_gog(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    gog_id = _extract_gog_id(url)
    if not gog_id:
        return game

    api_url = GOG_PRODUCT_URL.format(id=gog_id)
    try:
        async with session.get(api_url, timeout=10) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception as e:
        log.warning(f"[GOG] Échec enrichissement pour id={gog_id}: {e}")
        return game

    short_desc = data.get("shortDescription") or data.get("description")

    genres_raw = data.get("genres") or []
    genres = [g.get("name") for g in genres_raw if g.get("name")]

    tags_raw = data.get("tags") or []
    tags = [t.get("name") for t in tags_raw if t.get("name")]

    price_info = data.get("price") or {}
    final = price_info.get("finalAmount")
    base = price_info.get("baseAmount")
    discount = price_info.get("discountPercentage")

    images = data.get("images") or {}
    image = images.get("logo") or images.get("background") or game.get("image")

    game["gog"] = {
        "id": gog_id,
        "short_description": short_desc,
        "genres": genres,
        "tags": tags,
        "price_initial": base,
        "price_final": final,
        "discount_percent": discount,
        "image": image,
    }
    return game


async def enrich_with_xbox(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    xbox_id = _extract_xbox_id(url)
    if not xbox_id:
        return game

    api_url = XBOX_PRODUCT_URL.format(id=xbox_id)
    try:
        async with session.get(api_url, timeout=10) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception as e:
        log.warning(f"[XBOX] Échec enrichissement pour id={xbox_id}: {e}")
        return game

    items = data.get("Products") or []
    if not items:
        return game

    info = items[0]

    desc = info.get("LocalizedProperties", [{}])[0].get("ShortDescription")

    images = info.get("Images") or []
    image = None
    for img in images:
        if img.get("ImagePurpose") == "Poster":
            image = img.get("Uri")
            break
    if not image and images:
        image = images[0].get("Uri")

    price_info = (
        info.get("DisplaySkuAvailabilities", [{}])[0]
        .get("Sku", {})
        .get("LocalizedProperties", [{}])[0]
        .get("ListPrice", {})
    )

    base_price = price_info.get("BasePrice")
    final_price = price_info.get("Price")
    discount = price_info.get("DiscountPercentage")

    categories = info.get("Properties", {}).get("Categories", [])

    game["xbox"] = {
        "id": xbox_id,
        "short_description": desc,
        "categories": categories,
        "image": image,
        "price_initial": base_price,
        "price_final": final_price,
        "discount_percent": discount,
    }
    return game


async def enrich_with_psn(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    psn_id = _extract_psn_id(url)
    if not psn_id:
        return game

    api_url = PSN_PRODUCT_URL.format(id=psn_id)
    try:
        async with session.get(api_url, timeout=10) as r:
            r.raise_for_status()
            data = await r.json()
    except Exception as e:
        log.warning(f"[PSN] Échec enrichissement pour id={psn_id}: {e}")
        return game

    included = data.get("included") or []
    attributes = None
    for item in included:
        if item.get("type") == "product":
            attributes = item.get("attributes")
            break
    if not attributes:
        return game

    desc = attributes.get("long-description") or attributes.get("description")

    media = attributes.get("media") or {}
    image = None
    if "images" in media:
        for img in media["images"]:
            if img.get("type") == "thumbnail":
                image = img.get("url")
                break
        if not image and media["images"]:
            image = media["images"][0].get("url")

    price_info = (
        attributes.get("skus", [{}])[0]
        .get("prices", {})
        .get("non-plus-user", {})
    )

    base_price = price_info.get("base-price")
    final_price = price_info.get("actual-price")
    discount = price_info.get("discount-percentage")

    categories = attributes.get("genres") or []

    game["psn"] = {
        "id": psn_id,
        "short_description": desc,
        "categories": categories,
        "image": image,
        "price_initial": base_price,
        "price_final": final_price,
        "discount_percent": discount,
    }
    return game

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

        tasks = []
        for item in data:
            if item.get("status", "").lower() == "expired":
                continue
            platform = _normalize_platform_from_gamerpower(item.get("platforms", ""))
            game = {
                "title": item.get("title") or "Sans titre",
                "platform": platform,
                "url": item.get("open_giveaway_url") or item.get("gamerpower_url"),
                "image": item.get("image"),
            }

            if platform == "Steam":
                tasks.append(enrich_with_steam(game, session))
            elif platform == "GOG":
                tasks.append(enrich_with_gog(game, session))
            elif platform == "Xbox":
                tasks.append(enrich_with_xbox(game, session))
            elif platform == "PlayStation":
                tasks.append(enrich_with_psn(game, session))
            else:
                tasks.append(asyncio.sleep(0, result=game))

        if tasks:
            games = await asyncio.gather(*tasks)
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

    score = 1
    premium_platforms = {"Epic Games", "Steam", "GOG", "PlayStation", "Xbox", "Nintendo eShop"}
    if any(p in premium_platforms for p in platforms):
        score += 1
    if any(x in lower_title for x in ["edition", "bundle", "collection"]):
        score += 2
    if len(platforms) >= 3:
        score += 1

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
        for guild in self.guilds:
            log.info(f"🛡️ Guild: {guild.name} (ID: {guild.id})")
        log.info(f"📡 Salon de scan configuré : {CHANNEL_ID}")
        log.info(f"📨 Salon de logs : {os.getenv('LOG_CHANNEL_ID')}")

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
# 🔍 SCAN ENGINE + LOGS ULTRA‑PRO
# ─────────────────────────────────────────────

class LogLevel:
    INFO = "ℹ️ INFO"
    WARNING = "⚠️ WARNING"
    ERROR = "❌ ERROR"


async def send_log_embed(level: str, title: str, description: str = "", fields=None):
    """Système de logs ULTRA‑PRO : envoie un embed dans le salon de logs si défini."""
    log_channel_id = os.getenv("LOG_CHANNEL_ID")
    if not log_channel_id:
        return

    channel = bot.get_channel(int(log_channel_id))
    if not channel:
        return

    color = 0x3498DB
    if level == LogLevel.WARNING:
        color = 0xE67E22
    elif level == LogLevel.ERROR:
        color = 0xE74C3C

    embed = discord.Embed(
        title=f"{level} • {title}",
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    await channel.send(embed=embed)


# ─────────────────────────────────────────────
# 🔁 SCAN LOOP
# ─────────────────────────────────────────────

@tasks.loop(seconds=CHECK_INTERVAL)
async def scan_loop():
    try:
        await asyncio.wait_for(run_scan(), timeout=45)
    except asyncio.TimeoutError:
        log.error("⏳ Scan annulé : timeout global dépassé")
        await send_log_embed(LogLevel.ERROR, "Timeout global du scan")


async def run_scan():
    """Scan complet : fetch → agrégation → filtrage → envoi."""
    if bot.scan_lock.locked():
        log.info("Scan ignoré : déjà en cours.")
        return

    async with bot.scan_lock:
        log.info("🔎 Scan démarré…")
        await send_log_embed(LogLevel.INFO, "Scan automatique démarré")

        if not bot.session:
            log.error("Session HTTP non initialisée.")
            await send_log_embed(LogLevel.ERROR, "Session HTTP non initialisée")
            return

        try:
            raw_games = await fetch_games(bot.session)
            aggregated = aggregate_games_by_title(raw_games)

            channel = bot.get_channel(CHANNEL_ID)
            if not channel:
                log.error("Salon introuvable.")
                await send_log_embed(LogLevel.ERROR, "Salon introuvable")
                return

            for game in aggregated:
                gid = hashlib.sha256(
                    (game["title"] + ",".join(game["platforms"])).encode()
                ).hexdigest()

                if await bot.db.is_reported(gid):
                    continue

                embed = build_embed(game, CHANNEL_ID, bot.db)
                if not embed:
                    continue

                role_mention = (
                    f"<@&{ROLE_ID}>" if isinstance(ROLE_ID, int) else "@everyone"
                )

                await channel.send(role_mention, embed=embed)
                try:
                    await bot.db.mark_as_reported(gid)
                except Exception as e:
                    log.error(f"Erreur DB lors du marquage du jeu {gid}: {e}")
                    await send_log_embed(
                        LogLevel.ERROR,
                        "Erreur DB mark_as_reported",
                        str(e),
                    )

                await send_log_embed(
                    LogLevel.INFO,
                    "Nouveau jeu envoyé",
                    fields=[
                        ("Titre", game["title"], False),
                        ("Plateformes", ", ".join(game["platforms"]), False),
                    ],
                )

            log.info("Scan terminé.")
            await send_log_embed(LogLevel.INFO, "Scan terminé")

        except Exception as e:
            log.error(f"Erreur dans run_scan : {e}", exc_info=True)
            await send_log_embed(LogLevel.ERROR, "Erreur run_scan", str(e))


# ─────────────────────────────────────────────
# ❗ GESTION DES ERREURS SLASH COMMANDS
# ─────────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "❌ Vous n'avez pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"❌ Une erreur est survenue : {error}", ephemeral=True
    )

    await send_log_embed(
        LogLevel.ERROR,
        "Erreur commande slash",
        str(error),
        fields=[("Utilisateur", interaction.user.mention, True)],
    )

# ─────────────────────────────────────────────
# 🧩 COMMANDES SLASH
# ─────────────────────────────────────────────

@bot.tree.command(name="status", description="Affiche le statut du bot")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🤖 **Bot opérationnel !**\n"
        f"📡 Scan toutes les **{CHECK_INTERVAL} secondes**\n"
        f"📁 Jeux déjà envoyés : **{len(bot.db.sent_cache)}**",
        ephemeral=True,
    )


@bot.tree.command(name="force_scan", description="Force un scan immédiat (admin uniquement)")
@app_commands.checks.has_permissions(administrator=True)
async def force_scan_cmd(interaction: discord.Interaction):
    await interaction.response.send_message("🔎 Scan forcé lancé…", ephemeral=True)
    await run_scan()


@bot.tree.command(name="reload", description="Recharge la configuration du bot (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def reload_cmd(interaction: discord.Interaction):
    await bot.db.setup()
    await interaction.response.send_message("🔄 Configuration rechargée.", ephemeral=True)


@bot.tree.command(name="clear_reported", description="Réinitialise la liste des jeux envoyés (admin)")
@app_commands.checks.has_permissions(administrator=True)
async def clear_reported_cmd(interaction: discord.Interaction):
    bot.db.sent_cache.clear()
    if bot.db.db:
        await bot.db.db.execute("DELETE FROM sent_games")
        await bot.db.db.commit()
    await interaction.response.send_message("🗑️ Liste des jeux envoyés réinitialisée.", ephemeral=True)
    await send_log_embed(LogLevel.WARNING, "Liste des jeux envoyés réinitialisée")


@bot.tree.command(name="set_lang", description="Change la langue du bot pour ce salon")
@app_commands.describe(lang="Langue : fr ou en")
async def set_lang_cmd(interaction: discord.Interaction, lang: str):
    lang = lang.lower()
    if lang not in ("fr", "en"):
        await interaction.response.send_message("❌ Langue invalide. Choisissez `fr` ou `en`.", ephemeral=True)
        return

    await bot.db.set_lang(interaction.channel_id, lang)
    await interaction.response.send_message(f"🌍 Langue définie sur **{lang}**.", ephemeral=True)


# ─────────────────────────────────────────────
# 🚀 LANCEMENT DU BOT
# ─────────────────────────────────────────────

def main():
    bot.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
