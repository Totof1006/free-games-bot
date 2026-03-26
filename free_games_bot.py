import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
raw_channel = os.environ.get("CHANNEL_ID")
CHANNEL_ID = int(raw_channel) if raw_channel and raw_channel.isdigit() else 0
ROLE_ID    = "1125174549860851794" 

SENT_GAMES_FILE = "sent_games.json"
CHECK_INTERVAL  = 60 

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Steam": 0x1B2838, "GOG": 0xA12B2E,
    "PlayStation": 0x003791, "Xbox": 0x107C10, "Prime Gaming": 0xFF9900,
    "Ubisoft": 0x0070D1, "Nintendo eShop": 0xE60012, "Indiegala": 0x2C3E50
}

# ─────────────────────────────────────────────
# 🧠 FONCTIONS DE GESTION
# ─────────────────────────────────────────────

def load_sent():
    if os.path.exists(SENT_GAMES_FILE):
        try:
            with open(SENT_GAMES_FILE, "r") as f:
                data = json.load(f)
                return set(data)
        except Exception:
            return set()
    return set()

def save_sent(sent):
    try:
        with open(SENT_GAMES_FILE, "w") as f:
            json.dump(list(sent), f)
    except Exception as e:
        print(f"Erreur sauvegarde JSON : {e}")

def analyze_game(game):
    title = str(game.get("title", "")).lower()
    desc = str(game.get("description", "")).lower()
    full_text = title + " " + desc
    
    if any(x in full_text for x in ["demo", "beta", "playtest", "trial"]):
        return "Ignore", 0
    
    type_off = "⏳ Temporaire" if "weekend" in full_text else "🎁 Permanent"
    
    score = 1
    if game["platform"] in ["Epic Games", "GOG", "Nintendo eShop"]:
        score += 1
    if any(w in title for w in ["edition", "bundle", "ultimate", "complete"]):
        score += 2
        
    return type_off, min(score, 5)

def build_fusion_embed(game):
    off_type, score = analyze_game(game)
    if off_type == "Ignore":
        return None
        
    color = PLATFORM_COLORS.get(game["platform"], 0x34495e)
    stars = "⭐" * score
    
    embed = discord.Embed(
        title=f"🎁 {game['title']}",
        url=game["url"],
        description=f"**Plateforme :** {game['platform']}\n**Type :** {off_type}\n**Score :** {stars}",
        color=color,
        timestamp=datetime.utcnow()
    )
    if game.get("image"):
        embed.set_image(url=game["image"])
    embed.set_footer(text="Tracking Temps Réel • L'escouade DO")
    return embed

# ─────────────────────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────────────────────

async def fetch_epic(session):
    games = []
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    try:
        async with session.get(url, timeout=15) as r:
            data = await r.json()
            elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])
            for el in elements:
                promos = el.get("promotions", {}).get("promotionalOffers", [])
                if promos and any(o["discountSetting"]["discountPercentage"] == 0 for o in promos[0]["promotionalOffers"]):
                    slug = el.get("productSlug") or el.get("urlSlug")
                    games.append({
                        "platform": "Epic Games",
                        "title": el.get("title", "Jeu sans titre"),
                        "url": f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games",
                        "image": next((i["url"] for i in el.get("keyImages", []) if i["type"] in ["OfferImageWide", "Thumbnail"]), None)
                    })
    except Exception:
        pass
    return games

async def fetch_steam(session):
    games = []
    url = "https://store.steampowered.com/search/?maxprice=free&specials=1"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with session.get(url, headers=headers, timeout=15) as r:
            soup = BeautifulSoup(await r.text(), "html.parser")
            for item in soup.select(".search_result_row")[:10]:
                title_el = item.select_one(".title")
                img_el = item.select_one("img")
                if title_el:
                    games.append({
                        "platform": "Steam",
                        "title": title_el.text,
                        "url": item.get("href", "").split("?")[0],
                        "image": img_el.get("src", "").replace("capsule_sm_120", "header") if img_el else None
                    })
    except Exception:
        pass
    return games

async def fetch_gamerpower(session):
    games = []
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=15) as r:
            data = await r.json()
            if isinstance(data, list):
                for item in data:
                    plat_raw = item.get("platforms", "").lower()
                    platform = "Nintendo eShop" if "switch" in plat_raw or "nintendo" in plat_raw else item.get("platforms", "Autre").split(",")[0].strip()
                    games.append({
                        "platform": platform,
                        "title": item.get("title", "Inconnu"),
                        "url": item.get("open_giveaway_url"),
                        "image": item.get("image"),
                        "description": item.get("description")
                    })
    except Exception:
        pass
    return games

# ─────────────────────────────────────────────
# 🤖 BOT CORE
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=CHECK_INTERVAL)
async def scan_loop():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
        
    sent = load_sent()
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(fetch_epic(session), fetch_steam(session), fetch_gamerpower(session))
        flat_list = [g for sub in results for g in sub if g]
    
    new_found = False
    for g in flat_list:
        key = f"{g['title']}".lower().strip()
        if key not in sent:
            embed = build_fusion_embed(g)
            if embed:
                if ROLE_ID.lower() == "everyone":
                    mention = "@everyone "
                else:
                    mention = f"<@&{ROLE_ID}> " if ROLE_ID and ROLE_ID.isdigit() else ""
                
                try:
                    await channel.send(content=f"{mention}**Nouveau jeu détecté !**", embed=embed)
                    sent.add(key)
                    new_found = True
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"Erreur envoi message : {e}")
                    
    if new_found:
        save_sent(sent)

@bot.event
async def on_ready():
    print(f"✅ Bot Fusion Opérationnel : {bot.user}")
    if not scan_loop.is_running():
        scan_loop.start()

@bot.command()
async def aide(ctx):
    embed = discord.Embed(title="Aide Free Games Bot", color=0x9B59B6)
    embed.add_field(name="!freegames", value="Affiche les offres Epic Games actuelles.", inline=False)
    embed.add_field(name="!plateformes", value="Liste les boutiques trackées.", inline=False)
    embed.add_field(name="!check", value="Lance un scan manuel (Admin).", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def plateformes(ctx):
    liste = "\n".join([f"• {p}" for p in PLATFORM_COLORS.keys()])
    await ctx.send(f"**Plateformes surveillées :**\n{liste}")

@bot.command()
@commands.has_permissions(administrator=True)
async def check(ctx):
    await ctx.send("🔎 Scan manuel en cours...")
    await scan_loop()
    await ctx.send("✅ Scan terminé.")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
