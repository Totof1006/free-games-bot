"""
🎮 Free Games Discord Bot — Version complète
Plateformes surveillées :
  Epic Games, Xbox Game Pass, PlayStation Plus, Steam, Blizzard/Battle.net,
  EA Play/Origin, Prime Gaming, GOG, Ubisoft Connect, Humble Bundle,
  itch.io, Fanatical, Rockstar Games, Microsoft Store, Indiegala
"""

import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
import re
import asyncio
from datetime import datetime

# ─────────────────────────────────────────────
# ⚙️  CONFIGURATION — Modifie ces valeurs
# ─────────────────────────────────────────────
import os
BOT_TOKEN  = os.environ.get("BOT_TOKEN")           # Token du bot Discord
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))     # ID du salon Discord où poster les jeux

SENT_GAMES_FILE      = "sent_games.json"
CHECK_INTERVAL_HOURS = 1
# ─────────────────────────────────────────────

# Couleurs par plateforme (hex)
PLATFORM_COLORS = {
    "Epic Games":        0x2ECC71,
    "Xbox Game Pass":    0x107C10,
    "PlayStation Plus":  0x003791,
    "Steam":             0x1B2838,
    "Blizzard":          0x148EFF,
    "EA Play":           0xFF4747,
    "Prime Gaming":      0xFF9900,
    "GOG":               0xA12B2E,
    "Ubisoft Connect":   0x0070D1,
    "Humble Bundle":     0xCC3300,
    "itch.io":           0xFA5C5C,
    "Fanatical":         0xE84B3A,
    "Rockstar Games":    0xFCB813,
    "Microsoft Store":   0x00A4EF,
    "Indiegala":         0x2C3E50,
}

# Emojis par plateforme
PLATFORM_EMOJIS = {
    "Epic Games":        "🟢",
    "Xbox Game Pass":    "🟩",
    "PlayStation Plus":  "🔵",
    "Steam":             "🖥️",
    "Blizzard":          "💙",
    "EA Play":           "🔴",
    "Prime Gaming":      "🟠",
    "GOG":               "🟤",
    "Ubisoft Connect":   "🔷",
    "Humble Bundle":     "📦",
    "itch.io":           "🎀",
    "Fanatical":         "🔥",
    "Rockstar Games":    "⭐",
    "Microsoft Store":   "🪟",
    "Indiegala":         "🎲",
}

# Mapping GamerPower -> nom affiché
GAMERPOWER_PLATFORM_MAP = {
    "steam":             "Steam",
    "epic-games-store":  "Epic Games",
    "xbox-game-pass":    "Xbox Game Pass",
    "ps4":               "PlayStation Plus",
    "ps5":               "PlayStation Plus",
    "battle.net":        "Blizzard",
    "ea-games":          "EA Play",
    "prime-gaming":      "Prime Gaming",
    "gog":               "GOG",
    "ubisoft":           "Ubisoft Connect",
    "itch":              "itch.io",
    "humble":            "Humble Bundle",
    "fanatical":         "Fanatical",
    "rockstar":          "Rockstar Games",
    "microsoft-store":   "Microsoft Store",
    "indiegala":         "Indiegala",
}


