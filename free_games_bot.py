# ─────────────────────────────────────────────
# FREE GAMES BOT – V3 (single file, solide & stable)
# ─────────────────────────────────────────────

import asyncio
import hashlib
import logging
import os
import platform
import re
import time
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
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1480958930757877791"))
ROLE_ID_RAW = os.getenv("ROLE_ID", "everyone")
CHECK_INTERVAL = max(60, int(os.getenv("CHECK_INTERVAL", "3600")))  # seconds

if not BOT_TOKEN or CHANNEL_ID == 0:
    raise RuntimeError("❌ Configuration ENV manquante (BOT_TOKEN ou CHANNEL_ID)")

ROLE_ID: int | str
ROLE_ID = int(ROLE_ID_RAW) if ROLE_ID_RAW.isdigit() else ROLE_ID_RAW.lower()

DB_PATH = os.getenv("DB_PATH", "data.db")
BOT_START_TIME = time.time()

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
# 🧰 UTILITAIRES V3
# ─────────────────────────────────────────────
ENRICH_SEM = asyncio.Semaphore(3)


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(
        r"(edition|bundle|collection|game of the year|goty|standard edition|complete edition)",
        "",
        title
    )
    return re.sub(r"[^a-z0-9]", "", title)


async def fetch_json(session: aiohttp.ClientSession, url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "FreeGamesBot/2.0"}
            ) as r:
                r.raise_for_status()
                return await r.json()
        except Exception as e:
            log.warning(f"[HTTP] Tentative {attempt+1}/{retries} échouée pour {url}: {e}")
            if attempt == retries - 1:
                return None
            await asyncio.sleep(2 ** attempt)


async def safe_enrich(func, game, session):
    async with ENRICH_SEM:
        return await func(game, session)

# ─────────────────────────────────────────────
# 🗄️ DATA MANAGER
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
# 🌐 ENRICHISSEURS STORES
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
    m = re.search(r"/bundle/(\d+)", url)
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

    data = await fetch_json(session, STEAM_APPDETAILS_URL + f"?appids={appid}&l=english")
    if not data:
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
    data = await fetch_json(session, api_url)
    if not data:
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
    if image:
        game["image"] = image
    return game


