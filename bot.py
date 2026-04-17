import os
import asyncio
import random
import time
import discord
import yt_dlp
import nacl

from dotenv import load_dotenv
from discord.ext import commands
from discord.ui import View

# ---------- LOAD TOKEN ----------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env")

# ---------- BOT SETUP ----------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

# ---------- STORAGE ----------
queue = []
history = []
loop_enabled = False
current_player = None
current_song_query = None
song_start_time = None

# ---------- YTDL (FIXED FOR 403 / PO TOKEN ISSUES) ----------
ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "skip_download": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "geo_bypass": True,
    "extract_flat": False,

    # 🔥 FIX: safer client usage (avoids PO token required formats)
    "extractor_args": {
        "youtube": {
            "player_client": ["web", "android"]
        }
    },

    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    }
}

if os.path.exists("cookies.txt"):
    ytdl_format_options["cookiefile"] = "cookies.txt"

ffmpeg_options = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5"
    ),
    "options": "-vn"
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# ---------- AUDIO SOURCE ----------
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "Unknown Title")
        self.web_url = data.get("webpage_url", "")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string") or str(data.get("duration", "Unknown"))
        self.duration_secs = data.get("duration", 0)
        self.uploader = data.get("uploader") or data.get("channel", "Unknown")

    @classmethod
    async def from_query(cls, query):
        loop = asyncio.get_event_loop()

        try:
            data = await loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(query, download=False)
            )

            if not data:
                raise Exception("No data returned from YouTube.")

            if "entries" in data:
                entries = [e for e in data.get("entries", []) if e]
                if not entries:
                    raise Exception("Song not found.")
                data = entries[0]

            if data.get("webpage_url"):
                try:
                    full_data = await loop.run_in_executor(
                        None,
                        lambda: ytdl.extract_info(data["webpage_url"], download=False)
                    )
                    if full_data:
                        data = full_data
                except:
                    pass

            # ---------- 🔥 FIX: safer audio selection ----------
            formats = data.get("formats", [])
            audio_url = None

            # prefer clean audio-only formats
            audio_formats = [
                fmt for fmt in formats
                if fmt.get("url")
                and fmt.get("acodec") not in (None, "none")
                and fmt.get("vcodec") in (None, "none")
            ]

            if audio_formats:
                audio_formats.sort(
                    key=lambda x: (x.get("abr") or x.get("tbr") or 0),
                    reverse=True
                )
                audio_url = audio_formats[0]["url"]

            # fallback 1
            if not audio_url:
                for fmt in formats:
                    if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                        audio_url = fmt["url"]
                        break

            # fallback 2
            if not audio_url:
                audio_url = data.get("url")

            if not audio_url:
                raise Exception("No playable audio format found (YouTube restricted formats skipped).")

            source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
            return cls(source, data=data)

        except Exception as e:
            raise Exception(str(e))

# ---------- BUTTONS ----------
class MusicControls(View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="⏮ Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(history) < 2:
            return await interaction.response.send_message("❌ No previous song available.", ephemeral=True)

        current_song = history.pop()
        previous_song = history.pop()

        queue.insert(0, previous_song)
        queue.insert(1, current_song)

        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()

        await interaction.response.send_message("⏮ Playing previous song...", ephemeral=True)

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message("❌ Bot is not connected.", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            return await interaction.response.send_message("⏸ Music paused.", ephemeral=True)

        if vc.is_paused():
            vc.resume()
            return await interaction.response.send_message("▶ Music resumed.", ephemeral=True)

        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.success)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            return await interaction.response.send_message("⏭ Skipped current song.", ephemeral=True)

        await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global loop_enabled
        loop_enabled = not loop_enabled
        await interaction.response.send_message(
            f"Loop {'enabled 🔁' if loop_enabled else 'disabled'}.",
            ephemeral=True
        )

# ---------- PLAY NEXT ----------
async def play_next(ctx):
    global current_player, current_song_query, song_start_time

    if loop_enabled and current_song_query:
        queue.insert(0, current_song_query)

    if not queue:
        current_player = None
        current_song_query = None
        return

    next_song = queue.pop(0)
    history.append(next_song)
    current_song_query = next_song
    song_start_time = time.time()

    try:
        player = await YTDLSource.from_query(next_song)
    except Exception as e:
        await ctx.send(embed=discord.Embed(
            title="❌ Failed To Play Song",
            description=f"```{e}```",
            color=0xFF0000
        ))
        if queue:
            await play_next(ctx)
        return

    current_player = player

    def after_play(error):
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    ctx.voice_client.play(player, after=after_play)

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=(
            f"**{player.title}**\n\n"
            f"⏱ Duration: `{player.duration}`\n"
            f"🎤 Uploader: `{player.uploader}`\n"
            f"👤 Requested by: {ctx.author.mention}"
        ),
        color=0x2B2D31
    )

    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)

    if player.web_url:
        embed.add_field(name="Link", value=f"[Open Song]({player.web_url})", inline=False)

    await ctx.send(embed=embed, view=MusicControls(ctx))

# ---------- READY ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

# ---------- VOICE ----------
async def ensure_voice(ctx):
    if not ctx.author.voice:
        await ctx.send("❌ Join a voice channel first.")
        return False

    channel = ctx.author.voice.channel

    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    return True

# ---------- COMMANDS ----------
@bot.command()
async def play(ctx, *, query):
    if not await ensure_voice(ctx):
        return

    queue.append(query)

    await ctx.send(f"➕ Added to queue: `{query}`")

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)

@bot.command()
async def p(ctx, *, query):
    await play(ctx, query=query)

@bot.command()
async def skip(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()

@bot.command()
async def pause(ctx):
    if ctx.voice_client:
        ctx.voice_client.pause()

@bot.command()
async def resume(ctx):
    if ctx.voice_client:
        ctx.voice_client.resume()

@bot.command()
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        ctx.voice_client.stop()

@bot.command()
async def leave(ctx):
    queue.clear()
    history.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()

# ---------- RUN ----------
bot.run(TOKEN)