# ─────────────────────────────────────────────
# 📦  GESTION DE L'HISTORIQUE
# ─────────────────────────────────────────────
def load_sent_games() -> set:
    if os.path.exists(SENT_GAMES_FILE):
        with open(SENT_GAMES_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_games(sent: set):
    with open(SENT_GAMES_FILE, "w") as f:
        json.dump(list(sent), f)


# ─────────────────────────────────────────────
# 🌐  FETCHERS
# ─────────────────────────────────────────────

async def fetch_epic_games(session: aiohttp.ClientSession) -> list:
    """API officielle Epic Games — jeux 100% gratuits."""
    games = []
    url = (
        "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        "?locale=fr&country=FR&allowCountries=FR"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return games
            data = await r.json()
            elements = (
                data.get("data", {})
                    .get("Catalog", {})
                    .get("searchStore", {})
                    .get("elements", [])
            )
            for el in elements:
                promos = el.get("promotions") or {}
                for offer_group in promos.get("promotionalOffers", []):
                    for offer in offer_group.get("promotionalOffers", []):
                        if offer.get("discountSetting", {}).get("discountPercentage", -1) == 0:
                            title = el.get("title", "Inconnu")
                            slug  = el.get("catalogNs", {}).get("mappings", [{}])[0].get("pageSlug", "")
                            url_g = f"https://store.epicgames.com/fr/p/{slug}" if slug else "https://store.epicgames.com/fr/free-games"
                            img   = next((kv["url"] for kv in el.get("keyImages", []) if kv.get("type") == "OfferImageWide"), "")
                            end   = offer.get("endDate", "")
                            games.append({
                                "platform": "Epic Games",
                                "title":    title,
                                "url":      url_g,
                                "image":    img,
                                "end_date": end[:10] if end else "N/A",
                            })
    except Exception as e:
        print(f"[Epic Games] Erreur : {e}")
    return games


async def fetch_gamerpower_games(session: aiohttp.ClientSession) -> list:
    """
    GamerPower API gratuite — source principale multi-plateformes.
    Couvre : Steam, Xbox, PS, GOG, EA, Blizzard, Prime, Ubisoft,
             itch.io, Humble, Fanatical, Rockstar, MS Store, Indiegala…
    """
    games = []
    url = "https://www.gamerpower.com/api/giveaways?type=game"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return games
            data = await r.json(content_type=None)
            for item in data:
                raw = item.get("platforms", "").lower()
                matched = next(
                    (label for key, label in GAMERPOWER_PLATFORM_MAP.items() if key in raw),
                    "Autre"
                )
                games.append({
                    "platform":    matched,
                    "title":       item.get("title", "Inconnu"),
                    "url":         item.get("open_giveaway_url", item.get("giveaway_url", "")),
                    "image":       item.get("image", ""),
                    "end_date":    item.get("end_date", "N/A"),
                    "description": item.get("description", ""),
                })
    except Exception as e:
        print(f"[GamerPower] Erreur : {e}")
    return games


async def fetch_steam_free(session: aiohttp.ClientSession) -> list:
    """Steam Store — jeux temporairement gratuits (free weekends inclus)."""
    games = []
    url = (
        "https://store.steampowered.com/search/results/"
        "?query&start=0&count=15&maxprice=free&category1=998&hidef2p=1&infinite=1"
    )
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return games
            data   = await r.json(content_type=None)
            html   = data.get("results_html", "")
            titles = re.findall(r'class="title">(.*?)<', html)
            links  = re.findall(r'href="(https://store\.steampowered\.com/app/\d+/[^"]+)"', html)
            imgs   = re.findall(r'src="(https://cdn\.akamai\.steamstatic\.com/steam/apps/\d+/capsule_sm_120\.jpg)"', html)
            for i, title in enumerate(titles):
                games.append({
                    "platform": "Steam",
                    "title":    title.strip(),
                    "url":      links[i] if i < len(links) else "https://store.steampowered.com/",
                    "image":    imgs[i]  if i < len(imgs)  else "",
                    "end_date": "Limité",
                })
    except Exception as e:
        print(f"[Steam] Erreur : {e}")
    return games


async def fetch_indiegala(session: aiohttp.ClientSession) -> list:
    """Indiegala — jeux gratuits via leur API publique."""
    games = []
    url = "https://freebies.indiegala.com/api/pages/1"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return games
            data = await r.json(content_type=None)
            for item in data.get("freebies_list", []):
                games.append({
                    "platform":    "Indiegala",
                    "title":       item.get("prod_name", "Inconnu"),
                    "url":         item.get("link", "https://freebies.indiegala.com/"),
                    "image":       item.get("img", ""),
                    "end_date":    item.get("end_date", "N/A"),
                    "description": "Jeu gratuit sur Indiegala",
                })
    except Exception as e:
        print(f"[Indiegala] Erreur : {e}")
    return games


async def fetch_itchio_free(session: aiohttp.ClientSession) -> list:
    """itch.io — jeux gratuits mis en avant (top rated free games)."""
    games = []
    url = "https://itch.io/games/free/top-rated.json?format=json"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return games
            data = await r.json(content_type=None)
            for item in (data.get("games") or [])[:10]:
                if item.get("min_price", 1) == 0:
                    games.append({
                        "platform":    "itch.io",
                        "title":       item.get("title", "Inconnu"),
                        "url":         item.get("url", "https://itch.io/"),
                        "image":       item.get("cover_image", ""),
                        "end_date":    "Gratuit",
                        "description": item.get("short_text", ""),
                    })
    except Exception as e:
        print(f"[itch.io] Erreur : {e}")
    return games


async def fetch_all_free_games(session: aiohttp.ClientSession) -> list:
    """Agrège toutes les sources en parallèle, déduplique par (plateforme + titre)."""
    fetchers = await asyncio.gather(
        fetch_epic_games(session),
        fetch_gamerpower_games(session),   # Steam, Xbox, PS, GOG, EA, Blizzard, Prime, Ubisoft, Humble, Fanatical, Rockstar, MS Store…
        fetch_steam_free(session),
        fetch_indiegala(session),
        fetch_itchio_free(session),
        return_exceptions=True,
    )

    results = []
    for res in fetchers:
        if isinstance(res, list):
            results.extend(res)

    # Déduplication
    seen, unique = set(), []
    for g in results:
        key = f"{g['platform']}_{g['title']}".lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(g)

    return unique


# ─────────────────────────────────────────────
# 🤖  BOT DISCORD
# ─────────────────────────────────────────────
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="?", intents=intents)


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
    if game.get("image"):
        embed.set_thumbnail(url=game["image"])
    embed.set_footer(text="Free Games Bot • 15 plateformes surveillées")
    return embed


@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user} (ID: {bot.user.id})")
    print(f"📡 Surveillance de 15 plateformes — vérification toutes les {CHECK_INTERVAL_HOURS}h")
    check_free_games.start()


