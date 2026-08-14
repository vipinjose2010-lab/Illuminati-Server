import os
import asyncio
from typing import Iterable, Optional
from datetime import datetime

import discord
from discord import (
    Color,
    CategoryChannel,
    PermissionOverwrite,
    Role,
    TextChannel,
    VoiceChannel,
)
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
AUTO_BUILD = os.getenv("AUTO_BUILD", "true").lower() == "true"

# ─── Role Hierarchy (highest to lowest) ───
RANK_ORDER = ["Boss", "Underboss", "Capo", "Enforcer", "Associate", "Newbie", "Applicant"]

# ─── Colors for each rank ───
RANK_COLORS = {
    "Boss": Color.from_rgb(212, 175, 55),      # Gold
    "Underboss": Color.from_rgb(192, 192, 192), # Silver
    "Capo": Color.from_rgb(255, 69, 0),         # Red-Orange
    "Enforcer": Color.from_rgb(0, 100, 0),      # Dark Green
    "Associate": Color.from_rgb(70, 130, 180),  # Steel Blue
    "Newbie": Color.from_rgb(128, 128, 128),    # Gray
    "Applicant": Color.from_rgb(105, 105, 105), # Dim Gray
}

# ─── Helper: get numerical "tier" for a rank name ───
def rank_tier(name: str) -> int:
    try:
        return RANK_ORDER.index(name)
    except ValueError:
        return len(RANK_ORDER)  # unrecognised → below everyone

# ─── Category definitions ───
CATEGORY_DEFS = [
    {"name": "📜 COMMAND CENTER", "position": 0, "min_tier": 999},
    {"name": "📁 ARCHIVES", "position": 1, "min_tier": 999},
]

# The main "INFORMATION" category (everyone can read, no one writes)
INFO_CATEGORY = "📋 INFORMATION"
INFO_CHANNELS = [
    "📜-server-rules",
    "📜-whitelines",
    "📜-announcements",
    "📜-server-updates",
    "📜-rank-requirements",
    "📜-frequently-asked",
    "📜-gang-information",
]

# Rank-specific categories
RANK_CATEGORIES = {
    "Applicant": {
        "name": "🟦 APPLICANT ZONE",
        "position": 2,
        "channels": {"text": ["💬-lobby", "📸-introductions"], "voice": ["🎤-waiting-room"]},
    },
    "Newbie": {
        "name": "🟩 NEWBIE HUB",
        "position": 3,
        "channels": {"text": ["💬-general", "📸-screenshots"], "voice": ["🎤-lounge"]},
    },
    "Associate": {
        "name": "🟦 ASSOCIATE ZONE",
        "position": 4,
        "channels": {"text": ["💬-talk", "📸-media", "📋-scores"], "voice": ["🎤-associate-vc"]},
    },
    "Enforcer": {
        "name": "🟩 ENFORCER ZONE",
        "position": 5,
        "channels": {"text": ["💬-operations", "📸-evidence", "📋-reports"], "voice": ["🎤-enforcer-vc"]},
    },
    "Capo": {
        "name": "🟥 CAPO ZONE",
        "position": 6,
        "channels": {"text": ["💬-strategies", "📋-missions", "📸-captures"], "voice": ["🎤-capo-vc"]},
    },
    "Underboss": {
        "name": "🟪 UNDERBOSS ZONE",
        "position": 7,
        "channels": {"text": ["💬-command", "📋-directives", "📸-intel"], "voice": ["🎤-underboss-vc"]},
    },
    "Boss": {
        "name": "⬛ BOSS ZONE",
        "position": 8,
        "channels": {"text": ["💬-throne", "📋-orders", "📸-secrets"], "voice": ["🎤-boss-vc"]},
    },
}

# ─── Pin channel map ───
PIN_CHANNELS = {
    "📜-server-rules": ["📜-announcements", "📜-whitelines"],
    "📜-announcements": ["📜-server-rules", "📜-whitelines"],
}

# ─── Bot setup ───
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ─────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────

def safe_send(channel, content=None, **kwargs):
    """Send a message, safely ignoring ephemeral / deleted channels."""
    if channel and not channel.permissions_for(channel.guild.me).send_messages:
        return None
    try:
        return channel.send(content=content, **kwargs)
    except discord.Forbidden:
        return None
    except discord.HTTPException:
        return None

