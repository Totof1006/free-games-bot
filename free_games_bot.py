import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import re
import asyncio
from datetime import datetime

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or 0)
# Optionnel : ID du rôle à mentionner (ex: 123456789). Laisse à None sinon.
ROLE_ID    = os.environ.get("ROLE_ID") 

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
        except (json.JSONDecodeError, IOError):
            return set()
    return set()

def save_sent_games(sent: set):
    try:
        with open(SENT_GAMES_FILE, "w") as f:
            json.dump(list(sent), f)
    except IOError as e:
        print(f"❌ Erreur sauvegarde : {e}")

# ─────────────────────────────────────────────
# 🌐  FETCHERS (Optimisés)
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
    except Exception as e: print(f"[Epic] {e}")
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
    except Exception as e: print(f"[GamerPower] {e}")
    return games

async def fetch_all_free_games(session: aiohttp.ClientSession) -> list:
    results = await asyncio.gather(
        fetch_epic_games(session),
        fetch_gamerpower_games(session),
        return_exceptions=True
    )
    flat_list = [item for sublist in results if isinstance(sublist, list) for item in sublist]
    seen, unique = set(), []
    for g in flat_list:
        key = f"{g['platform']}_{g['title']}".lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(g)
    return unique

# ─────────────────────────────────────────────
# 🤖  BOT DISCORD
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def build_embed(game: dict) -> discord.Embed:
    platform = game.get("platform", "Inconnu")
    color    = PLATFORM_COLORS.get(platform, 0xFFFFFF)
    emoji    = PLATFORM_EMOJIS.get(platform, "🎮")

    embed = discord.Embed(
        title       = f"🎁 {game['title']}",
        url         = game.get("url", ""),
        description = game.get("description") or "Un jeu gratuit est disponible !",
        color       = color,
        timestamp   = datetime.utcnow(),
    )
    embed.set_author(name=f"{emoji} {platform}")
    embed.add_field(name="⏳ Disponible jusqu'au", value=game.get("end_date", "N/A"),                inline=True)
    embed.add_field(name="🔗 Récupérer le jeu",    value=f"[Cliquez ici]({game.get('url', '')})",   inline=True)

    # --- PARTIE MODIFIÉE ---
    if game.get("image"):
        # On utilise set_image à la place de set_thumbnail pour un affichage en grand
        embed.set_image(url=game["image"])
    # ----------------------

    embed.set_footer(text="Free Games Bot • 15 plateformes surveillées")
    return embed

@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user}")
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
        key = f"{game['platform']}_{game['title']}".lower().replace(" ", "_")
        if key not in sent_games:
            mention = f"<@&{ROLE_ID}> " if ROLE_ID else ""
            await channel.send(content=f"{mention}**Nouveau jeu gratuit détecté !**", embed=build_embed(game))
            sent_games.add(key)
            new_found = True
            await asyncio.sleep(2) 

    if new_found:
        save_sent_games(sent_games)

@bot.command()
async def freegames(ctx):
    """Affiche les jeux gratuits du moment."""
    async with ctx.typing():
        async with aiohttp.ClientSession() as session:
            games = await fetch_all_free_games(session)
        if not games:
            return await ctx.send("Rien pour le moment !")
        for g in games[:5]: # Limite à 5 pour éviter le spam
            await ctx.send(embed=build_embed(g))

@bot.command()
@commands.has_permissions(administrator=True)
async def check(ctx):
    """Force la vérification."""
    await ctx.send("🔄 Scan en cours...")
    await check_free_games()
    await ctx.send("✅ Scan terminé.")

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