@tasks.loop(hours=CHECK_INTERVAL_HOURS)
async def check_free_games():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"❌ Salon introuvable (ID: {CHANNEL_ID})")
        return

    sent_games = load_sent_games()
    new_count  = 0

    async with aiohttp.ClientSession() as session:
        games = await fetch_all_free_games(session)

    for game in games:
        key = f"{game['platform']}_{game['title']}".lower().replace(" ", "_")
        if key in sent_games:
            continue
        try:
            await channel.send(embed=build_embed(game))
            sent_games.add(key)
            new_count += 1
            await asyncio.sleep(1)
        except Exception as e:
            print(f"[Discord] Erreur envoi '{game['title']}' : {e}")

    save_sent_games(sent_games)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ {new_count} nouveau(x) jeu(x) envoyé(s)")


# ─────────────────────────────────────────────
# 🕹️  COMMANDES
# ─────────────────────────────────────────────

@bot.command(name="freegames", aliases=["jeux", "free"])
async def cmd_freegames(ctx):
    """!freegames — Affiche tous les jeux gratuits actuels."""
    await ctx.send("🔍 Recherche en cours sur **15 plateformes**...")
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_free_games(session)

    if not games:
        await ctx.send("😔 Aucun jeu gratuit trouvé pour le moment.")
        return

    await ctx.send(f"🎮 **{len(games)} jeu(x) gratuit(s) trouvé(s) !**")
    for game in games[:15]:
        await ctx.send(embed=build_embed(game))
        await asyncio.sleep(0.5)


@bot.command(name="plateforme", aliases=["platform"])
async def cmd_platform(ctx, *, platform_name: str):
    """!plateforme <nom> — Filtre les jeux par plateforme. Ex: !plateforme Steam"""
    await ctx.send(f"🔍 Recherche pour **{platform_name}**...")
    async with aiohttp.ClientSession() as session:
        games = await fetch_all_free_games(session)

    filtered = [g for g in games if platform_name.lower() in g["platform"].lower()]
    if not filtered:
        await ctx.send(f"😔 Aucun jeu gratuit trouvé pour **{platform_name}** en ce moment.")
        return

    await ctx.send(f"🎮 **{len(filtered)} jeu(x) trouvé(s) sur {platform_name} :**")
    for game in filtered[:10]:
        await ctx.send(embed=build_embed(game))
        await asyncio.sleep(0.5)


@bot.command(name="plateformes", aliases=["liste"])
async def cmd_list_platforms(ctx):
    """!plateformes — Liste toutes les plateformes surveillées."""
    lines = [f"{emoji} **{name}**" for name, emoji in PLATFORM_EMOJIS.items()]
    embed = discord.Embed(
        title       = "📋 Plateformes surveillées",
        description = "\n".join(lines),
        color       = 0x9B59B6,
    )
    embed.set_footer(text=f"Free Games Bot • {len(PLATFORM_EMOJIS)} plateformes actives")
    await ctx.send(embed=embed)


@bot.command(name="check")
@commands.has_permissions(administrator=True)
async def cmd_check(ctx):
    """!check — Force une vérification immédiate (Admin)."""
    await ctx.send("🔄 Vérification forcée en cours...")
    await check_free_games()
    await ctx.send("✅ Vérification terminée !")


@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def cmd_reset(ctx):
    """!reset — Réinitialise l'historique des jeux envoyés (Admin)."""
    if os.path.exists(SENT_GAMES_FILE):
        os.remove(SENT_GAMES_FILE)
    await ctx.send("🗑️ Historique réinitialisé. Tous les jeux seront renvoyés au prochain cycle.")


@bot.command(name="aide", aliases=["help_bot"])
async def cmd_help(ctx):
    """!aide — Affiche l'aide complète du bot."""
    embed = discord.Embed(
        title       = "🎮 Free Games Bot — Aide",
        color       = 0x9B59B6,
        description = "Je surveille les jeux gratuits sur **15 plateformes** en temps réel !",
    )
    embed.add_field(
        name  = "🕹️ Commandes",
        value = (
            "`!freegames` — Afficher tous les jeux gratuits actuels\n"
            "`!plateforme <nom>` — Filtrer par plateforme *(ex: `!plateforme Steam`)*\n"
            "`!plateformes` — Lister les 15 plateformes surveillées\n"
            "`!check` — Forcer une vérification *(Admin)*\n"
            "`!reset` — Réinitialiser l'historique *(Admin)*"
        ),
        inline=False,
    )
    embed.add_field(
        name  = "⏰ Automatique",
        value = f"Vérification automatique toutes les **{CHECK_INTERVAL_HOURS}h**",
        inline=False,
    )
    embed.set_footer(text="Free Games Bot • 15 plateformes surveillées")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
# 🚀  LANCEMENT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
