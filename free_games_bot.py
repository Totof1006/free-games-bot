import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import os
import json
from datetime import datetime

# ─────────────────────────────────────────────
# ⚙️ CONFIGURATION & TRADUCTIONS
# ─────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
raw_channel = os.environ.get("CHANNEL_ID")
CHANNEL_ID = int(raw_channel) if raw_channel and raw_channel.isdigit() else 0
ROLE_ID    = os.environ.get("ROLE_ID", "everyone")

SENT_GAMES_FILE = "sent_games.json"
SETTINGS_FILE   = "settings.json"
CHECK_INTERVAL  = 60 

LOCALES = {
    "fr": {
        "NEW_GAME": "📢 **Nouveau jeu détecté !**",
        "PLATFORM": "🎮 **Plateforme**",
        "TYPE": "🏷️ **Type**",
        "SCORE": "⭐ **Score**",
        "FOOTER": "Tracking Temps Réel • L'escouade DO",
        "HELP_TITLE": "🎮 Aide - Free Games Bot",
        "LANG_CONFIRM": "✅ La langue de ce salon est : **Français**."
    },
    "en": {
        "NEW_GAME": "📢 **New game detected!**",
        "PLATFORM": "🎮 **Platform**",
        "TYPE": "🏷️ **Type**",
        "SCORE": "⭐ **Score**",
        "FOOTER": "Real-time Tracking • L'escouade DO",
        "HELP_TITLE": "🎮 Help - Free Games Bot",
        "LANG_CONFIRM": "✅ Language for this channel is: **English**."
    }
}

PLATFORM_COLORS = {
    "Epic Games": 0x2ECC71, "Steam": 0x1B2838, "GOG": 0xA12B2E,
    "PlayStation": 0x003791, "Xbox": 0x107C10, "Prime Gaming": 0xFF9900,
    "Ubisoft": 0x0070D1, "Nintendo eShop": 0xE60012, "Indiegala": 0x2C3E50
}

# ─────────────────────────────────────────────
# 🧠 GESTION DES FICHIERS (CORRIGÉE)
# ─────────────────────────────────────────────

def load_sent():
    if os.path.exists(SENT_GAMES_FILE):
        try:
            with open(SENT_GAMES_FILE, "r") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
        except: return set()
    return set()

def save_sent(sent_set):
    with open(SENT_GAMES_FILE, "w") as f:
        json.dump(list(sent_set), f)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

# ─────────────────────────────────────────────
# 🌍 LOGIQUE DE LANGUE
# ─────────────────────────────────────────────

def get_text(channel_id, key):
    settings = load_settings()
    lang = settings.get(str(channel_id), "both")
    if lang == "fr": return LOCALES["fr"][key]
    if lang == "en": return LOCALES["en"][key]
    return f"{LOCALES['fr'][key]} / {LOCALES['en'][key]}"

def build_embed(game, channel_id):
    title = str(game.get("title", "")).lower()
    if any(x in title for x in ["demo", "beta", "playtest", "trial"]): return None
    
    score = 1
    if game["platform"] in ["Epic Games", "GOG", "Nintendo eShop"]: score += 1
    if "edition" in title or "bundle" in title: score += 2
    
    embed = discord.Embed(
        title=f"🎁 {game['title']}",
        url=game["url"],
        color=PLATFORM_COLORS.get(game["platform"], 0x34495e),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name=get_text(channel_id, "PLATFORM"), value=game["platform"], inline=True)
    embed.add_field(name=get_text(channel_id, "TYPE"), value="🎁 Permanent", inline=True)
    embed.add_field(name=get_text(channel_id, "SCORE"), value='⭐' * min(score, 5), inline=True)
    embed.set_footer(text=get_text(channel_id, "FOOTER"))
    if game.get("image"): embed.set_image(url=game["image"])
    return embed

# ─────────────────────────────────────────────
# 🌐 FETCHERS
# ─────────────────────────────────────────────

async def fetch_games(session):
    games = []
    # Epic Games
    try:
        async with session.get("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=fr", timeout=10) as r:
            data = await r.json()
            elements = data["data"]["Catalog"]["searchStore"]["elements"]
            for el in elements:
                promos = el.get("promotions", {}).get("promotionalOffers", [])
                if promos and any(o["discountSetting"]["discountPercentage"] == 0 for o in promos[0]["promotionalOffers"]):
                    slug = el.get("productSlug") or el.get("urlSlug")
                    games.append({
                        "platform": "Epic Games", "title": el["title"],
                        "url": f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games",
                        "image": next((i["url"] for i in el.get("keyImages", []) if i["type"] in ["OfferImageWide", "Thumbnail"]), None)
                    })
    except: pass
    # GamerPower
    try:
        async with session.get("https://www.gamerpower.com/api/giveaways?type=game", timeout=10) as r:
            data = await r.json()
            for item in data:
                plat = item.get("platforms", "").lower()
                matched = "Nintendo eShop" if "switch" in plat or "nintendo" in plat else item.get("platforms", "Autre").split(",")[0].strip()
                games.append({"platform": matched, "title": item.get("title"), "url": item.get("open_giveaway_url"), "image": item.get("image")})
    except: pass
    return games

