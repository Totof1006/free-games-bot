import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import asyncio
from datetime import datetime

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or 0)
# Ton ID de rôle pour les notifications
ROLE_ID    = "1125174549860851794" 

SENT_GAMES_FILE      = "sent_games.json"
CHECK_INTERVAL_HOURS = 1

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Xbox Game Pass": 0x107C10, "PlayStation Plus": 0x003791,
    "Steam": 0x1B2838, "Blizzard": 0x148EFF, "EA Play": 0xFF4747,
    "Prime Gaming": 0xFF9900, "GOG": 0xA12B2E, "Ubisoft Connect": 0x0070D1,
    "Humble Bundle": 0xCC3300, "itch.io": 0xFA5C5C, "Fanatical": 0xE84B3A,
    "Rockstar Games": 0xFCB813, "Microsoft Store": 0x00A4EF, "Indiegala": 0x2C3E50,
}

PLATFORM_EMOJIS = {
    "Epic Games": "🟢", "Xbox Game Pass": "🟩", "PlayStation Plus": "🔵",
    "Steam": "🖥️", "Blizzard": "💙", "EA Play": "🔴", "Prime Gaming": "🟠",
    "GOG": "🟤", "Ubisoft Connect": "🔷", "Humble Bundle": "📦",
    "itch.io": "🎀", "Fanatical": "🔥", "Rockstar Games": "⭐",
    "Microsoft Store": "🪟", "Indiegala": "🎲",
}

GAMERPOWER_PLATFORM_MAP = {
    "steam": "Steam", "epic-games-store": "Epic Games", "xbox-game-pass": "Xbox Game Pass",
    "ps4": "PlayStation Plus", "ps5": "PlayStation Plus", "battle.net": "Blizzard",
    "ea-games": "EA Play", "prime-gaming": "Prime Gaming", "gog": "GOG",
    "ubisoft": "Ubisoft Connect", "itch": "itch.io", "humble": "Humble Bundle",
    "fanatical": "Fanatical", "rockstar": "Rockstar Games", "microsoft-store": "Microsoft Store",
    "indiegala": "Indiegala",
}

# ─────────────────────────────────────────────
# 📦  GESTION DE L'HISTORIQUE
# ─────────────────────────────────────────────
def load_sent_games() -> set:
    if os.path.exists(SENT_GAMES_FILE):
        try:
            with open(SENT_GAMES_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_sent_games(sent: set):
    with open(SENT_GAMES_FILE, "w") as f:
        json.dump(list(sent), f)

# ─────────────────────────────────────────────
# 🌐  RECUPÉRATION DES JEUX (DÉDUPLIQUÉS)
# ─────────────────────────────────────────────

async def fetch_epic_games(session: aiohttp.ClientSession) -> list:
    games = []
    url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr"
    try:
        async with session.get(url, timeout=10) as r:
            if r.status == 200:
                data = await r.json()
                elements = data["data"]["Catalog"]["searchStore"]["elements"]
                for el in elements:
                    promos = el.get("promotions") or {}
                    offers = promos.get("promotionalOffers", [])
                    if offers and any(o["discountSetting"]["discountPercentage"] == 0 for o in offers[0]["promotionalOffers"]):
                        slug = next((m["pageSlug"] for m in el.get("catalogNs", {}).get("mappings", []) if "pageSlug" in m), "")
                        games.append({
                            "platform": "Epic Games",
                            "title": el.get("title", "Inconnu"),
                            "url": f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games",
                            "image": next((i["url"] for i in el.get("keyImages", []) if i["type"] == "OfferImageWide"), ""),
                            "end_date": offers[0]["promotionalOffers"][0]["endDate"][:10],
                        })
    except: pass
    return games

async def fetch_gamerpower_games(session: aiohttp.ClientSession) -> list:
    games = []
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=10) as r:
            if r.status == 200:
                for item in await r.json():
                    raw = item.get("platforms", "").lower()
                    matched = next((v for k, v in GAMERPOWER_PLATFORM_MAP.items() if k in raw), "Autre")
                    games.append({
                        "platform": matched,
                        "title": item.get("title"),
                        "url": item.get("open_giveaway_url"),
                        "image": item.get("image"),
                        "end_date": item.get("end_date", "N/A"),
                        "description": item.get("description"),
                    })
    except: pass
    return games

