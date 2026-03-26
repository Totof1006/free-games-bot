import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
import time
from datetime import datetime
from bs4 import BeautifulSoup

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

if not BOT_TOKEN or not CHANNEL_ID:
    raise ValueError("BOT_TOKEN ou CHANNEL_ID manquant")

CHANNEL_ID = int(CHANNEL_ID)

ROLE_MAP = {
    "Epic Games": 123456789012345678,
    "Steam": 123456789012345678,
    "GOG": 123456789012345678
}

CHECK_INTERVAL = 60  # minutes
SENT_FILE = "sent_games.json"

# ─────────────────────────────
# DISCORD INTENTS
# ─────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ─────────────────────────────
# STORAGE
# ─────────────────────────────
def load_sent():
    if os.path.exists(SENT_FILE):
        try:
            with open(SENT_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent(data):
    with open(SENT_FILE, "w") as f:
        json.dump(list(data), f)

# ─────────────────────────────
# 🧠 DETECTION
# ─────────────────────────────
def detect_offer(game):
    text = (game["title"] + " " + game.get("description","")).lower()

    if any(x in text for x in ["demo", "beta", "playtest", "dlc"]):
        return "ignore"

    if any(x in text for x in ["trial", "weekend", "try for free"]):
        return "temporary"

    if game["platform"] in ["Epic Games", "GOG", "Prime Gaming"]:
        return "permanent"

    return "unknown"

# ─────────────────────────────
# 📊 SCORE + VALEUR
# ─────────────────────────────
def score_game(game):
    score = 0
    title = game["title"].lower()

    if len(title) > 12:
        score += 1

    if game["platform"] in ["Epic Games", "GOG"]:
        score += 2

    if any(w in title for w in ["ultimate", "definitive", "complete"]):
        score += 2

    if any(w in title for w in ["simulator"]):
        score -= 1

    return score

def estimate_value(game):
    title = game["title"].lower()

    if "ultimate" in title:
        return "50€+"
    if "definitive" in title:
        return "40€"
    if len(title) > 15:
        return "20-30€"

    return "10-20€"

# ─────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────
async def fetch_epic(session):
    games = []
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"

    try:
        async with session.get(url, timeout=10) as r:
            data = await r.json()
            for el in data["data"]["Catalog"]["searchStore"]["elements"]:
                promos = el.get("promotions")
                if not promos:
                    continue

                for offer in promos.get("promotionalOffers", []):
                    for p in offer.get("promotionalOffers", []):
                        if p["discountSetting"]["discountPercentage"] == 0:
                            games.append({
                                "platform": "Epic Games",
                                "title": el["title"],
                                "url": "https://store.epicgames.com",
                                "description": "Epic giveaway"
                            })
    except Exception as e:
        print("Erreur Epic:", e)

    return games


async def fetch_gog(session):
    games = []
    url = "https://www.gog.com/games/ajax/filtered?price=free"

    try:
        async with session.get(url, timeout=10) as r:
            data = await r.json()
            for g in data.get("products", []):
                games.append({
                    "platform": "GOG",
                    "title": g["title"],
                    "url": f"https://gog.com{g['url']}",
                    "description": "GOG free"
                })
    except Exception as e:
        print("Erreur GOG:", e)

    return games


async def fetch_steam(session):
    games = []
    url = "https://store.steampowered.com/search/?maxprice=free&specials=1"

    try:
        async with session.get(url, timeout=10) as r:
            soup = BeautifulSoup(await r.text(), "html.parser")

            for item in soup.select(".search_result_row")[:20]:
                title_tag = item.select_one(".title")
                if not title_tag:
                    continue

                title = title_tag.text

                if any(x in title.lower() for x in ["demo", "dlc"]):
                    continue

                games.append({
                    "platform": "Steam",
                    "title": title,
                    "url": item.get("href"),
                    "description": "Steam deal"
                })
    except Exception as e:
        print("Erreur Steam:", e)

    return games

# ─────────────────────────────
# GLOBAL FETCH
# ─────────────────────────────
async def fetch_all(session):
    results = await asyncio.gather(
        fetch_epic(session),
        fetch_gog(session),
        fetch_steam(session)
    )

    flat = [g for sub in results for g in sub]

    seen, unique = set(), []
    for g in flat:
        key = f"{g['title']}_{g['platform']}".lower()
        if key not in seen:
            seen.add(key)
            unique.append(g)

    return unique

# ─────────────────────────────
# EMBED
# ─────────────────────────────
def build_embed(game):
    if not game.get("title") or not game.get("url"):
        return None

    score = score_game(game)
    value = estimate_value(game)
    offer = detect_offer(game)

    badge = "🔥 PÉPITE" if score >= 4 else "🔥 BON PLAN" if score >= 3 else ""

    embed = discord.Embed(
        title=f"{badge} {game['title']}",
        url=game["url"],
        description=f"{game['platform']}",
        color=0x00ff99,
        timestamp=datetime.utcnow()
    )

    embed.add_field(name="🎯 Type", value=offer)
    embed.add_field(name="⭐ Score", value=str(score))
    embed.add_field(name="💰 Valeur", value=value)

    return embed

# ─────────────────────────────
# WATCHDOG
# ─────────────────────────────
LAST_RUN = time.time()

def update_heartbeat():
    global LAST_RUN
    LAST_RUN = time.time()

async def watchdog():
    await bot.wait_until_ready()

    while not bot.is_closed():
        await asyncio.sleep(60)

        if time.time() - LAST_RUN > 300:
            print("⚠️ Bot bloqué → redémarrage loop")

            try:
                if loop.is_running():
                    loop.cancel()
                loop.start()
            except Exception as e:
                print("Erreur watchdog:", e)

# ─────────────────────────────
# LOOP
# ─────────────────────────────
@tasks.loop(minutes=CHECK_INTERVAL)
async def loop():
    update_heartbeat()

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("Channel introuvable")
        return

    sent = load_sent()

    async with aiohttp.ClientSession() as session:
        games = await fetch_all(session)

    for g in games:
        if detect_offer(g) == "ignore":
            continue

        key = f"{g['title']}_{g['platform']}".lower()

        if key not in sent:
            role_id = ROLE_MAP.get(g["platform"])
            mention = f"<@&{role_id}>" if role_id else ""

            embed = build_embed(g)
            if not embed:
                continue

            await channel.send(
                content=f"{mention} 🎮 Nouveau jeu détecté !",
                embed=embed
            )

            sent.add(key)
            await asyncio.sleep(1.5)

    save_sent(sent)

# ─────────────────────────────
# EVENTS
# ─────────────────────────────
@bot.event
async def on_ready():
    print("🚀 BOT GOD TIER ONLINE")

    if not loop.is_running():
        loop.start()

    bot.loop.create_task(watchdog())

# ─────────────────────────────
# AUTO-RESTART GLOBAL
# ─────────────────────────────
while True:
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print("💥 Crash détecté :", e)
        time.sleep(5)