# ─────────────────────────────────────────────
# 🤖 BOT COMMANDS (SLASH CONVERSION)
# ─────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents, help_command=None)

async def run_scan(target_channel=None):
    channel = target_channel or bot.get_channel(CHANNEL_ID)
    if not channel: return
    
    sent = load_sent()
    async with aiohttp.ClientSession() as session:
        all_found = await fetch_games(session)
    
    new_found = False
    for g in all_found:
        key = g['title'].lower().strip()
        if key not in sent:
            embed = build_embed(g, channel.id)
            if embed:
                mention = "@everyone " if str(ROLE_ID).lower() == "everyone" else (f"<@&{ROLE_ID}> " if str(ROLE_ID).isdigit() else "")
                await channel.send(content=f"{mention}{get_text(channel.id, 'NEW_GAME')}", embed=embed)
                sent.add(key)
                new_found = True
                await asyncio.sleep(1)
    if new_found: save_sent(sent)

@tasks.loop(minutes=CHECK_INTERVAL)
async def scan_loop(): await run_scan()

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"🔗 Synchronisé {len(synced)} commandes Slash")
    except Exception as e:
        print(f"❌ Erreur de synchro : {e}")
    if not scan_loop.is_running(): scan_loop.start()

@bot.tree.command(name="lang", description="Change la langue du bot pour ce salon")
@app_commands.describe(choice="fr, en, or both")
@app_commands.checks.has_permissions(administrator=True)
async def lang(interaction: discord.Interaction, choice: str):
    choice = choice.lower()
    if choice not in ["fr", "en", "both"]:
        return await interaction.response.send_message("❌ Usage: `/lang fr` | `/lang en` | `/lang both`", ephemeral=True)
    
    settings = load_settings()
    settings[str(interaction.channel.id)] = choice
    save_settings(settings)
    
    confirm = "Mode bilingue activé !" if choice == "both" else LOCALES[choice]["LANG_CONFIRM"]
    await interaction.response.send_message(confirm)

@bot.tree.command(name="aide", description="Menu d'aide")
async def aide(interaction: discord.Interaction):
    embed = discord.Embed(title=get_text(interaction.channel.id, "HELP_TITLE"), color=0x3498DB)
    embed.add_field(name="/aide / /help", value="Menu d'aide / Help menu", inline=False)
    embed.add_field(name="/lang [fr/en/both]", value="*(Admin)* Change la langue / Change language", inline=False)
    embed.add_field(name="/check", value="*(Admin)* Scan manuel / Manual scan", inline=False)
    embed.add_field(name="/reset", value="*(Admin)* Reset l'historique / Clear history", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Help menu")
async def help(interaction: discord.Interaction):
    await aide(interaction)

@bot.tree.command(name="plateformes", description="Liste des boutiques surveillées")
async def plateformes(interaction: discord.Interaction):
    header = "🛰️ **Boutiques surveillées :**" if get_text(interaction.channel.id, "PLATFORM") == "🎮 **Plateforme**" else "🛰️ **Monitored Stores:**"
    liste = ", ".join(PLATFORM_COLORS.keys())
    await interaction.response.send_message(f"{header} {liste}")

@bot.tree.command(name="platforms", description="List of monitored stores")
async def platforms(interaction: discord.Interaction):
    await plateformes(interaction)

@bot.tree.command(name="check", description="Lancer un scan manuel des jeux")
@app_commands.checks.has_permissions(administrator=True)
async def check(interaction: discord.Interaction):
    await interaction.response.send_message("🔎 Scan...")
    await run_scan(interaction.channel)

@bot.tree.command(name="reset", description="Réinitialiser l'historique des jeux envoyés")
@app_commands.checks.has_permissions(administrator=True)
async def reset(interaction: discord.Interaction):
    if os.path.exists(SENT_GAMES_FILE):
        os.remove(SENT_GAMES_FILE)
        await interaction.response.send_message("✅ History cleared.")
    else:
        await interaction.response.send_message("❌ No history file found.", ephemeral=True)

bot.run(BOT_TOKEN)
