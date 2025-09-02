import os
import logging
import json
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands, Embed, Object
from discord.ext import commands
from dotenv import load_dotenv

# ----------------------------
# Env & basic setup
# ----------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

# Intents: enable message_content for transcripts
intents = discord.Intents.default()
intents.message_content = True  # enable in Dev Portal too (Bot → Privileged Gateway Intents)
bot = commands.Bot(command_prefix="!", intents=intents)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ----------------------------
# Load config
# ----------------------------
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        CONFIG = json.load(f)
else:
    CONFIG = {
        "announcement_channel_ids": [],
        "brand": {
            "name": "Rusty Tiger",
            "color": "#d97706",
            "author_icon": "",
            "footer_text": "Stay Rusty.",
            "default_buttons": []
        },
        "tickets": {
            "category_id": "",
            "support_role_id": "",
            "transcripts_channel_id": "",
            "panel_channel_id": "",
            "panel_title": "Need Help?",
            "panel_description": "Click the button to open a private ticket.",
            "button_label": "🐯 Open Ticket",
            "ticket_prefix": "ticket"
        }
    }

# ----------------------------
# Helpers (embed styling, color, newlines, JSON parsing)
# ----------------------------
COLOR_MAP = {
    "orange": 0xD97706,
    "burnt_orange": 0xCC5500,
    "black": 0x111111,
    "gray": 0x4B5563,
    "red": 0xEF4444,
    "green": 0x10B981,
    "blue": 0x3B82F6,
    "purple": 0x8B5CF6,
    "gold": 0xF59E0B,
}

def parse_color(value: Optional[str]) -> int:
    if not value:
        brand_color = (CONFIG.get("brand") or {}).get("color")
        return parse_color(brand_color) if brand_color else COLOR_MAP["orange"]
    v = value.strip().lower()
    if v in COLOR_MAP:
        return COLOR_MAP[v]
    v = v.lstrip("#")
    try:
        return int(v, 16) & 0xFFFFFF
    except Exception:
        return COLOR_MAP["orange"]

def list_or_none(x: Optional[str]) -> Optional[List[Any]]:
    if not x:
        return None
    try:
        obj = json.loads(x)
        return obj if isinstance(obj, list) else None
    except Exception:
        return None

def normalize_multiline(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return (s
            .replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t"))

def make_announcement_embed(
    text: str,
    title: Optional[str] = None,
    color: Optional[str] = None,
    url: Optional[str] = None,
    thumbnail: Optional[str] = None,
    image: Optional[str] = None,
    footer: Optional[str] = None,
    author_name: Optional[str] = None,
    author_icon: Optional[str] = None,
) -> Embed:
    brand = CONFIG.get("brand") or {}
    chosen_color = parse_color(color or brand.get("color"))
    emb = Embed(
        title=title or "📣 Announcement",
        description=text,
        color=chosen_color
    )
    emb.url = url or discord.utils.MISSING
    emb.timestamp = discord.utils.utcnow()

    _author_name = author_name or brand.get("name")
    _author_icon = author_icon or brand.get("author_icon") or None
    if _author_name:
        if _author_icon:
            emb.set_author(name=_author_name, icon_url=_author_icon)
        else:
            emb.set_author(name=_author_name)

    if thumbnail:
        emb.set_thumbnail(url=thumbnail)
    if image:
        emb.set_image(url=image)

    footer_text = footer or brand.get("footer_text")
    if footer_text:
        emb.set_footer(text=footer_text)

    return emb

async def sync_commands():
    if GUILD_ID:
        try:
            guild = Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logging.info(f"Synced {len(synced)} commands to guild {GUILD_ID}.")
        except Exception as e:
            logging.exception(f"Failed to sync to guild {GUILD_ID}: {e}")
            synced = await bot.tree.sync()
            logging.info(f"Synced {len(synced)} global commands (fallback).")
    else:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} global commands.")

# ----------------------------
# Ticket System
# ----------------------------
class TicketReasonModal(discord.ui.Modal, title="Open a Ticket"):
    reason = discord.ui.TextInput(
        label="Brief description",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us what you need help with...",
        max_length=1000,
        required=True
    )

    def __init__(self, opener: discord.Member):
        super().__init__()
        self.opener = opener

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket_channel(interaction, self.opener, str(self.reason))

class TicketPanelView(discord.ui.View):
    def __init__(self, opener: Optional[discord.Member] = None):
        super().__init__(timeout=None)
        self.opener = opener

    @discord.ui.button(label=(CONFIG.get("tickets") or {}).get("button_label", "🎟️ Open Ticket"),
                       style=discord.ButtonStyle.primary, custom_id="ticket_open_button")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TicketReasonModal(opener=interaction.user)
        await interaction.response.send_modal(modal)

async def create_ticket_channel(interaction: discord.Interaction, opener: discord.Member, reason: str):
    tcfg = CONFIG.get("tickets") or {}
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Tickets must be used in a server.", ephemeral=True)

    # Category
    category_id = tcfg.get("category_id")
    category = guild.get_channel(int(category_id)) if category_id else None
    if category_id and not isinstance(category, discord.CategoryChannel):
        category = None

    # Role
    support_role_id = tcfg.get("support_role_id")
    support_role = guild.get_role(int(support_role_id)) if support_role_id else None

    # Channel name
    prefix = (tcfg.get("ticket_prefix") or "ticket").lower()
    channel_name = f"{prefix}-{opener.name[:20].lower()}-{interaction.id % 10000:04d}"

    # Overwrites: only opener + staff can view
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        opener: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, attach_files=True, embed_links=True),
    }
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True, manage_messages=True)

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        reason=f"Ticket opened by {opener} ({opener.id})"
    )

    # Intro embed
    intro = make_announcement_embed(
        text=f"**Ticket opened by {opener.mention}**\n\n**Reason:**\n{reason}",
        title="🎟️ New Ticket",
        color="orange"
    )
    await channel.send(embed=intro)
    await channel.send(f"{opener.mention}" + (f" {support_role.mention}" if support_role else ""))

    try:
        await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
    except discord.InteractionResponded:
        pass

