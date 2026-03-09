import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
import random
import string
import io
from datetime import datetime
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFilter

# ============ CONFIG ============
TOKEN = os.getenv("DISCORD_TOKEN")
LOG_CHANNEL_NAME = "corsu-logs"
WELCOME_CHANNEL_NAME = "welcome"
AUTO_ROLE_NAME = ""  # Role to auto assign on join, leave empty to disable
# ================================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree

# Storage files
WARNS_FILE = "warns.json"
XP_FILE = "xp.json"
CUSTOM_COMMANDS_FILE = "custom_commands.json"
SETTINGS_FILE = "settings.json"
REACTION_ROLES_FILE = "reaction_roles.json"

# Anti-raid
join_tracker = defaultdict(list)
RAID_JOIN_THRESHOLD = 20
RAID_TIME_WINDOW = 10
raid_mode = {}

# Spam tracking
message_tracker = defaultdict(list)
SPAM_THRESHOLD = 5
SPAM_TIME_WINDOW = 5

# Nuke protection (on by default)
channel_delete_tracker = defaultdict(list)
NUKE_THRESHOLD = 3
NUKE_TIME_WINDOW = 10

# Captcha tracking
captcha_codes = {}
captcha_attempts = defaultdict(int)

# ============ HELPERS ============

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            return json.load(f)
    return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

def get_settings(guild_id):
    settings = load_json(SETTINGS_FILE)
    return settings.get(str(guild_id), {})

def save_settings(guild_id, data):
    settings = load_json(SETTINGS_FILE)
    settings[str(guild_id)] = data
    save_json(SETTINGS_FILE, settings)

async def log(guild, message):
    channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if channel:
        embed = discord.Embed(description=message, color=0xff4444, timestamp=datetime.utcnow())
        await channel.send(embed=embed)

PERMS_FILE = "perms.json"

def is_admin(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id

def has_perm(interaction: discord.Interaction, command: str):
    # Owner and admins always pass
    if interaction.user.id == interaction.guild.owner_id:
        return True
    if interaction.user.guild_permissions.administrator:
        return True
    # Check granted perms
    perms = load_json(PERMS_FILE)
    guild_id = str(interaction.guild.id)
    guild_perms = perms.get(guild_id, {})
    # Check user perms
    user_perms = guild_perms.get("users", {}).get(str(interaction.user.id), [])
    if command in user_perms:
        return True
    # Check role perms
    role_perms = guild_perms.get("roles", {})
    for role in interaction.user.roles:
        if command in role_perms.get(str(role.id), []):
            return True
    return False

def generate_captcha():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    img = Image.new('RGB', (200, 80), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    for _ in range(8):
        x1, y1 = random.randint(0, 200), random.randint(0, 80)
        x2, y2 = random.randint(0, 200), random.randint(0, 80)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(80, 180), random.randint(80, 180), random.randint(80, 180)), width=1)
    for _ in range(300):
        x, y = random.randint(0, 200), random.randint(0, 80)
        draw.point((x, y), fill=(random.randint(100, 200), random.randint(100, 200), random.randint(100, 200)))
    x_pos = 15
    for char in code:
        color = (random.randint(200, 255), random.randint(200, 255), random.randint(200, 255))
        y_offset = random.randint(-5, 5)
        draw.text((x_pos, 20 + y_offset), char, fill=color)
        x_pos += 28
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return code, buf

# ============ EVENTS ============

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Corsu is online as {bot.user}")
    await bot.change_presence(activity=discord.Game(name="/help | Corsu Bot"))

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    now = datetime.utcnow()
    settings = get_settings(guild.id)
    # Nuke protection is ON by default
    if settings.get("nuke_protection", True):
        channel_delete_tracker[guild.id].append(now)
        channel_delete_tracker[guild.id] = [t for t in channel_delete_tracker[guild.id] if (now - t).seconds < NUKE_TIME_WINDOW]
        if len(channel_delete_tracker[guild.id]) >= NUKE_THRESHOLD:
            try:
                async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
                    culprit = entry.user
                    if culprit and culprit != guild.me and not culprit.guild_permissions.administrator:
                        await culprit.ban(reason="Nuke protection: Mass channel deletion detected")
                        await log(guild, f"NUKE ATTEMPT — {culprit} permanently banned for deleting {len(channel_delete_tracker[guild.id])} channels in {NUKE_TIME_WINDOW}s.")
            except:
                pass

