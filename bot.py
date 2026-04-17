import os
import sys
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

# ---------- YTDLP (RAILWAY + 403 FIXED) ----------
ytdl_format_options = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "nocheckcertificate": True,
    "geo_bypass": True,

    # FIX: avoids PO token / mobile client errors
    "extractor_args": {
        "youtube": {
            "player_client": ["web"]
        }
    },

    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
}

if os.path.exists("cookies.txt"):
    ytdl_format_options["cookiefile"] = "cookies.txt"

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin",
    "options": "-vn"
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# ---------- AUDIO ----------
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "Unknown Title")
        self.web_url = data.get("webpage_url", "")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string") or str(data.get("duration", "Unknown"))
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
                raise Exception("No data found")

            if "entries" in data:
                entries = [e for e in data["entries"] if e]
                if not entries:
                    raise Exception("No results found")
                data = entries[0]

            # ---------- AUDIO PICK FIX ----------
            formats = data.get("formats", [])
            audio_url = None

            audio_formats = [
                f for f in formats
                if f.get("url")
                and f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")
            ]

            if audio_formats:
                audio_formats.sort(
                    key=lambda x: (x.get("abr") or x.get("tbr") or 0),
                    reverse=True
                )
                audio_url = audio_formats[0]["url"]

            if not audio_url:
                for f in formats:
                    if f.get("url") and f.get("acodec") not in (None, "none"):
                        audio_url = f["url"]
                        break

            if not audio_url:
                audio_url = data.get("url")

            if not audio_url:
                raise Exception("No playable audio stream found")

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
    async def back_button(self, interaction, button):
        if len(history) < 2:
            return await interaction.response.send_message("❌ No previous song.", ephemeral=True)

        current = history.pop()
        previous = history.pop()

        queue.insert(0, previous)
        queue.insert(1, current)

        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()

        await interaction.response.send_message("⏮ Back playing...", ephemeral=True)

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction, button):
        vc = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message("❌ Not connected", ephemeral=True)

        if vc.is_playing():
            vc.pause()
            return await interaction.response.send_message("⏸ Paused", ephemeral=True)

        if vc.is_paused():
            vc.resume()
            return await interaction.response.send_message("▶ Resumed", ephemeral=True)

        await interaction.response.send_message("❌ Nothing playing", ephemeral=True)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.success)
    async def skip_button(self, interaction, button):
        vc = interaction.guild.voice_client

        if vc:
            vc.stop()
            return await interaction.response.send_message("⏭ Skipped", ephemeral=True)

        await interaction.response.send_message("❌ Nothing playing", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction, button):
        global loop_enabled
        loop_enabled = not loop_enabled
        await interaction.response.send_message(
            f"Loop {'ON 🔁' if loop_enabled else 'OFF'}",
            ephemeral=True
        )

# ---------- PLAY ----------
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
        await ctx.send(f"❌ Error: {e}")
        if queue:
            await play_next(ctx)
        return

    current_player = player

    def after(error):
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    ctx.voice_client.play(player, after=after)

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**{player.title}**\n🎤 {player.uploader}\n👤 {ctx.author.mention}",
        color=0x2B2D31
    )

    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)

    await ctx.send(embed=embed, view=MusicControls(ctx))

# ---------- READY ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    # Railway keep alive
    bot.loop.create_task(asyncio.sleep(0))

# ---------- VOICE ----------
async def ensure_voice(ctx):
    if not ctx.author.voice:
        await ctx.send("❌ Join a voice channel first")
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
    await ctx.send(f"➕ Added: `{query}`")

    if not ctx.voice_client.is_playing():
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