async def resolve_rank_role(guild: discord.Guild, rank_name: str) -> Optional[Role]:
    """Find the rank role by name (case-insensitive)."""
    rank = discord.utils.get(guild.roles, name=rank_name)
    if rank:
        return rank
    # fallback: create it
    color = RANK_COLORS.get(rank_name, Color.default())
    return await guild.create_role(name=rank_name, color=color, mentionable=True)

def everyone_overwrites(view: bool = False, send: bool = False) -> PermissionOverwrite:
    return PermissionOverwrite(view_channel=view, send_messages=send, read_message_history=view)

def role_overwrites(role: Role, view: bool = True, send: bool = False) -> PermissionOverwrite:
    return PermissionOverwrite(view_channel=view, send_messages=send, read_message_history=view)

def build_overwrites(
    guild: discord.Guild,
    min_tier: int,
    extra_roles: Optional[list] = None,
    extra_send: bool = False,
) -> dict:
    """Build channel overwrites: everyone denied; roles at or above min_tier can view/send."""
    overwrites = {}
    overwrites[guild.default_role] = everyone_overwrites(False, False)

    allowed_roles = [r for r in guild.roles if r.name in RANK_ORDER and rank_tier(r.name) <= min_tier]
    for r in allowed_roles:
        overwrites[r] = role_overwrites(r, view=True, send=True)

    if extra_roles:
        for r in extra_roles:
            overwrites[r] = role_overwrites(r, view=True, send=extra_send)

    return overwrites

async def ensure_category(guild: discord.Guild, name: str, position: int, min_tier: int = 999) -> CategoryChannel:
    existing = discord.utils.get(guild.categories, name=name)
    if existing:
        return existing
    overwrites = build_overwrites(guild, min_tier)
    return await guild.create_category(name=name, position=position, overwrites=overwrites)

async def ensure_text_channel(
    guild: discord.Guild,
    name: str,
    category: CategoryChannel,
    min_tier: int = 999,
    topic: str = "",
    extra_roles: Optional[list] = None,
    extra_send: bool = False,
) -> TextChannel:
    existing = discord.utils.get(guild.text_channels, name=name)
    if existing:
        return existing
    overwrites = build_overwrites(guild, min_tier, extra_roles, extra_send)
    return await guild.create_text_channel(name=name, category=category, topic=topic, overwrites=overwrites)

async def ensure_voice_channel(
    guild: discord.Guild,
    name: str,
    category: CategoryChannel,
    min_tier: int = 999,
) -> VoiceChannel:
    existing = discord.utils.get(guild.voice_channels, name=name)
    if existing:
        return existing
    overwrites = build_overwrites(guild, min_tier)
    return await guild.create_voice_channel(name=name, category=category, overwrites=overwrites)

# ─────────────────────────────────────────────────────
# EVENTS
# ─────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"   Connected to {len(bot.guilds)} guild(s)")

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("❌ Guild not found – check GUILD_ID in .env")
        return

    # ── Sync role hierarchy ──
    print(f"🏁 Syncing roles for {guild.name}...")
    for rank_name in RANK_ORDER:
        role = discord.utils.get(guild.roles, name=rank_name)
        if not role:
            color = RANK_COLORS.get(rank_name, Color.default())
            role = await guild.create_role(name=rank_name, color=color, mentionable=True)
            print(f"   ➕ Created role: {rank_name}")
        else:
            print(f"   ✅ Found role: {rank_name}")

    # Reorder roles to match RANK_ORDER
    role_objects = [discord.utils.get(guild.roles, name=r) for r in RANK_ORDER if discord.utils.get(guild.roles, name=r)]
    if role_objects:
        await guild.edit_role_positions(positions=role_objects)
        print("   🔄 Role positions synced")

    if not AUTO_BUILD:
        print("⏸️  AUTO_BUILD is false – skipping channel creation")
        return

    # ── Create categories & channels ──
    print("🏗️  Building channel structure...")

    # Command Center & Archives
    for cat_def in CATEGORY_DEFS:
        await ensure_category(guild, cat_def["name"], cat_def["position"], cat_def["min_tier"])

    # Information category
    info_cat = await ensure_category(guild, INFO_CATEGORY, 10, 999)
    for ch_name in INFO_CHANNELS:
        await ensure_text_channel(guild, ch_name, info_cat, min_tier=999)

    # Rank-specific categories
    for rank_name, rank_def in RANK_CATEGORIES.items():
        min_tier = rank_tier(rank_name)
        cat = await ensure_category(guild, rank_def["name"], rank_def["position"], min_tier)
        for ch_name in rank_def["channels"]["text"]:
            await ensure_text_channel(guild, ch_name, cat, min_tier)
        for vc_name in rank_def["channels"]["voice"]:
            await ensure_voice_channel(guild, vc_name, cat, min_tier)

    print("✅ Build complete — all channels verified")
    print(f"🌐 {guild.name} is ready for One State RP | ILLUMINATI - Madera")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── Pin system ──
    if message.channel.name in PIN_CHANNELS:
        for target_name in PIN_CHANNELS[message.channel.name]:
            target = discord.utils.get(message.guild.text_channels, name=target_name)
            if target and target.permissions_for(message.guild.me).send_messages:
                try:
                    await target.send(content=message.content)
                except discord.HTTPException:
                    pass

    await bot.process_commands(message)


