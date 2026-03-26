import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import json
from datetime import datetime
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# ⚙️ CONFIGURATION
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
raw_channel = os.environ.get("CHANNEL_ID")
CHANNEL_ID = int(raw_channel) if raw_channel and raw_channel.isdigit() else 0
# Correction : On récupère ROLE_ID depuis Railway ou on met "everyone" par défaut
ROLE_ID    = os.environ.get("ROLE_ID", "everyone")

SENT_GAMES_FILE = "sent_games.json"
CHECK_INTERVAL  = 60 

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Steam": 0x1B2838, "GOG": 0xA12B2E,
    "PlayStation": 0x003791, "Xbox": 0x107C10, "Prime Gaming": 0xFF9900,
    "Ubisoft": 0x0070D1, "Nintendo eShop": 0xE60012, "Indiegala": 0x2C3E50
}

# ─────────────────────────────────────────────
# 🧠 LOGIQUE DE GESTION
# ─────────────────────────────────────────────

def load_sent():
    if os.path.exists(SENT_GAMES_FILE):
        try:
            with open(SENT_GAMES_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent(sent):
    with open(SENT_GAMES_FILE, "w") as f:
        json.dump(list(sent), f)

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
    if any(w in title for w in ["edition", "bundle", "complete"]):
        score += 2
    return type_off, min(score, 5)

def build_fusion_embed(game):
    off_type, score = analyze_game(game)
    if off_type == "Ignore":
        return None
    embed = discord.Embed(
        title=f"🎁 {game['title']}",
        url=game["url"],
        description=f"**Plateforme :** {game['platform']}\n**Type :** {off_type}\n**Score :** {'⭐' * score}",
        color=PLATFORM_COLORS.get(game["platform"], 0x34495e),
        timestamp=datetime.utcnow()
    )
    if game.get("image"):
        embed.set_image(url=game["image"])
    embed.set_footer(text="Tracking Temps Réel • L'escouade DO")
    return embed

# ─────────────────────────────────────────────
# 🌐 SOURCES (FETCHERS)
# ─────────────────────────────────────────────

async def fetch_all_sources(session):
    all_games = []
    
    # EPIC GAMES
    try:
        async with session.get("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr", timeout=10) as r:
            data = await r.json()
            elements = data["data"]["Catalog"]["searchStore"]["elements"]
            for el in elements:
                promos = el.get("promotions", {}).get("promotionalOffers", [])
                if promos and any(o["discountSetting"]["discountPercentage"] == 0 for o in promos[0]["promotionalOffers"]):
                    slug = el.get("productSlug") or el.get("urlSlug")
                    all_games.append({
                        "platform": "Epic Games",
                        "title": el["title"],
                        "url": f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games",
                        "image": next((i["url"] for i in el.get("keyImages", []) if i["type"] in ["OfferImageWide", "Thumbnail"]), None),
                        "description": ""
                    })
    except: pass

    # GAMERPOWER (Steam, GOG, Switch, etc.)
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=10) as r:
            data = await r.json()
            for item in data:
                plat_raw = item.get("platforms", "").lower()
                platform = "Nintendo eShop" if "switch" in plat_raw or "nintendo" in plat_raw else item.get("platforms", "Autre").split(",")[0].strip()
                all_games.append({
                    "platform": platform,
                    "title": item.get("title"),
                    "url": item.get("open_giveaway_url"),
                    "image": item.get("image"),
                    "description": item.get("description", "")
                })
    except: pass
    
    return all_games

# ─────────────────────────────────────────────
# 🤖 BOT COMMANDS
# ─────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

async def run_scan(target_channel=None):
    """Fonction centrale de scan utilisée par la boucle et la commande !check"""
    channel = target_channel or bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    sent = load_sent()
    async with aiohttp.ClientSession() as session:
        flat_list = await fetch_all_sources(session)
    
    new_found = False
    for g in flat_list:
        key = f"{g['title']}".lower().strip()
        if key not in sent:
            embed = build_fusion_embed(g)
            if embed:
                # GESTION PROPRE DU @@EVERYONE
                if str(ROLE_ID).lower() == "everyone":
                    mention = "@everyone "
                else:
                    mention = f"<@&{ROLE_ID}> " if str(ROLE_ID).isdigit() else ""
                
                await channel.send(content=f"{mention}**Nouveau jeu détecté !**", embed=embed)
                sent.add(key)
                new_found = True
                await asyncio.sleep(1)
    if new_found:
        save_sent(sent)

@tasks.loop(minutes=CHECK_INTERVAL)
async def scan_loop():
    await run_scan()

@bot.event
async def on_ready():
    print(f"✅ Bot opérationnel : {bot.user}")
    if not scan_loop.is_running():
        scan_loop.start()

# --- LES COMMANDES ---

@bot.command()
async def aide(ctx):
    embed = discord.Embed(title="🎮 Menu d'Aide - Free Games Bot", color=0x3498DB)
    embed.add_field(name="!aide / !help", value="Affiche ce menu.", inline=False)
    embed.add_field(name="!freegames", value="Affiche les jeux gratuits en cours.", inline=False)
    embed.add_field(name="!plateformes", value="Liste les boutiques surveillées.", inline=False)
    embed.add_field(name="!check", value="Force un scan (Admin uniquement).", inline=False)
    embed.add_field(name="!reset", value="Efface l'historique (Admin uniquement).", inline=False)
    embed.set_footer(text="Tracking en temps réel activé.")
    await ctx.send(embed=embed)

@bot.command()
async def help(ctx):
    await aide(ctx)

@bot.command()
async def plateformes(ctx):
    liste = "\n".join([f"• {p}" for p in PLATFORM_COLORS.keys()])
    await ctx.send(f"🛰️ **Plateformes surveillées :**\n{liste}")

@bot.command()
async def freegames(ctx):
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            games = await fetch_all_sources(session)
            if not games: 
                return await ctx.send("Désolé, aucun jeu gratuit trouvé actuellement.")
            # On envoie les 3 premiers jeux trouvés pour ne pas spammer
            for g in games[:3]:
                embed = build_fusion_embed(g)
                if embed: await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def check(ctx):
    await ctx.send("🔎 **Scan manuel lancé...**")
    await run_scan(ctx.channel)
    await ctx.send("✅ **Scan terminé.**")

@bot.command()
@commands.has_permissions(administrator=True)
async def reset(ctx):
    if os.path.exists(SENT_GAMES_FILE):
        os.remove(SENT_GAMES_FILE)
        await ctx.send("✅ **Historique effacé.** Le prochain scan republiera tout.")
    else:
        await ctx.send("❌ Aucun historique trouvé.")

bot.run(BOT_TOKEN)
