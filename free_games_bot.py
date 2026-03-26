import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION (SÉCURISÉE)
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
# Sécurité pour le CHANNEL_ID
raw_channel = os.environ.get("CHANNEL_ID")
CHANNEL_ID = int(raw_channel) if raw_channel and raw_channel.isdigit() else 0

# Ton ID de rôle fixe (plus de os.environ ici pour éviter les bugs)
ROLE_ID    = "1125174549860851794" 

SENT_GAMES_FILE = "sent_games.json"
CHECK_INTERVAL  = 60 

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Steam": 0x1B2838, "GOG": 0xA12B2E,
    "PlayStation": 0x003791, "Xbox": 0x107C10, "Prime Gaming": 0xFF9900,
    "Ubisoft": 0x0070D1, "Nintendo eShop": 0xE60012, "Indiegala": 0x2C3E50
}

# ─────────────────────────────────────────────
# 🧠 INTELLIGENCE DE DÉTECTION
# ─────────────────────────────────────────────

def analyze_game(game):
    title = str(game.get("title", "")).lower()
    desc = str(game.get("description", "")).lower()
    full_text = title + " " + desc
    
    if any(x in full_text for x in ["demo", "beta", "playtest", "trial version"]):
        return "Ignore", 0
    elif any(x in full_text for x in ["free weekend", "temporaire", "essai gratuit"]):
        type_off = "⏳ Temporaire"
    else:
        type_off = "🎁 Permanent"
        
    score = 1
    if game["platform"] in ["Epic Games", "GOG", "Nintendo eShop"]: score += 1
    if any(w in title for w in ["ultimate", "definitive", "edition", "bundle"]): score += 2
    if score > 5: score = 5
    
    return type_off, score

# ─────────────────────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────────────────────

async def fetch_epic(session):
    games = []
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    try:
        async with session.get(url, timeout=15) as r:
            data = await r.json()
            elements = data["data"]["Catalog"]["searchStore"]["elements"]
            for el in elements:
                promos = el.get("promotions", {}).get("promotionalOffers", [])
                if promos and any(o["discountSetting"]["discountPercentage"] == 0 for o in promos[0]["promotionalOffers"]):
                    slug = el.get("productSlug") or el.get("urlSlug")
                    games.append({
                        "platform": "Epic Games",
                        "title": el["title"],
                        "url": f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games",
                        "image": next((i["url"] for i in el.get("keyImages", []) if i["type"] in ["OfferImageWide", "Thumbnail"]), None),
                        "description": "Cadeau de la semaine Epic Games"
                    })
    except: pass
    return games

async def fetch_steam(session):
    games = []
    url = "https://store.steampowered.com/search/?maxprice=free&specials=1"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with session.get(url, headers=headers, timeout=15) as r:
            soup = BeautifulSoup(await r.text(), "html.parser")
            for item in soup.select(".search_result_row")[:10]:
                title = item.select_one(".title").text
                img_url = item.select_one("img").get("src").replace("capsule_sm_120", "header")
                games.append({
                    "platform": "Steam",
                    "title": title,
                    "url": item.get("href").split("?")[0],
                    "image": img_url,
                    "description": "Promotion Steam"
                })
    except: pass
    return games

async def fetch_gamerpower(session):
    games = []
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=15) as r:
            data = await r.json()
            if isinstance(data, list):
                for item in data:
                    plat_raw = item.get("platforms", "").lower()
                    # Détection Nintendo
                    platform = "Nintendo eShop" if "switch" in plat_raw or "nintendo" in plat_raw else item.get("platforms", "Autre").split(",")[0].strip()
                    games.append({
                        "platform": platform,
                        "title": item.get("title"),
                        "url": item.get("open_giveaway_url"),
                        "image": item.get("image"),
                        "description": item.get("description")
                    })
    except: pass
    return games

# ─────────────────────────────────────────────
# 🤖 BOT CORE
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=CHECK_INTERVAL)
async def scan_loop():
    if not CHANNEL_ID: return
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return

    async with aiohttp.ClientSession() as session:
        all_results = await asyncio.gather(fetch_epic(session), fetch_steam(session), fetch_gamerpower(session))
        flat_list = [g for sub in all_results for g in sub if g]

    for g in flat_list:
        off_type, score = analyze_game(g)
        if off_type == "Ignore": continue
        
        # On ne stocke/vérifie que les nouveaux jeux ici (logique simplifiée pour Railway)
        embed = discord.Embed(title=f"🎁 {g['title']}", url=g["url"], color=PLATFORM_COLORS.get(g["platform"], 0x34495e))
        embed.description = f"**Plateforme :** {g['platform']}\n**Type :** {off_type}\n**Score :** {'⭐' * score}"
        if g.get("image"): embed.set_image(url=g["image"])
        
        # Test d'envoi (tu peux remettre la logique de sent_games après test)
        try:
            mention = "@everyone " if ROLE_ID == "everyone" else (f"<@&{ROLE_ID}> " if ROLE_ID else "")
            await channel.send(content=f"{mention}**Nouveau jeu détecté !**", embed=embed)
        except: pass
        await asyncio.sleep(2)

@bot.event
async def on_ready():
    print(f"✅ Bot prêt : {bot.user}")
    if not scan_loop.is_running(): scan_loop.start()

bot.run(BOT_TOKEN)