async def generate_text_transcript(channel: discord.TextChannel) -> str:
    """
    Returns the path to a text transcript file saved in ./transcripts/.
    """
    os.makedirs("transcripts", exist_ok=True)
    safe_name = f"{channel.name}-{channel.id}.txt"
    path = os.path.join("transcripts", safe_name)

    lines: List[str] = []
    lines.append(f"Transcript for #{channel.name} ({channel.id}) in {channel.guild.name}\n")
    lines.append(f"Channel created at: {channel.created_at} UTC\n")
    lines.append("=" * 60 + "\n")

    async for msg in channel.history(limit=None, oldest_first=True):
        author = f"{msg.author} ({msg.author.id})"
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = msg.content or ""
        # include basic embed summaries
        if msg.embeds:
            for idx, e in enumerate(msg.embeds, start=1):
                title = e.title or ""
                desc = e.description or ""
                lines.append(f"[{ts}] {author} (EMBED {idx}) Title: {title}\n{desc}\n")
        if content:
            lines.append(f"[{ts}] {author}: {content}\n")
        if msg.attachments:
            for a in msg.attachments:
                lines.append(f"[{ts}] {author} attached: {a.url}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return path

# ----------------------------
# Events
# ----------------------------
@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    await sync_commands()

# ----------------------------
# Slash Commands (existing + tickets)
# ----------------------------
@bot.tree.command(name="ping", description="Check if the bot is online.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Tiger Bot is Online ✅", ephemeral=True)

@bot.tree.command(name="say", description="Make the bot send a plain message in this channel.")
@app_commands.describe(message="What should I say?")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(message)

@bot.tree.command(name="say_embed", description="Send a quick, clean embed with optional link and image.")
@app_commands.describe(
    message="Main text (use \\n for new lines; supports **markdown** and [links](https://example.com))",
    title="Optional title (clickable if 'url' provided)",
    url="Optional URL to make the title clickable",
    color="Hex (#d97706) or name (orange, blue, purple...)",
    image="Large image URL",
    thumbnail="Small thumbnail URL",
    footer="Footer text (defaults to brand footer)"
)
async def say_embed(
    interaction: discord.Interaction,
    message: str,
    title: Optional[str] = None,
    url: Optional[str] = None,
    color: Optional[str] = None,
    image: Optional[str] = None,
    thumbnail: Optional[str] = None,
    footer: Optional[str] = None
):
    message = normalize_multiline(message)
    footer = normalize_multiline(footer)

    emb = make_announcement_embed(
        text=message,
        title=title,
        color=color,
        url=url,
        image=image,
        thumbnail=thumbnail,
        footer=footer
    )
    await interaction.response.send_message("✅ Sent!", ephemeral=True)
    await interaction.channel.send(embed=emb)

@bot.tree.command(name="announce", description="Post a styled announcement embed (with optional buttons and role ping).")
@app_commands.describe(
    message="Announcement text (use \\n for new lines; supports **markdown** and [links](https://...))",
    channel="Target channel (optional)",
    title="Title (clickable if 'url' set). Default: 📣 Announcement",
    url="Optional URL to make the title clickable",
    color="Hex (#d97706) or named color (orange, blue, purple, ...)",
    thumbnail="Thumbnail image URL",
    image="Main/hero image URL",
    footer="Footer text (defaults to brand footer)",
    author_name="Override author display (defaults to brand name)",
    author_icon="Author icon URL (defaults to brand icon)",
    ping_role="Role to mention (pings at top of message)",
    buttons_json='JSON: [{"label":"Website","url":"https://..."}, ...]',
)
async def announce(
    interaction: discord.Interaction,
    message: str,
    channel: Optional[discord.abc.GuildChannel] = None,
    title: Optional[str] = None,
    url: Optional[str] = None,
    color: Optional[str] = None,
    thumbnail: Optional[str] = None,
    image: Optional[str] = None,
    footer: Optional[str] = None,
    author_name: Optional[str] = None,
    author_icon: Optional[str] = None,
    ping_role: Optional[discord.Role] = None,
    buttons_json: Optional[str] = None,
):
    target = channel if channel else interaction.channel

    message = normalize_multiline(message)
    footer = normalize_multiline(footer)

    emb = make_announcement_embed(
        text=message,
        title=title,
        color=color,
        url=url,
        thumbnail=thumbnail,
        image=image,
        footer=footer,
        author_name=author_name,
        author_icon=author_icon,
    )

    class LinkButtons(discord.ui.View):
        def __init__(self, buttons: Optional[List[Dict[str, str]]] = None):
            super().__init__(timeout=None)
            buttons = buttons or []
            for b in buttons[:5]:
                label = str(b.get("label", "Open"))
                burl = str(b.get("url", "https://discord.com"))
                if burl.startswith("http://") or burl.startswith("https://"):
                    self.add_item(discord.ui.Button(label=label, url=burl))

    buttons_list = list_or_none(buttons_json)
    if not buttons_list:
        buttons_list = (CONFIG.get("brand") or {}).get("default_buttons") or []
    view = LinkButtons(buttons=buttons_list)

    content = ping_role.mention if ping_role else None

    if isinstance(target, (discord.TextChannel, discord.Thread)):
        try:
            await target.send(content=content, embed=emb, view=view if buttons_list else None)
            await interaction.response.send_message(f"📣 Announcement posted in {target.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to send messages there.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to send: {e}", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Please choose a text channel.", ephemeral=True)

@bot.tree.command(name="announce_in_announcements", description="Post an announcement to all channels listed in config.json.")
@app_commands.describe(
    message="Announcement text (use \\n for new lines)",
    title="Optional title",
    color="Hex or name",
    url="Optional URL for clickable title",
    buttons_json='JSON: [{"label":"Website","url":"https://..."}, ...]'
)
async def announce_all(
    interaction: discord.Interaction,
    message: str,
    title: Optional[str] = None,
    color: Optional[str] = None,
    url: Optional[str] = None,
    buttons_json: Optional[str] = None,
):
    channel_ids = CONFIG.get("announcement_channel_ids", [])
    if not channel_ids:
        await interaction.response.send_message("ℹ️ No channel IDs set in config.json.", ephemeral=True)
        return

    message = normalize_multiline(message)

    emb = make_announcement_embed(text=message, title=title, color=color, url=url)

    class LinkButtons(discord.ui.View):
        def __init__(self, buttons: Optional[List[Dict[str, str]]] = None):
            super().__init__(timeout=None)
            buttons = buttons or []
            for b in buttons[:5]:
                label = str(b.get("label", "Open"))
                burl = str(b.get("url", "https://discord.com"))
                if burl.startswith("http://") or burl.startswith("https://"):
                    self.add_item(discord.ui.Button(label=label, url=burl))

    buttons_list = list_or_none(buttons_json)
    if not buttons_list:
        buttons_list = (CONFIG.get("brand") or {}).get("default_buttons") or []
    view = LinkButtons(buttons=buttons_list)

    sent = 0
    failed = 0
    for cid in channel_ids:
        ch = bot.get_channel(int(cid))
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            try:
                await ch.send(embed=emb, view=view if buttons_list else None)
                sent += 1
            except Exception:
                failed += 1
        else:
            failed += 1

    await interaction.response.send_message(f"📣 Done. Sent: {sent} | Failed: {failed}", ephemeral=True)

# -------- Ticket commands --------
@bot.tree.command(name="ticket_setup", description="Post a ticket panel with an Open Ticket button.")
@app_commands.describe(channel="Channel to post the panel (optional)")
async def ticket_setup(interaction: discord.Interaction, channel: Optional[discord.abc.GuildChannel] = None):
    tcfg = CONFIG.get("tickets") or {}
    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await interaction.response.send_message("❌ Choose a text channel.", ephemeral=True)

    emb = make_announcement_embed(
        text=normalize_multiline(tcfg.get("panel_description") or "Click below to open a ticket."),
        title=tcfg.get("panel_title") or "Need Help?",
        color="orange"
    )
    await target.send(embed=emb, view=TicketPanelView())
    await interaction.response.send_message("✅ Ticket panel posted.", ephemeral=True)

@bot.tree.command(name="ticket_claim", description="Claim the current ticket channel.")
async def ticket_claim(interaction: discord.Interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not ch.name.startswith((CONFIG.get("tickets") or {}).get("ticket_prefix", "ticket")):
        return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)
    await interaction.response.send_message(f"🛠️ Ticket claimed by {interaction.user.mention}.", ephemeral=False)

@bot.tree.command(name="ticket_add", description="Add a user to this ticket.")
@app_commands.describe(user="User to add")
async def ticket_add(interaction: discord.Interaction, user: discord.Member):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not ch.name.startswith((CONFIG.get("tickets") or {}).get("ticket_prefix", "ticket")):
        return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)
    await ch.set_permissions(user, view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
    await interaction.response.send_message(f"✅ Added {user.mention} to this ticket.", ephemeral=False)

@bot.tree.command(name="ticket_remove", description="Remove a user from this ticket.")
@app_commands.describe(user="User to remove")
async def ticket_remove(interaction: discord.Interaction, user: discord.Member):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not ch.name.startswith((CONFIG.get("tickets") or {}).get("ticket_prefix", "ticket")):
        return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)
    await ch.set_permissions(user, overwrite=None)
    await interaction.response.send_message(f"✅ Removed {user.mention} from this ticket.", ephemeral=False)

@bot.tree.command(name="ticket_close", description="Close this ticket (generate transcript and delete channel).")
@app_commands.describe(reason="Reason for closing (optional)")
async def ticket_close(interaction: discord.Interaction, reason: Optional[str] = None):
    ch = interaction.channel
    if not isinstance(ch, discord.TextChannel) or not ch.name.startswith((CONFIG.get("tickets") or {}).get("ticket_prefix", "ticket")):
        return await interaction.response.send_message("❌ Use this inside a ticket channel.", ephemeral=True)

    await interaction.response.send_message("🔒 Closing ticket and generating transcript...", ephemeral=True)

    # Generate transcript
    filepath = await generate_text_transcript(ch)

    # Post to transcripts channel
    tcfg = CONFIG.get("tickets") or {}
    transcripts_channel_id = tcfg.get("transcripts_channel_id")
    transcripts_ch = ch.guild.get_channel(int(transcripts_channel_id)) if transcripts_channel_id else None

    close_embed = make_announcement_embed(
        text=f"Ticket **#{ch.name}** closed by {interaction.user.mention}.\n"
             f"{('**Reason:** ' + reason) if reason else ''}",
        title="✅ Ticket Closed",
        color="green"
    )

    if transcripts_ch and isinstance(transcripts_ch, discord.TextChannel):
        try:
            await transcripts_ch.send(embed=close_embed, file=discord.File(filepath))
        except Exception as e:
            logging.exception(f"Failed to post transcript: {e}")

    # Attach transcript in the ticket for final record then delete
    try:
        await ch.send(embed=close_embed, file=discord.File(filepath))
    except Exception:
        pass

    # Delete the channel
    try:
        await ch.delete(reason=reason or "Ticket closed")
    except Exception as e:
        logging.exception(f"Failed to delete channel: {e}")

# ----------------------------
# Entry
# ----------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set. Put it in .env (see .env.example).")
    bot.run(TOKEN)