@bot.event
async def on_member_join(member: discord.Member):
    """Assign 'Applicant' role to new members."""
    applicant = discord.utils.get(member.guild.roles, name="Applicant")
    if applicant:
        try:
            await member.add_roles(applicant)
            print(f"👤 Assigned 'Applicant' to {member.display_name}")
        except discord.Forbidden:
            print(f"⚠️ Could not assign role to {member.display_name}")

# ─────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────

@bot.command(name="rank")
async def set_rank(ctx, member: discord.Member = None, *, rank_name: str = None):
    """Promote/demote a member to a rank. Usage: !rank @User RankName"""
    if ctx.author == bot.user:
        return

    # Permission check: only Boss or Underboss can rank others
    author_roles = [r.name for r in ctx.author.roles]
    if not any(r in ["Boss", "Underboss"] for r in author_roles):
        await safe_send(ctx.channel, "❌ Only Boss or Underboss can assign ranks.")
        return

    if not member or not rank_name:
        await safe_send(ctx.channel, "Usage: `!rank @User RankName`\nRanks: " + ", ".join(RANK_ORDER))
        return

    rank_name = rank_name.strip().title()
    if rank_name not in RANK_ORDER:
        await safe_send(ctx.channel, f"❌ Invalid rank. Valid ranks: {', '.join(RANK_ORDER)}")
        return

    target_role = await resolve_rank_role(ctx.guild, rank_name)
    if not target_role:
        await safe_send(ctx.channel, "❌ Could not find or create that rank role.")
        return

    # Remove all other rank roles
    for rank in RANK_ORDER:
        role = discord.utils.get(ctx.guild.roles, name=rank)
        if role and role in member.roles and role != target_role:
            await member.remove_roles(role)

    # Add target rank
    await member.add_roles(target_role)

    embed = discord.Embed(
        title="🆙 Rank Update",
        description=f"{member.mention} has been promoted to **{rank_name}**",
        color=RANK_COLORS.get(rank_name, Color.green()),
        timestamp=datetime.utcnow(),
    )
    embed.set_footer(text=f"By {ctx.author.display_name}")
    await safe_send(ctx.channel, embed=embed)


@bot.command(name="ranks")
async def list_ranks(ctx):
    """List all ranks in order."""
    embed = discord.Embed(
        title="🏆 Illuminati Ranks",
        description="\n".join(f"{i+1}. **{r}**" for i, r in enumerate(RANK_ORDER)),
        color=Color.gold(),
    )
    await safe_send(ctx.channel, embed=embed)


@bot.command(name="reload")
@commands.is_owner()
async def reload_build(ctx):
    """Force rebuild of all categories and channels (owner only)."""
    guild = ctx.guild
    for cat_def in CATEGORY_DEFS:
        await ensure_category(guild, cat_def["name"], cat_def["position"], cat_def["min_tier"])
    info_cat = await ensure_category(guild, INFO_CATEGORY, 10, 999)
    for ch_name in INFO_CHANNELS:
        await ensure_text_channel(guild, ch_name, info_cat, min_tier=999)
    for rank_name, rank_def in RANK_CATEGORIES.items():
        min_tier = rank_tier(rank_name)
        cat = await ensure_category(guild, rank_def["name"], rank_def["position"], min_tier)
        for ch_name in rank_def["channels"]["text"]:
            await ensure_text_channel(guild, ch_name, cat, min_tier)
        for vc_name in rank_def["channels"]["voice"]:
            await ensure_voice_channel(guild, vc_name, cat, min_tier)
    await safe_send(ctx.channel, "✅ Full rebuild complete!")


# ─────────────────────────────────────────────────────
# START
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN is not set in .env!")
    bot.run(TOKEN)