async def fetch_all_free_games(session: aiohttp.ClientSession) -> list:
    results = await asyncio.gather(fetch_epic_games(session), fetch_gamerpower_games(session), return_exceptions=True)
    flat_list = [item for sublist in results if isinstance(sublist, list) for item in sublist]
    
    seen, unique = set(), []
    for g in flat_list:
        clean_title = g['title'].lower().strip()
        if clean_title not in seen:
            seen.add(clean_title)
            unique.append(g)
    return unique

# ─────────────────────────────────────────────
# 🤖  COMMANDES ET LOGIQUE DU BOT
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def build_embed(game: dict) -> discord.Embed:
    platform = game.get("platform", "Inconnu")
    embed = discord.Embed(
        title=f"🎁 {game['title']}",
        url=game.get("url"),
        description=game.get("description") or "Nouveau jeu gratuit disponible !",
        color=PLATFORM_COLORS.get(platform, 0xFFFFFF),
        timestamp=datetime.utcnow()
    )
    embed.set_author(name=f"{PLATFORM_EMOJIS.get(platform, '🎮')} {platform}")
    embed.add_field(name="⏳ Jusqu'au", value=f"`{game.get('end_date', 'N/A')}`", inline=True)
    if game.get("image"): embed.set_image(url=game["image"])
    embed.set_footer(text="Free Games Bot • L'escouade DO")
    return embed

@bot.event
async def on_ready():
    print(f"✅ Bot prêt : {bot.user}")
    if not check_free_games.is_running():
        check_free_games.start()

@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def check_free_games():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel: return

    sent_games = load_sent_games()
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_free_games(session)

    new_found = False
    for game in games:
        key = f"{game['title']}".lower().strip()
        if key not in sent_games:
            mention = f"<@&{ROLE_ID}> " if ROLE_ID else ""
            await channel.send(content=f"{mention}**Nouveau jeu gratuit détecté !**", embed=build_embed(game))
            sent_games.add(key)
            new_found = True
            await asyncio.sleep(2) 

    if new_found:
        save_sent_games(sent_games)

# --- COMMANDES UTILISATEURS ---

@bot.command()
async def freegames(ctx):
    """Affiche tous les jeux gratuits actuels."""
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            games = await fetch_all_free_games(session)
        if not games:
            return await ctx.send("Rien pour le moment !")
        for g in games[:8]: # Affiche les 8 premiers
            await ctx.send(embed=build_embed(g))

@bot.command()
async def plateforme(ctx, *, name: str):
    """Filtre les jeux par plateforme (ex: !plateforme Steam)."""
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            games = await fetch_all_free_games(session)
        filtered = [g for g in games if name.lower() in g['platform'].lower()]
        if not filtered:
            return await ctx.send(f"Aucun jeu trouvé pour '{name}'.")
        for g in filtered[:5]:
            await ctx.send(embed=build_embed(g))

@bot.command()
async def plateformes(ctx):
    """Liste les plateformes surveillées."""
    liste = "\n".join([f"{emoji} {name}" for name, emoji in PLATFORM_EMOJIS.items()])
    embed = discord.Embed(title="Plateformes surveillées", description=liste, color=0x3498DB)
    await ctx.send(embed=embed)

@bot.command()
async def aide(ctx):
    """Affiche ce menu d'aide."""
    embed = discord.Embed(title="Aide du Free Games Bot", color=0x9B59B6)
    embed.add_field(name="!freegames", value="Affiche les jeux gratuits actuels.", inline=False)
    embed.add_field(name="!plateforme <nom>", value="Filtre par boutique (ex: !plateforme Epic).", inline=False)
    embed.add_field(name="!plateformes", value="Liste toutes les boutiques surveillées.", inline=False)
    embed.add_field(name="!check", value="Force une vérification (Admin).", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def check(ctx):
    """Force un scan immédiat (Admin)."""
    await ctx.send("🔄 Scan manuel lancé...")
    await check_free_games()
    await ctx.send("✅ Scan terminé.")

@bot.command()
@commands.has_permissions(administrator=True)
async def reset(ctx):
    """Réinitialise l'historique (Admin)."""
    if os.path.exists(SENT_GAMES_FILE):
        os.remove(SENT_GAMES_FILE)
        await ctx.send("🗑️ Historique vidé. Au prochain scan, tous les jeux seront renvoyés.")
    else:
        await ctx.send("L'historique est déjà vide.")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
