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
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or 0)
ROLE_ID    = "1125174549860851794" 

SENT_GAMES_FILE = "sent_games.json"
CHECK_INTERVAL  = 60 # minutes

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Steam": 0x1B2838, "GOG": 0xA12B2E,
    "PlayStation": 0x003791, "Xbox": 0x107C10, "Prime Gaming": 0xFF9900,
    "Ubisoft": 0x0070D1, "Indiegala": 0x2C3E50, "Itch.io": 0xFA5C5C
}

# ─────────────────────────────────────────────
# 🧠 INTELLIGENCE DE DÉTECTION
# ─────────────────────────────────────────────

def analyze_game(game):
    title = game["title"].lower()
    desc = game.get("description", "").lower()
    full_text = title + " " + desc
    
    # 1. Détection du type (Évite les DLC ou démos)
    if any(x in full_text for x in ["demo", "beta", "playtest", "trial version"]):
        return "Ignore", 0
    elif any(x in full_text for x in ["free weekend", "temporaire", "essai gratuit"]):
        type_off = "⏳ Temporaire"
    else:
        type_off = "🎁 Permanent"
        
    # 2. Calcul du Score (0 à 5)
    score = 1
    if game["platform"] in ["Epic Games", "GOG"]: score += 1
    if any(w in title for w in ["ultimate", "definitive", "edition", "bundle"]): score += 2
    if len(title) > 15: score += 1
    if score > 5: score = 5
    
    return type_off, score

# ─────────────────────────────────────────────
# 🌐 FETCHERS (Tracking optimisé)
# ─────────────────────────────────────────────

async def fetch_epic(session):
    games = []
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    try:
        async with session.get(url, timeout=10) as r:
            data = await r.json()
            elements = data["data"]["Catalog"]["searchStore"]["elements"]
            for el in elements:
                promos = el.get("promotions", {}).get("promotionalOffers", [])
                if promos and any(o["discountSetting"]["discountPercentage"] == 0 for o in promos[0]["promotionalOffers"]):
                    # Correction du lien Epic
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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        async with session.get(url, headers=headers, timeout=10) as r:
            soup = BeautifulSoup(await r.text(), "html.parser")
            for item in soup.select(".search_result_row")[:10]:
                title = item.select_one(".title").text
                link = item.get("href").split("?")[0]
                img_url = item.select_one("img").get("src").replace("capsule_sm_120", "header")
                games.append({
                    "platform": "Steam",
                    "title": title,
                    "url": link,
                    "image": img_url,
                    "description": "Promotion Steam"
                })
    except: pass
    return games

async def fetch_gamerpower(session):
    games = []
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=10) as r:
            data = await r.json()
            if isinstance(data, list):
                for item in data:
                    games.append({
                        "platform": item.get("platforms", "Autre").split(",")[0].strip(),
                        "title": item.get("title"),
                        "url": item.get("open_giveaway_url"),
                        "image": item.get("image"),
                        "description": item.get("description")
                    })
    except: pass
    return games

# ─────────────────────────────────────────────
# 🤖 LOGIQUE DU BOT
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def load_sent():
    if os.path.exists(SENT_GAMES_FILE):
        try:
            with open(SENT_GAMES_FILE, "r") as f: return set(json.load(f))
        except: return set()
    return set()

def save_sent(sent):
    with open(SENT_GAMES_FILE, "w") as f: json.dump(list(sent), f)

def build_fusion_embed(game):
    off_type, score = analyze_game(game)
    if off_type == "Ignore": return None

    color = PLATFORM_COLORS.get(game["platform"], 0x34495e)
    stars = "⭐" * score
    
    embed = discord.Embed(
        title=f"🎁 {game['title']}",
        url=game["url"],
        description=f"**Plateforme :** {game['platform']}\n**Type :** {off_type}\n**Score :** {stars}",
        color=color,
        timestamp=datetime.utcnow()
    )
    if game.get("image"): embed.set_image(url=game["image"])
    embed.set_footer(text="Tracking Temps Réel • L'escouade DO")
    return embed

@tasks.loop(minutes=CHECK_INTERVAL)
async def scan_loop():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return

    sent = load_sent()
    async with aiohttp.ClientSession() as session:
        all_results = await asyncio.gather(fetch_epic(session), fetch_steam(session), fetch_gamerpower(session))
        flat_list = [g for sub in all_results for g in sub if g]

    new_count = 0
    for g in flat_list:
        key = f"{g['title']}".lower().strip()
        if key not in sent:
            embed = build_fusion_embed(g)
            if embed:
                mention = f"<@&{ROLE_ID}> " if ROLE_ID else ""
                await channel.send(content=f"{mention}**Nouveau jeu détecté !**", embed=embed)
                sent.add(key)
                new_count += 1
                await asyncio.sleep(2) # Évite le spam Discord

    if new_count > 0: save_sent(sent)

@bot.event
async def on_ready():
    print(f"✅ Bot Fusion connecté : {bot.user}")
    if not scan_loop.is_running(): scan_loop.start()

# --- COMMANDES ---

@bot.command()
async def aide(ctx):
    embed = discord.Embed(title="Aide - Free Games Bot", color=0x9B59B6)
    embed.add_field(name="!freegames", value="Affiche les meilleures offres actuelles.", inline=False)
    embed.add_field(name="!plateformes", value="Liste les boutiques surveillées.", inline=False)
    embed.add_field(name="!check", value="Force un scan (Admin).", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def plateformes(ctx):
    names = "\n".join([f"• {p}" for p in PLATFORM_COLORS.keys()])
    await ctx.send(f"**Boutiques surveillées :**\n{names}")

@bot.command()
async def freegames(ctx):
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            epic = await fetch_epic(session)
            if not epic: return await ctx.send("Aucune offre majeure à afficher via la commande rapide.")
            for g in epic[:3]:
                embed = build_fusion_embed(g)
                if embed: await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def check(ctx):
    await ctx.send("🔎 Scan manuel lancé...")
    await scan_loop()
    await ctx.send("✅ Scan terminé.")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