@bot.event
async def on_member_join(member):
    guild = member.guild
    now = datetime.utcnow()

    join_tracker[guild.id].append(now)
    join_tracker[guild.id] = [t for t in join_tracker[guild.id] if (now - t).seconds < RAID_TIME_WINDOW]

    if len(join_tracker[guild.id]) >= RAID_JOIN_THRESHOLD:
        if not raid_mode.get(guild.id):
            raid_mode[guild.id] = True
            await log(guild, f"RAID DETECTED — {len(join_tracker[guild.id])} joins in {RAID_TIME_WINDOW}s. Raid mode enabled.")
            for ch in guild.text_channels:
                try:
                    await ch.edit(slowmode_delay=30)
                except:
                    pass
            await log(guild, "Applied 30 second slowmode to all channels during raid.")
        try:
            await member.kick(reason="Anti-raid: Mass join detected")
            await log(guild, f"Kicked {member} during raid mode.")
        except:
            pass
        return

    welcome_channel = discord.utils.get(guild.text_channels, name=WELCOME_CHANNEL_NAME)
    if welcome_channel:
        embed = discord.Embed(
            title=f"Welcome to {guild.name}!",
            description=f"Hey {member.mention}, glad to have you here. You are member #{guild.member_count}.",
            color=0x5865F2
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await welcome_channel.send(embed=embed)

    if AUTO_ROLE_NAME:
        role = discord.utils.get(guild.roles, name=AUTO_ROLE_NAME)
        if role:
            try:
                await member.add_roles(role)
            except:
                pass

@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    # Handle captcha DM responses
    if isinstance(message.channel, discord.DMChannel):
        user_id = str(message.author.id)
        if user_id in captcha_codes:
            if message.content.upper().strip() == captcha_codes[user_id]:
                del captcha_codes[user_id]
                captcha_attempts.pop(user_id, None)
                for guild in bot.guilds:
                    member = guild.get_member(message.author.id)
                    if member:
                        settings = get_settings(guild.id)
                        verified_role_id = settings.get("verified_role")
                        if verified_role_id:
                            verified_role = guild.get_role(int(verified_role_id))
                            if verified_role:
                                try:
                                    await member.add_roles(verified_role)
                                    await message.author.send("You have been verified! You now have access to the server.")
                                except:
                                    pass
            else:
                captcha_attempts[user_id] += 1
                remaining = 3 - captcha_attempts[user_id]
                if remaining <= 0:
                    del captcha_codes[user_id]
                    captcha_attempts.pop(user_id, None)
                    await message.author.send("Too many wrong attempts. Please use `/verify` again in the server.")
                    for guild in bot.guilds:
                        member = guild.get_member(message.author.id)
                        if member:
                            try:
                                await member.kick(reason="Failed captcha verification 3 times")
                            except:
                                pass
                else:
                    await message.author.send(f"Wrong code. {remaining} attempt(s) remaining.")
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    user_id = str(message.author.id)
    guild_id = str(message.guild.id)
    now = datetime.utcnow()
    settings = get_settings(message.guild.id)

    # Spam detection (on by default, skip admins)
    antispam_enabled = settings.get("antispam", True)
    if antispam_enabled and not message.author.guild_permissions.administrator:
        message_tracker[user_id].append(now)
        message_tracker[user_id] = [t for t in message_tracker[user_id] if (now - t).seconds < SPAM_TIME_WINDOW]
        if len(message_tracker[user_id]) >= SPAM_THRESHOLD:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Slow down, you are spamming.", delete_after=5)
                await log(message.guild, f"Spam detected from {message.author} in {message.channel.mention}")
            except:
                pass
            return

    # Caps filter (skip admins)
    if not message.author.guild_permissions.administrator and len(message.content) > 10:
        caps_ratio = sum(1 for c in message.content if c.isupper()) / len(message.content)
        if caps_ratio > 0.7:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Please do not use excessive caps.", delete_after=5)
            except:
                pass
            return

    # Invite filter
    if settings.get("invite_filter") and "discord.gg/" in message.content:
        if not message.author.guild_permissions.administrator:
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Discord invite links are not allowed here.", delete_after=5)
                await log(message.guild, f"Invite link blocked from {message.author} in {message.channel.mention}")
            except:
                pass
            return

    # Word blacklist + built-in family filter
    blacklist = settings.get("blacklist", [])
    BUILTIN_FILTER = [
        "nigger", "nigga", "faggot", "fag", "retard", "tranny",
        "chink", "spic", "kike", "cunt", "whore", "slut"
    ]
    if settings.get("family_filter"):
        blacklist = list(set(blacklist + BUILTIN_FILTER))
    msg_lower = message.content.lower()
    for word in blacklist:
        if word.lower() in msg_lower:
            try:
                await message.delete()
                await message.author.send(f"Your message in **{message.guild.name}** was removed for containing a blocked word.")
                await log(message.guild, f"Blocked word used by {message.author} in {message.channel.mention} — deleted silently")
            except:
                pass
            return

    # XP system
    xp_data = load_json(XP_FILE)
    if guild_id not in xp_data:
        xp_data[guild_id] = {}
    if user_id not in xp_data[guild_id]:
        xp_data[guild_id][user_id] = {"xp": 0, "level": 1}
    xp_data[guild_id][user_id]["xp"] += random.randint(5, 15)
    xp = xp_data[guild_id][user_id]["xp"]
    level = xp_data[guild_id][user_id]["level"]
    if xp >= level * 100:
        new_level = level + 1
        xp_data[guild_id][user_id]["level"] = new_level
        xp_data[guild_id][user_id]["xp"] = 0
        await message.channel.send(f"{message.author.mention} You reached level {new_level}!", delete_after=10)
        role_rewards = settings.get("role_rewards", {})
        if str(new_level) in role_rewards:
            reward_role = message.guild.get_role(int(role_rewards[str(new_level)]))
            if reward_role:
                try:
                    await message.author.add_roles(reward_role)
                    await message.channel.send(f"{message.author.mention} You earned the **{reward_role.name}** role!", delete_after=10)
                except:
                    pass
    save_json(XP_FILE, xp_data)

    # Custom commands
    custom_cmds = load_json(CUSTOM_COMMANDS_FILE)
    content = message.content.strip()
    if guild_id in custom_cmds and content in custom_cmds[guild_id]:
        await message.channel.send(custom_cmds[guild_id][content])
        return

    await bot.process_commands(message)

@bot.event
async def on_raw_reaction_add(payload):
    rr_data = load_json(REACTION_ROLES_FILE)
    guild_id = str(payload.guild_id)
    msg_id = str(payload.message_id)
    emoji = str(payload.emoji)
    if guild_id in rr_data and msg_id in rr_data[guild_id]:
        role_id = rr_data[guild_id][msg_id].get(emoji)
        if role_id:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role = guild.get_role(int(role_id))
            if role and member and not member.bot:
                await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload):
    rr_data = load_json(REACTION_ROLES_FILE)
    guild_id = str(payload.guild_id)
    msg_id = str(payload.message_id)
    emoji = str(payload.emoji)
    if guild_id in rr_data and msg_id in rr_data[guild_id]:
        role_id = rr_data[guild_id][msg_id].get(emoji)
        if role_id:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role = guild.get_role(int(role_id))
            if role and member and not member.bot:
                await member.remove_roles(role)