async def enrich_with_xbox(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    xbox_id = _extract_xbox_id(url)
    if not xbox_id:
        return game

    api_url = XBOX_PRODUCT_URL.format(id=xbox_id)
    data = await fetch_json(session, api_url)
    if not data:
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
    if image:
        game["image"] = image
    return game


async def enrich_with_psn(game: dict, session: aiohttp.ClientSession) -> dict:
    url = game.get("url") or ""
    psn_id = _extract_psn_id(url)
    if not psn_id:
        return game

    api_url = PSN_PRODUCT_URL.format(id=psn_id)
    data = await fetch_json(session, api_url)
    if not data:
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
    if image:
        game["image"] = image
    return game

# ─────────────────────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────────────────────
async def fetch_epic(session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    games: List[Dict[str, Any]] = []
    try:
        data = await fetch_json(session, url)
        if not data:
            return games
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
        data = await fetch_json(session, url)
        if not data:
            return games

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
                tasks.append(safe_enrich(enrich_with_steam, game, session))
            elif platform == "GOG":
                tasks.append(safe_enrich(enrich_with_gog, game, session))
            elif platform == "Xbox":
                tasks.append(safe_enrich(enrich_with_xbox, game, session))
            elif platform == "PlayStation":
                tasks.append(safe_enrich(enrich_with_psn, game, session))
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
# 🎁 FUSION & EMBEDS
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
    for _, data in by_title.items():
        aggregated.append(
            {
                "title": data["title"],
                "platforms": sorted(list(data["platforms"])),
                "url": data["url"],
                "image": data["image"],
                # on garde la première source enrichie si dispo
                "steam": next((s.get("steam") for s in data["sources"] if s.get("steam")), None),
                "gog": next((s.get("gog") for s in data["sources"] if s.get("gog")), None),
                "xbox": next((s.get("xbox") for s in data["sources"] if s.get("xbox")), None),
                "psn": next((s.get("psn") for s in data["sources"] if s.get("psn")), None),
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

    steam = game.get("steam")
    if steam:
        if steam.get("price_initial"):
            price = steam["price_initial"] / 100
            embed.add_field(
                name="💸 Prix",
                value=f"{price:.2f}€ → GRATUIT",
                inline=True,
            )
            if steam.get("price_initial", 0) > 3000:
                score += 2
        if steam.get("genres"):
            embed.add_field(
                name="🎭 Genres",
                value=", ".join(steam["genres"][:3]),
                inline=False,
            )
        if steam.get("short_description") and not embed.description:
            embed.description = steam["short_description"][:200]

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
# 🟥 LOGS ULTRA‑PRO
# ─────────────────────────────────────────────
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))

from enum import Enum

class LogLevel(Enum):
    INFO = ("INFO", 0x3498DB, "ℹ️")
    SUCCESS = ("SUCCESS", 0x2ECC71, "✅")
    WARNING = ("WARNING", 0xE67E22, "⚠️")
    ERROR = ("ERROR", 0xE74C3C, "❌")
    CRITICAL = ("CRITICAL", 0x8E44AD, "🛑")

    @property
    def label(self): return self.value[0]
    @property
    def color(self): return self.value[1]
    @property
    def emoji(self): return self.value[2]


async def send_log_embed(
    level: LogLevel,
    title: str,
    description: str | None = None,
    fields: List[tuple[str, str, bool]] | None = None,
) -> None:
    if not LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        log.warning(f"[LOGS] Salon introuvable (ID={LOG_CHANNEL_ID})")
        return

    embed = discord.Embed(
        title=f"{level.emoji} {title}",
        description=description or "",
        color=level.color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Free Games Bot • Logs")

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        log.error(f"Erreur lors de l'envoi d'un log embed: {e}", exc_info=True)

# ─────────────────────────────────────────────
# 🔎 SCAN ENGINE V3
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
        await send_log_embed(
            LogLevel.WARNING,
            "[SCAN] Scan ignoré",
            "Un scan est déjà en cours."
        )
        return

    async with bot.scan_lock:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            log.error(f"Channel introuvable (ID={CHANNEL_ID})")
            await send_log_embed(
                LogLevel.ERROR,
                "[SCAN] Channel introuvable",
                f"Impossible d'envoyer dans le salon ID={CHANNEL_ID}"
            )
            return

        if not bot.session:
            log.error("Session HTTP non initialisée.")
            await send_log_embed(
                LogLevel.CRITICAL,
                "[SCAN] Session HTTP absente",
                "La session aiohttp n'est pas initialisée."
            )
            return

        await send_log_embed(
            LogLevel.INFO,
            "[SCAN] Scan lancé",
            fields=[("Intervalle", f"{CHECK_INTERVAL} sec", True)]
        )

        try:
            raw_games = await fetch_games(bot.session)
        except Exception as e:
            log.error(f"Erreur lors du fetch des jeux: {e}", exc_info=True)
            await send_log_embed(
                LogLevel.ERROR,
                "[FETCH] Erreur globale",
                str(e)
            )
            return

        aggregated_games = aggregate_games_by_title(raw_games)
        log.info(f"Jeux agrégés (par titre): {len(aggregated_games)}")

        await send_log_embed(
            LogLevel.INFO,
            "[SCAN] Agrégation terminée",
            fields=[("Jeux agrégés", str(len(aggregated_games)), True)]
        )

        mention = _build_mention()
        new_count = 0

        for g in aggregated_games:
            title = g.get("title", "")
            if not title:
                continue

            normalized = normalize_title(title)
            key = normalized + "," + ",".join(g.get("platforms", []))
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

                await send_log_embed(
                    LogLevel.SUCCESS,
                    "[GAME] Nouveau jeu envoyé",
                    fields=[
                        ("Titre", title, False),
                        ("Plateformes", ", ".join(g.get("platforms", [])), False),
                    ]
                )

                await asyncio.sleep(2)

            except discord.Forbidden:
                log.error(f"Permission manquante pour envoyer dans #{channel.name}")
                await send_log_embed(
                    LogLevel.ERROR,
                    "[DISCORD] Permission manquante",
                    f"Impossible d'envoyer dans {channel.mention}"
                )
                break

            except discord.HTTPException as e:
                log.error(f"Erreur HTTP Discord lors de l'envoi: {e}", exc_info=True)
                await send_log_embed(
                    LogLevel.ERROR,
                    "[DISCORD] Erreur HTTP",
                    str(e)
                )

            except Exception as e:
                log.error(f"Erreur inattendue lors de l'envoi: {e}", exc_info=True)
                await send_log_embed(
                    LogLevel.ERROR,
                    "[DISCORD] Erreur inattendue",
                    str(e)
                )

        log.info(f"Scan terminé. Nouveaux jeux envoyés: {new_count}")

        await send_log_embed(
            LogLevel.INFO,
            "[SCAN] Scan terminé",
            fields=[("Nouveaux jeux envoyés", str(new_count), True)]
        )


@tasks.loop(seconds=CHECK_INTERVAL)
async def scan_loop() -> None:
    try:
        await asyncio.wait_for(run_scan(), timeout=120)
    except asyncio.TimeoutError:
        log.error("⏳ Scan annulé : timeout global dépassé")
        await send_log_embed(
            LogLevel.ERROR,
            "[SCAN] Timeout global",
            "Le scan a dépassé le délai maximal."
        )
    except Exception as e:
        log.error(f"Crash dans la loop de scan: {e}", exc_info=True)
        await send_log_embed(
            LogLevel.CRITICAL,
            "[SCAN] Crash dans scan_loop",
            str(e)
        )


@scan_loop.error
async def scan_loop_error(error: Exception) -> None:
    log.error(f"Erreur dans scan_loop: {error}", exc_info=True)
    await send_log_embed(
        LogLevel.ERROR,
        "[SCAN] Erreur dans scan_loop",
        str(error)
    )

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
        await send_log_embed(
            LogLevel.WARNING,
            "[ADMIN] Permissions insuffisantes",
            f"Commande : {interaction.command}"
        )
    else:
        log.error(f"Erreur commande slash {interaction.command}: {error}", exc_info=True)
        await send_log_embed(
            LogLevel.ERROR,
            "[SLASH] Erreur interne",
            str(error)
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ Une erreur interne est survenue.", ephemeral=True
            )

# ─────────────────────────────────────────────
# 🧩 COMMANDES SLASH
# ─────────────────────────────────────────────
async def _send_help_embed(interaction: discord.Interaction):
    lang = bot.db.get_lang(interaction.channel.id)

    if lang == "fr":
        title = "🎮 Aide - Free Games Bot"
        public_title = "🟢 Commandes publiques"
        admin_title = "🛡️ Commandes administrateur"
    elif lang == "en":
        title = "🎮 Help - Free Games Bot"
        public_title = "🟢 Public commands"
        admin_title = "🛡️ Administrator commands"
    else:
        title = "🎮 Aide / Help - Free Games Bot"
        public_title = "🟢 Commandes publiques / Public commands"
        admin_title = "🛡️ Commandes administrateur / Admin commands"

    embed = discord.Embed(
        title=title,
        color=0x3498DB,
    )

    public_value = (
        "• `/aide` — Menu d'aide / Help menu\n"
        "• `/help` — Alias de `/aide`\n"
        "• `/platforms` — Boutiques surveillées / Monitored stores\n"
    )

    admin_value = (
        "• `/lang [fr/en/both]` — Change la langue du salon\n"
        "• `/check` — Force un scan manuel\n"
        "• `/platforms_test` — Test de fetch en direct\n"
        "• `/reset` — Réinitialise les jeux déjà envoyés\n"
        "• `/status` — Affiche l'état du bot et son uptime\n"
    )

    embed.add_field(
        name=public_title,
        value=public_value,
        inline=False,
    )
    embed.add_field(
        name=admin_title,
        value=admin_value,
        inline=False,
    )

    embed.set_footer(text="Free Games Bot • Slash commands")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    await _send_help_embed(interaction)


@bot.tree.command(name="help", description="Help menu")
async def cmd_help(interaction: discord.Interaction) -> None:
    await _send_help_embed(interaction)


@bot.tree.command(name="platforms", description="Liste des boutiques surveillées / Monitored stores")
async def cmd_platforms(interaction: discord.Interaction) -> None:
    lang = bot.db.get_lang(interaction.channel.id)

    if lang == "fr":
        header = "🛰️ **Boutiques surveillées :**"
    elif lang == "en":
        header = "🛰️ **Monitored stores:**"
    else:
        header = "🛰️ **Boutiques surveillées / Monitored stores:**"

    liste = ", ".join(sorted(PLATFORM_COLORS.keys()))
    await interaction.response.send_message(f"{header} {liste}")


@bot.tree.command(name="platforms_test", description="Test de fetch en direct (Admin uniquement)")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_platforms_test(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("⏳ Test des fetchers en cours...", ephemeral=True)

    if not bot.session:
        return await interaction.followup.send("❌ Session HTTP non initialisée.", ephemeral=True)

    try:
        raw_games = await fetch_games(bot.session)
        aggregated = aggregate_games_by_title(raw_games)

        total_raw = len(raw_games)
        total_aggregated = len(aggregated)

        platform_count = {}
        for g in aggregated:
            for p in g.get("platforms", []):
                platform_count[p] = platform_count.get(p, 0) + 1

        lines = [
            "🧪 **Test de fetch en direct**",
            f"• Jeux bruts récupérés : **{total_raw}**",
            f"• Jeux fusionnés (sans doublons) : **{total_aggregated}**",
            "",
            "📊 **Répartition par plateforme :**"
        ]

        for p, count in sorted(platform_count.items(), key=lambda x: -x[1]):
            lines.append(f"• **{p}** : {count}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

        await send_log_embed(
            LogLevel.INFO,
            "[ADMIN] /platforms_test exécuté",
            fields=[("Utilisateur", interaction.user.mention, True)]
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors du test : {e}", ephemeral=True)
        log.error(f"Erreur /platforms_test : {e}", exc_info=True)
        await send_log_embed(
            LogLevel.ERROR,
            "[ADMIN] Erreur /platforms_test",
            str(e)
        )


@bot.tree.command(name="check", description="Force un scan manuel")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_check(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("🔎 Scan manuel lancé...", ephemeral=True)

    await send_log_embed(
        LogLevel.INFO,
        "[ADMIN] /check exécuté",
        fields=[("Utilisateur", interaction.user.mention, True)]
    )

    await run_scan()


@bot.tree.command(name="reset", description="*(Admin)* Réinitialise les jeux déjà envoyés")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_reset(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("🗑️ Réinitialisation en cours...", ephemeral=True)

    try:
        await bot.db.db.execute("DELETE FROM sent_games")
        await bot.db.db.commit()
        bot.db.sent_cache.clear()

        await interaction.followup.send("✅ La liste des jeux envoyés a été réinitialisée.", ephemeral=True)
        log.info("🗑️ Commande /reset exécutée : cache + DB vidés.")

        await send_log_embed(
            LogLevel.WARNING,
            "[ADMIN] /reset exécuté",
            "Cache + DB vidés.",
            fields=[("Utilisateur", interaction.user.mention, True)]
        )

    except Exception as e:
        await interaction.followup.send(f"❌ Erreur lors du reset : {e}", ephemeral=True)
        log.error(f"Erreur /reset : {e}", exc_info=True)
        await send_log_embed(
            LogLevel.ERROR,
            "[ADMIN] Erreur /reset",
            str(e)
        )


@bot.tree.command(name="status", description="*(Admin)* Affiche l'état du bot et son uptime")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_status(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("📡 Récupération du statut...", ephemeral=True)

    uptime_seconds = int(time.time() - BOT_START_TIME)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    if days > 0:
        uptime_str = f"{days}j {hours}h {minutes}m"
    else:
        uptime_str = f"{hours}h {minutes}m {seconds}s"

    latency_ms = round(bot.latency * 1000)
    scan_state = "⏳ En cours" if bot.scan_lock.locked() else "✔️ Disponible"
    next_scan = f"{CHECK_INTERVAL // 60} min" if CHECK_INTERVAL >= 60 else f"{CHECK_INTERVAL} sec"

    try:
        async with bot.db.db.execute("SELECT COUNT(*) FROM sent_games") as cursor:
            (sent_count,) = await cursor.fetchone()
        async with bot.db.db.execute("SELECT COUNT(*) FROM settings") as cursor:
            (settings_count,) = await cursor.fetchone()
        db_state = "OK"
    except Exception:
        db_state = "❌ Erreur"
        sent_count = 0
        settings_count = 0

    http_state = "Oui" if bot.session and not bot.session.closed else "Non"
    python_version = platform.python_version()
    discord_version = discord.__version__

    lines = [
        "🛰️ **Free Games Bot — Status**",
        "",
        f"⏱️ **Uptime :** {uptime_str}",
        f"📡 **Latence Discord :** {latency_ms} ms",
        "",
        "🔎 **Scan :**",
        f"• État : {scan_state}",
        f"• Prochain scan dans : {next_scan}",
        "",
        "🗄️ **Base de données :**",
        f"• Connexion : {db_state}",
        f"• Jeux enregistrés : {sent_count}",
        f"• Salons configurés : {settings_count}",
        "",
        "⚡ **Cache mémoire :**",
        f"• Jeux envoyés : {len(bot.db.sent_cache)}",
        f"• Langues configurées : {len(bot.db.settings_cache)}",
        "",
        "🌐 **Session HTTP :**",
        f"• Ouverte : {http_state}",
        f"• User-Agent : FreeGamesBot/2.0",
        "",
        "🤖 **Version :**",
        f"• Python : {python_version}",
        f"• discord.py : {discord_version}",
    ]

    await interaction.followup.send("\n".join(lines), ephemeral=True)

    await send_log_embed(
        LogLevel.INFO,
        "[ADMIN] /status exécuté",
        fields=[("Utilisateur", interaction.user.mention, True)]
    )

# ─────────────────────────────────────────────
# 🚀 LANCEMENT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