# ============ MODERATION ============

@tree.command(name="ban", description="Ban a member from the server")
@app_commands.describe(member="Member to ban", reason="Reason for ban")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_perm(interaction, "ban"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await member.ban(reason=reason)
    embed = discord.Embed(title="Banned", description=f"{member} has been banned.\n**Reason:** {reason}", color=0xff4444)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} banned {member} — {reason}")

@tree.command(name="kick", description="Kick a member from the server")
@app_commands.describe(member="Member to kick", reason="Reason for kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_perm(interaction, "kick"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await member.kick(reason=reason)
    embed = discord.Embed(title="Kicked", description=f"{member} has been kicked.\n**Reason:** {reason}", color=0xff8800)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} kicked {member} — {reason}")

@tree.command(name="mute", description="Mute a member")
@app_commands.describe(member="Member to mute", reason="Reason for mute")
async def mute(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_perm(interaction, "mute"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await interaction.guild.create_role(name="Muted")
        for channel in interaction.guild.channels:
            await channel.set_permissions(muted_role, send_messages=False, speak=False)
    await member.add_roles(muted_role, reason=reason)
    embed = discord.Embed(title="Muted", description=f"{member} has been muted.\n**Reason:** {reason}", color=0xffcc00)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} muted {member} — {reason}")

@tree.command(name="unmute", description="Unmute a member")
@app_commands.describe(member="Member to unmute")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not has_perm(interaction, "unmute"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    muted_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        await member.remove_roles(muted_role)
        embed = discord.Embed(title="Unmuted", description=f"{member} has been unmuted.", color=0x5865F2)
        await interaction.response.send_message(embed=embed)
        await log(interaction.guild, f"{interaction.user} unmuted {member}")
    else:
        await interaction.response.send_message(f"{member} is not muted.", ephemeral=True)

@tree.command(name="tempban", description="Temporarily ban a member")
@app_commands.describe(member="Member to tempban", minutes="Duration in minutes", reason="Reason")
async def tempban(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason provided"):
    if not has_perm(interaction, "tempban"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await member.ban(reason=reason)
    embed = discord.Embed(title="Temp Banned", description=f"{member} banned for {minutes} minutes.\n**Reason:** {reason}", color=0xff4444)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} tempbanned {member} for {minutes}min — {reason}")
    await asyncio.sleep(minutes * 60)
    await interaction.guild.unban(member)
    await log(interaction.guild, f"Tempban expired for {member}")

@tree.command(name="warn", description="Warn a member")
@app_commands.describe(member="Member to warn", reason="Reason for warn")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not has_perm(interaction, "warn"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    warns = load_json(WARNS_FILE)
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    if guild_id not in warns:
        warns[guild_id] = {}
    if user_id not in warns[guild_id]:
        warns[guild_id][user_id] = []
    warns[guild_id][user_id].append({"reason": reason, "by": str(interaction.user), "time": str(datetime.utcnow())})
    save_json(WARNS_FILE, warns)
    count = len(warns[guild_id][user_id])
    embed = discord.Embed(title="Warning Issued", description=f"{member} has been warned.\n**Reason:** {reason}\n**Total warns:** {count}", color=0xffcc00)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} warned {member} ({count} total) — {reason}")

@tree.command(name="warns", description="View warns for a member")
@app_commands.describe(member="Member to check")
async def warns(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    warns_data = load_json(WARNS_FILE)
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    user_warns = warns_data.get(guild_id, {}).get(user_id, [])
    if not user_warns:
        await interaction.response.send_message(f"{member} has no warnings.", ephemeral=True)
        return
    desc = "\n".join([f"{i+1}. {w['reason']} — by {w['by']}" for i, w in enumerate(user_warns)])
    embed = discord.Embed(title=f"Warnings for {member}", description=desc, color=0xffcc00)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="clearwarns", description="Clear all warns for a member")
@app_commands.describe(member="Member to clear warns for")
async def clearwarns(interaction: discord.Interaction, member: discord.Member):
    if not has_perm(interaction, "clearwarns"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    warns_data = load_json(WARNS_FILE)
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    if guild_id in warns_data and user_id in warns_data[guild_id]:
        warns_data[guild_id][user_id] = []
        save_json(WARNS_FILE, warns_data)
    await interaction.response.send_message(f"Warnings cleared for {member}.", ephemeral=True)

@tree.command(name="purge", description="Delete messages in bulk")
@app_commands.describe(amount="Number of messages to delete")
async def purge(interaction: discord.Interaction, amount: int = 10):
    if not has_perm(interaction, "purge"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"Deleted {amount} messages.", ephemeral=True)
    await log(interaction.guild, f"{interaction.user} purged {amount} messages in {interaction.channel.mention}")

@tree.command(name="slowmode", description="Set slowmode in a channel")
@app_commands.describe(seconds="Slowmode delay in seconds, 0 to disable")
async def slowmode(interaction: discord.Interaction, seconds: int = 0):
    if not has_perm(interaction, "slowmode"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await interaction.channel.edit(slowmode_delay=seconds)
    if seconds == 0:
        await interaction.response.send_message("Slowmode disabled.")
    else:
        await interaction.response.send_message(f"Slowmode set to {seconds} seconds.")

@tree.command(name="lockdown", description="Lock a channel so only admins can send messages")
@app_commands.describe(channel="Channel to lock, leave empty for current channel")
async def lockdown(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not has_perm(interaction, "lockdown"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = discord.Embed(title="Channel Locked", description=f"{channel.mention} has been locked down.", color=0xff4444)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} locked {channel.mention}")

@tree.command(name="unlock", description="Unlock a locked channel")
@app_commands.describe(channel="Channel to unlock, leave empty for current channel")
async def unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not has_perm(interaction, "unlock"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    channel = channel or interaction.channel
    await channel.set_permissions(interaction.guild.default_role, send_messages=True)
    embed = discord.Embed(title="Channel Unlocked", description=f"{channel.mention} has been unlocked.", color=0x00cc66)
    await interaction.response.send_message(embed=embed)
    await log(interaction.guild, f"{interaction.user} unlocked {channel.mention}")

@tree.command(name="nukeprotection", description="Toggle nuke protection on or off")
@app_commands.describe(toggle="on or off")
async def nukeprotection(interaction: discord.Interaction, toggle: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings["nuke_protection"] = toggle.lower() == "on"
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"Nuke protection turned {toggle.lower()}.", ephemeral=True)

# ============ ROLES ============

@tree.command(name="giverole", description="Give a role to a member")
@app_commands.describe(member="Member to give role to", role="Role to give")
async def giverole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not has_perm(interaction, "giverole"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await member.add_roles(role)
    await interaction.response.send_message(f"Gave **{role.name}** to {member.mention}.")
    await log(interaction.guild, f"{interaction.user} gave {role.name} to {member}")

@tree.command(name="takerole", description="Remove a role from a member")
@app_commands.describe(member="Member to remove role from", role="Role to remove")
async def takerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not has_perm(interaction, "takerole"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    await member.remove_roles(role)
    await interaction.response.send_message(f"Removed **{role.name}** from {member.mention}.")
    await log(interaction.guild, f"{interaction.user} removed {role.name} from {member}")

@tree.command(name="rolereward", description="Set a role to be given at a certain level")
@app_commands.describe(level="Level required", role="Role to give")
async def rolereward(interaction: discord.Interaction, level: int, role: discord.Role):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    if "role_rewards" not in settings:
        settings["role_rewards"] = {}
    settings["role_rewards"][str(level)] = str(role.id)
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"Members will receive **{role.name}** at level {level}.", ephemeral=True)

@tree.command(name="reactionrole", description="Add a reaction role to a message")
@app_commands.describe(message_id="Message ID to add reaction role to", emoji="Emoji to react with", role="Role to give")
async def reactionrole(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    rr_data = load_json(REACTION_ROLES_FILE)
    guild_id = str(interaction.guild.id)
    if guild_id not in rr_data:
        rr_data[guild_id] = {}
    if message_id not in rr_data[guild_id]:
        rr_data[guild_id][message_id] = {}
    rr_data[guild_id][message_id][emoji] = str(role.id)
    save_json(REACTION_ROLES_FILE, rr_data)
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except:
        pass
    await interaction.response.send_message(f"Reaction role set. React with {emoji} to get **{role.name}**.", ephemeral=True)

# ============ AUTO MOD ============

@tree.command(name="blacklist", description="Add a word to the blacklist")
@app_commands.describe(word="Word to blacklist")
async def blacklist(interaction: discord.Interaction, word: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    if "blacklist" not in settings:
        settings["blacklist"] = []
    if word.lower() not in settings["blacklist"]:
        settings["blacklist"].append(word.lower())
        save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"**{word}** added to blacklist.", ephemeral=True)

@tree.command(name="unblacklist", description="Remove a word from the blacklist")
@app_commands.describe(word="Word to remove")
async def unblacklist(interaction: discord.Interaction, word: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    bl = settings.get("blacklist", [])
    if word.lower() in bl:
        bl.remove(word.lower())
        settings["blacklist"] = bl
        save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"**{word}** removed from blacklist.", ephemeral=True)

@tree.command(name="invitefilter", description="Toggle invite link filter on or off")
@app_commands.describe(toggle="on or off")
async def invitefilter(interaction: discord.Interaction, toggle: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings["invite_filter"] = toggle.lower() == "on"
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"Invite filter turned {toggle.lower()}.", ephemeral=True)

@tree.command(name="familyfilter", description="Toggle the built-in family friendly word filter")
@app_commands.describe(toggle="on or off")
async def familyfilter(interaction: discord.Interaction, toggle: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings["family_filter"] = toggle.lower() == "on"
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"Family friendly filter turned {toggle.lower()}.", ephemeral=True)

@tree.command(name="antispam", description="Toggle antispam on or off")
@app_commands.describe(toggle="on or off")
async def antispam(interaction: discord.Interaction, toggle: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings["antispam"] = toggle.lower() == "on"
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"Antispam turned {toggle.lower()}.", ephemeral=True)

# ============ ANTI-RAID ============

@tree.command(name="raidmode", description="Toggle raid mode on or off")
@app_commands.describe(toggle="on or off")
async def raidmode(interaction: discord.Interaction, toggle: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    if toggle.lower() == "on":
        raid_mode[interaction.guild.id] = True
        for ch in interaction.guild.text_channels:
            try:
                await ch.edit(slowmode_delay=30)
            except:
                pass
        await interaction.response.send_message("Raid mode enabled. 30s slowmode applied to all channels.")
        await log(interaction.guild, f"{interaction.user} manually enabled raid mode.")
    elif toggle.lower() == "off":
        raid_mode[interaction.guild.id] = False
        for ch in interaction.guild.text_channels:
            try:
                await ch.edit(slowmode_delay=0)
            except:
                pass
        await interaction.response.send_message("Raid mode disabled. Slowmode removed from all channels.")
        await log(interaction.guild, f"{interaction.user} disabled raid mode.")
    else:
        await interaction.response.send_message("Use on or off.", ephemeral=True)

# ============ VERIFICATION ============

@tree.command(name="verifysetup", description="Set up the verification system")
@app_commands.describe(verified_role="Role to give after passing captcha")
async def verifysetup(interaction: discord.Interaction, verified_role: discord.Role):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    settings = get_settings(guild.id)
    settings["verified_role"] = str(verified_role.id)
    save_settings(guild.id, settings)

    # Create verify channel if not exists
    verify_channel = discord.utils.get(guild.text_channels, name="verify")
    if not verify_channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, use_application_commands=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        verify_channel = await guild.create_text_channel("verify", overwrites=overwrites)
    else:
        await verify_channel.set_permissions(guild.default_role, read_messages=True, send_messages=True, use_application_commands=True)

    # Lock all other channels from @everyone
    for ch in guild.text_channels:
        if ch.name != "verify" and ch.name != LOG_CHANNEL_NAME:
            await ch.set_permissions(guild.default_role, read_messages=False)

    # Give verified role access to all channels
    for ch in guild.text_channels:
        if ch.name != "verify":
            await ch.set_permissions(verified_role, read_messages=True, send_messages=True)

    embed = discord.Embed(
        title="Verification Required",
        description="Welcome! Use `/verify` to receive a captcha via DM. Solve it to get access to the server.",
        color=0x5865F2
    )
    await verify_channel.send(embed=embed)
    await interaction.response.send_message(f"Verification system set up. New members must complete captcha to access the server.", ephemeral=True)

@tree.command(name="verify", description="Verify yourself to access the server")
async def verify(interaction: discord.Interaction):
    guild = interaction.guild
    user = interaction.user
    settings = get_settings(guild.id)

    verified_role_id = settings.get("verified_role")
    if not verified_role_id:
        await interaction.response.send_message("Verification is not set up on this server.", ephemeral=True)
        return

    verified_role = guild.get_role(int(verified_role_id))
    if verified_role and verified_role in user.roles:
        await interaction.response.send_message("You are already verified.", ephemeral=True)
        return

    code, image_buf = generate_captcha()
    captcha_codes[str(user.id)] = code
    captcha_attempts[str(user.id)] = 0

    await interaction.response.send_message("Check your DMs for the captcha code!", ephemeral=True)
    try:
        file = discord.File(fp=image_buf, filename="captcha.png")
        await user.send("Type the code shown in the image to verify. You have 3 attempts.", file=file)
    except:
        await interaction.followup.send("Could not DM you. Please enable DMs from server members and try again.", ephemeral=True)

# ============ TICKETS ============

@tree.command(name="ticketsetup", description="Set up the ticket system")
@app_commands.describe(support_role="Role that can see all tickets")
async def ticketsetup(interaction: discord.Interaction, support_role: discord.Role = None):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    guild = interaction.guild
    settings = get_settings(guild.id)
    if support_role:
        settings["ticket_support_role"] = str(support_role.id)

    # Auto create tickets channel if it doesn't exist
    ticket_channel = discord.utils.get(guild.text_channels, name="tickets")
    if not ticket_channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False, use_application_commands=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        ticket_channel = await guild.create_text_channel("tickets", overwrites=overwrites)

    settings["ticket_channel"] = str(ticket_channel.id)
    save_settings(guild.id, settings)
    embed = discord.Embed(
        title="Support Tickets",
        description="Need help? Use `/ticket` to open a support ticket and our team will assist you.",
        color=0x5865F2
    )
    await ticket_channel.send(embed=embed)
    await interaction.response.send_message(f"Ticket system set up in {ticket_channel.mention}.", ephemeral=True)

@tree.command(name="ticket", description="Open a support ticket")
@app_commands.describe(reason="Reason for opening a ticket")
async def ticket(interaction: discord.Interaction, reason: str = "No reason provided"):
    guild = interaction.guild
    user = interaction.user
    settings = get_settings(guild.id)
    ticket_name = f"ticket-{user.name.lower().replace(' ', '-')}"
    existing = discord.utils.get(guild.text_channels, name=ticket_name)
    if existing:
        await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
        return
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    support_role_id = settings.get("ticket_support_role")
    if support_role_id:
        support_role = guild.get_role(int(support_role_id))
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    channel = await guild.create_text_channel(ticket_name, overwrites=overwrites)
    embed = discord.Embed(
        title="Support Ticket",
        description=f"Ticket opened by {user.mention}\n**Reason:** {reason}\n\nAn admin will assist you shortly. Use `/closeticket` to close.",
        color=0x5865F2
    )
    await channel.send(embed=embed)
    await interaction.response.send_message(f"Ticket opened: {channel.mention}", ephemeral=True)
    await log(guild, f"{user} opened a ticket — {reason}")

@tree.command(name="addticketsupport", description="Add a role that can see all tickets")
@app_commands.describe(role="Role to add as ticket support")
async def addticketsupport(interaction: discord.Interaction, role: discord.Role):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings["ticket_support_role"] = str(role.id)
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message(f"**{role.name}** can now see all tickets.", ephemeral=True)

@tree.command(name="removeticketsupport", description="Remove ticket support role access")
async def removeticketsupport(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    settings = get_settings(interaction.guild.id)
    settings.pop("ticket_support_role", None)
    save_settings(interaction.guild.id, settings)
    await interaction.response.send_message("Ticket support role removed.", ephemeral=True)

@tree.command(name="closeticket", description="Close the current ticket channel")
async def closeticket(interaction: discord.Interaction):
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
        return
    await interaction.response.send_message("Closing ticket in 5 seconds...")
    await log(interaction.guild, f"Ticket {interaction.channel.name} closed by {interaction.user}")
    await asyncio.sleep(5)
    await interaction.channel.delete()

# ============ CUSTOM COMMANDS ============

@tree.command(name="addcommand", description="Add a custom command")
@app_commands.describe(trigger="Command trigger e.g. !rules", response="Response message")
async def addcommand(interaction: discord.Interaction, trigger: str, response: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    custom_cmds = load_json(CUSTOM_COMMANDS_FILE)
    guild_id = str(interaction.guild.id)
    if guild_id not in custom_cmds:
        custom_cmds[guild_id] = {}
    custom_cmds[guild_id][trigger] = response
    save_json(CUSTOM_COMMANDS_FILE, custom_cmds)
    await interaction.response.send_message(f"Command **{trigger}** added.", ephemeral=True)

@tree.command(name="removecommand", description="Remove a custom command")
@app_commands.describe(trigger="Command trigger to remove")
async def removecommand(interaction: discord.Interaction, trigger: str):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    custom_cmds = load_json(CUSTOM_COMMANDS_FILE)
    guild_id = str(interaction.guild.id)
    if guild_id in custom_cmds and trigger in custom_cmds[guild_id]:
        del custom_cmds[guild_id][trigger]
        save_json(CUSTOM_COMMANDS_FILE, custom_cmds)
        await interaction.response.send_message(f"Command **{trigger}** removed.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Command **{trigger}** not found.", ephemeral=True)

@tree.command(name="listcommands", description="List all custom commands")
async def listcommands(interaction: discord.Interaction):
    custom_cmds = load_json(CUSTOM_COMMANDS_FILE)
    guild_id = str(interaction.guild.id)
    cmds = custom_cmds.get(guild_id, {})
    if not cmds:
        await interaction.response.send_message("No custom commands yet.", ephemeral=True)
        return
    desc = "\n".join([f"**{k}** → {v}" for k, v in cmds.items()])
    embed = discord.Embed(title="Custom Commands", description=desc, color=0x5865F2)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============ INFO ============

@tree.command(name="help", description="Show all commands")
async def help(interaction: discord.Interaction):
    embed = discord.Embed(title="Corsu Bot — Commands", color=0x5865F2)
    embed.add_field(name="Moderation", value="`/ban` `/kick` `/mute` `/unmute` `/tempban` `/warn` `/warns` `/clearwarns` `/purge` `/slowmode` `/lockdown` `/unlock`", inline=False)
    embed.add_field(name="Roles", value="`/giverole` `/takerole` `/rolereward` `/reactionrole` `/createrole`", inline=False)
    embed.add_field(name="Permissions", value="`/perm` `/removeperm` `/perms`", inline=False)
    embed.add_field(name="Auto Mod", value="`/blacklist` `/unblacklist` `/invitefilter` `/familyfilter` `/antispam`", inline=False)
    embed.add_field(name="Security", value="`/raidmode` `/nukeprotection` `/verifysetup` `/verify`", inline=False)
    embed.add_field(name="Tickets", value="`/ticketsetup` `/ticket` `/closeticket` `/addticketsupport` `/removeticketsupport`", inline=False)
    embed.add_field(name="Custom Commands", value="`/addcommand` `/removecommand` `/listcommands`", inline=False)
    embed.add_field(name="Info", value="`/userinfo` `/serverinfo` `/ping`", inline=False)
    embed.add_field(name="Levels", value="`/level` `/leaderboard`", inline=False)
    embed.add_field(name="Fun", value="`/poll` `/announce` `/8ball` `/coinflip`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="userinfo", description="Get info about a user")
@app_commands.describe(member="Member to look up")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"User Info — {member}", color=0x5865F2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Roles", value=", ".join([r.name for r in member.roles[1:]]) or "None")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="serverinfo", description="Get server information")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"Server Info — {guild.name}", color=0x5865F2)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Members", value=guild.member_count)
    embed.add_field(name="Channels", value=len(guild.channels))
    embed.add_field(name="Roles", value=len(guild.roles))
    embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Owner", value=guild.owner)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    embed = discord.Embed(title="Pong!", description=f"Latency: **{round(bot.latency * 1000)}ms**", color=0x5865F2)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============ LEVELS ============

@tree.command(name="level", description="Check your level or another member's level")
@app_commands.describe(member="Member to check")
async def level(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    xp_data = load_json(XP_FILE)
    guild_id = str(interaction.guild.id)
    user_id = str(member.id)
    data = xp_data.get(guild_id, {}).get(user_id, {"xp": 0, "level": 1})
    embed = discord.Embed(title=f"Level — {member}", color=0x5865F2)
    embed.add_field(name="Level", value=data["level"])
    embed.add_field(name="XP", value=f"{data['xp']} / {data['level'] * 100}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="leaderboard", description="Show the XP leaderboard")
async def leaderboard(interaction: discord.Interaction):
    xp_data = load_json(XP_FILE)
    guild_id = str(interaction.guild.id)
    guild_data = xp_data.get(guild_id, {})
    if not guild_data:
        await interaction.response.send_message("No XP data yet.", ephemeral=True)
        return
    sorted_users = sorted(guild_data.items(), key=lambda x: (x[1]["level"], x[1]["xp"]), reverse=True)[:10]
    desc = ""
    for i, (user_id, data) in enumerate(sorted_users):
        user = interaction.guild.get_member(int(user_id))
        name = user.display_name if user else "Unknown"
        desc += f"{i+1}. **{name}** — Level {data['level']} ({data['xp']} XP)\n"
    embed = discord.Embed(title="XP Leaderboard", description=desc, color=0x5865F2)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============ FUN ============

@tree.command(name="poll", description="Create a poll")
@app_commands.describe(question="Poll question")
async def poll(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="Poll", description=question, color=0x5865F2)
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")

@tree.command(name="announce", description="Send an announcement to a channel")
@app_commands.describe(channel="Channel to announce in", message="Announcement message")
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not has_perm(interaction, "announce"):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    embed = discord.Embed(description=message, color=0x5865F2, timestamp=datetime.utcnow())
    embed.set_footer(text=f"Announced by {interaction.user}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"Announcement sent to {channel.mention}", ephemeral=True)

@tree.command(name="8ball", description="Ask the magic 8ball a question")
@app_commands.describe(question="Your question")
async def eightball(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain.", "Without a doubt.", "Yes, definitely.", "You may rely on it.",
        "As I see it, yes.", "Most likely.", "Outlook good.", "Yes.",
        "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
        "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.",
        "Outlook not so good.", "Very doubtful."
    ]
    embed = discord.Embed(color=0x5865F2)
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=random.choice(responses), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(f"**{result}**", ephemeral=True)


# ============ PERMISSIONS ============

@tree.command(name="perm", description="Grant a user or role access to a bot command")
@app_commands.describe(command="Command name e.g. ban", user="User to grant perm to", role="Role to grant perm to")
async def perm(interaction: discord.Interaction, command: str, user: discord.Member = None, role: discord.Role = None):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    if not user and not role:
        await interaction.response.send_message("Specify a user or role.", ephemeral=True)
        return
    perms = load_json(PERMS_FILE)
    guild_id = str(interaction.guild.id)
    if guild_id not in perms:
        perms[guild_id] = {"users": {}, "roles": {}}
    if user:
        uid = str(user.id)
        if uid not in perms[guild_id]["users"]:
            perms[guild_id]["users"][uid] = []
        if command not in perms[guild_id]["users"][uid]:
            perms[guild_id]["users"][uid].append(command)
        save_json(PERMS_FILE, perms)
        await interaction.response.send_message(f"Granted **{user.mention}** access to `/{command}`.", ephemeral=True)
    if role:
        rid = str(role.id)
        if rid not in perms[guild_id]["roles"]:
            perms[guild_id]["roles"][rid] = []
        if command not in perms[guild_id]["roles"][rid]:
            perms[guild_id]["roles"][rid].append(command)
        save_json(PERMS_FILE, perms)
        await interaction.response.send_message(f"Granted **{role.name}** access to `/{command}`.", ephemeral=True)

@tree.command(name="removeperm", description="Remove a user or role's access to a bot command")
@app_commands.describe(command="Command name", user="User to remove perm from", role="Role to remove perm from")
async def removeperm(interaction: discord.Interaction, command: str, user: discord.Member = None, role: discord.Role = None):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    perms = load_json(PERMS_FILE)
    guild_id = str(interaction.guild.id)
    if user:
        uid = str(user.id)
        try:
            perms[guild_id]["users"][uid].remove(command)
            save_json(PERMS_FILE, perms)
        except:
            pass
        await interaction.response.send_message(f"Removed **{user.mention}**'s access to `/{command}`.", ephemeral=True)
    if role:
        rid = str(role.id)
        try:
            perms[guild_id]["roles"][rid].remove(command)
            save_json(PERMS_FILE, perms)
        except:
            pass
        await interaction.response.send_message(f"Removed **{role.name}**'s access to `/{command}`.", ephemeral=True)

@tree.command(name="perms", description="View permissions for a user or role")
@app_commands.describe(user="User to check", role="Role to check")
async def perms(interaction: discord.Interaction, user: discord.Member = None, role: discord.Role = None):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    perms_data = load_json(PERMS_FILE)
    guild_id = str(interaction.guild.id)
    guild_perms = perms_data.get(guild_id, {"users": {}, "roles": {}})
    if user:
        cmds = guild_perms["users"].get(str(user.id), [])
        desc = ", ".join([f"`/{c}`" for c in cmds]) if cmds else "No permissions granted."
        embed = discord.Embed(title=f"Permissions — {user}", description=desc, color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif role:
        cmds = guild_perms["roles"].get(str(role.id), [])
        desc = ", ".join([f"`/{c}`" for c in cmds]) if cmds else "No permissions granted."
        embed = discord.Embed(title=f"Permissions — {role.name}", description=desc, color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("Specify a user or role.", ephemeral=True)

@tree.command(name="createrole", description="Create a new role with basic member permissions")
@app_commands.describe(name="Name of the role", color="Hex color e.g. ff5733 (optional)")
async def createrole(interaction: discord.Interaction, name: str, color: str = None):
    if not is_admin(interaction):
        await interaction.response.send_message("Admins only.", ephemeral=True)
        return
    role_color = discord.Color.default()
    if color:
        try:
            role_color = discord.Color(int(color.strip("#"), 16))
        except:
            pass
    # Basic member permissions
    perms = discord.Permissions(
        read_messages=True,
        send_messages=True,
        read_message_history=True,
        add_reactions=True,
        use_application_commands=True,
        attach_files=True,
        embed_links=True,
        connect=True,
        speak=True
    )
    role = await interaction.guild.create_role(name=name, permissions=perms, color=role_color)
    await interaction.response.send_message(f"Role **{role.name}** created. Use `/perm role:{role.name} command:ban` to grant it bot permissions.", ephemeral=True)
    await log(interaction.guild, f"{interaction.user} created role {role.name}")

# ============ ERROR HANDLING ============

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await interaction.response.send_message(f"Error: {str(error)}", ephemeral=True)

# ============ RUN ============
bot.run(TOKEN)
