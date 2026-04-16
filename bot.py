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

# ---------- STORAGE ----------
queue = []
history = []
loop_enabled = False
current_player = None
current_song_query = None
song_start_time = None

# ---------- YTDL ----------
ytdl_format_options = {
    "format": "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "skip_download": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "geo_bypass": True,
    "geo_bypass_country": "US",
    "extractor_args": {
        "youtube": {
            "player_client": ["android_music", "android", "ios", "web"],
            "player_skip": ["webpage", "configs"],
        }
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 11; Pixel 5) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/90.0.4430.91 Mobile Safari/537.36"
        )
    },
}

if os.path.exists("cookies.txt"):
    ytdl_format_options["cookiefile"] = "cookies.txt"

ffmpeg_options = {
    "before_options": (
        "-reconnect 1 "
        "-reconnect_streamed 1 "
        "-reconnect_delay_max 5 "
        "-probesize 200M"
    ),
    "options": "-vn -bufsize 64k"
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

        search_query = query
        if not query.startswith(("http://", "https://")):
            search_query = f"ytsearch1:{query}"

        try:
            data = await loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(search_query, download=False)
            )

            if not data:
                raise Exception("No data returned from YouTube.")

            if "entries" in data:
                entries = [e for e in data.get("entries", []) if e]
                if not entries:
                    raise Exception("No song found.")
                data = entries[0]

            if data.get("webpage_url"):
                full_data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(data["webpage_url"], download=False)
                )
                if full_data:
                    data = full_data

            if not data:
                raise Exception("Could not load video information.")

            audio_url = cls._pick_best_audio_url(data)

            if not audio_url:
                raise Exception("No playable audio format found for this video.")

            source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
            return cls(source, data=data)

        except Exception as e:
            raise Exception(str(e))

    @staticmethod
    def _pick_best_audio_url(data):
        if data.get("url") and data.get("acodec") not in (None, "none"):
            return data["url"]

        formats = data.get("formats", [])

        audio_only = [
            f for f in formats
            if (
                f.get("url")
                and f.get("acodec") not in (None, "none")
                and f.get("vcodec") in (None, "none")
            )
        ]
        if audio_only:
            audio_only.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
            return audio_only[0]["url"]

        for fmt in formats:
            if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                return fmt["url"]

        for fmt in data.get("requested_formats", []):
            if fmt and fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                return fmt["url"]

        return None


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
        state = "enabled 🔁" if loop_enabled else "disabled"
        await interaction.response.send_message(f"Loop {state}.", ephemeral=True)


# ---------- PLAY NEXT ----------
async def play_next(ctx):
    global current_player, current_song_query, song_start_time, loop_enabled

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
        embed = discord.Embed(
            description=(f"❌ Failed to play: `{next_song}`\n```{e}```"),
            color=0xFF0000
        )
        await ctx.send(embed=embed)

        if queue:
            await play_next(ctx)
        return

    current_player = player

    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        future = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            future.result()
        except Exception as err:
            print(f"Queue error: {err}")

    ctx.voice_client.play(player, after=after_play)

    loop_indicator = " 🔁" if loop_enabled else ""
    embed = discord.Embed(
        description=(
            f"💜 **Now Playing**{loop_indicator}\n\n"
            f"**{player.title}**\n\n"
            f"⏱️ Duration: `{player.duration}`\n"
            f"🎙️ Artist: `{player.uploader}`\n"
            f"👤 Requested by {ctx.author.mention}\n"
            f"🔗 [Open Song]({player.web_url})"
        ),
        color=0x2B2D31
    )

    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)

    embed.set_footer(text="Use the buttons below to control the music")
    await ctx.send(embed=embed, view=MusicControls(ctx))


# ---------- READY ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play | music 🎵"
        )
    )


# ---------- VOICE HELPER ----------
async def ensure_voice(ctx):
    if ctx.author.voice is None:
        embed = discord.Embed(description="❌ You need to join a voice channel first.", color=0xFF0000)
        await ctx.send(embed=embed)
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

    embed = discord.Embed(description=f"➕ Added to queue: `{query}`", color=0x2B2D31)
    await ctx.send(embed=embed)

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command()
async def p(ctx, *, query):
    await play(ctx, query=query)


@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        embed = discord.Embed(description="📭 Queue is empty.", color=0x2B2D31)
        return await ctx.send(embed=embed)

    description = "\n".join(f"`{i + 1}.` {song}" for i, song in enumerate(queue[:10]))
    embed = discord.Embed(title="📜 Current Queue", description=description, color=0x2B2D31)

    if len(queue) > 10:
        embed.set_footer(text=f"And {len(queue) - 10} more songs...")

    await ctx.send(embed=embed)


@bot.command()
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        embed = discord.Embed(description="⏭ Skipped current song.", color=0x2B2D31)
    else:
        embed = discord.Embed(description="❌ Nothing is playing.", color=0xFF0000)

    await ctx.send(embed=embed)


@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        embed = discord.Embed(description="⏸ Music paused.", color=0x2B2D31)
    else:
        embed = discord.Embed(description="❌ Nothing is playing.", color=0xFF0000)

    await ctx.send(embed=embed)


@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        embed = discord.Embed(description="▶ Music resumed.", color=0x2B2D31)
    else:
        embed = discord.Embed(description="❌ Nothing is paused.", color=0xFF0000)

    await ctx.send(embed=embed)


@bot.command()
async def stop(ctx):
    queue.clear()

    if ctx.voice_client:
        ctx.voice_client.stop()

    embed = discord.Embed(description="⏹ Music stopped and queue cleared.", color=0x2B2D31)
    await ctx.send(embed=embed)


@bot.command()
async def leave(ctx):
    queue.clear()
    history.clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()

    embed = discord.Embed(description="👋 Left the voice channel.", color=0x2B2D31)
    await ctx.send(embed=embed)


# ✨ NEW: Volume control
@bot.command()
async def volume(ctx, vol: int):
    """Set volume 0-100. Example: !volume 75"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        embed = discord.Embed(description="❌ Nothing is playing.", color=0xFF0000)
        return await ctx.send(embed=embed)

    if not (0 <= vol <= 100):
        embed = discord.Embed(description="❌ Volume must be between `0` and `100`.", color=0xFF0000)
        return await ctx.send(embed=embed)

    ctx.voice_client.source.volume = vol / 100

    bar_filled = int(vol / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)

    embed = discord.Embed(description=f"🔊 Volume set to `{vol}%`\n`{bar}`", color=0x2B2D31)
    await ctx.send(embed=embed)


# ✨ NEW: Now playing with progress bar
@bot.command(aliases=["np"])
async def nowplaying(ctx):
    """Show the currently playing song with progress bar."""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        embed = discord.Embed(description="❌ Nothing is currently playing.", color=0xFF0000)
        return await ctx.send(embed=embed)

    player = current_player
    if not player:
        return

    elapsed = int(time.time() - song_start_time) if song_start_time else 0
    total = player.duration_secs or 0

    if total > 0:
        progress = min(elapsed / total, 1.0)
        bar_len = 20
        filled = int(progress * bar_len)
        bar = "▓" * filled + "░" * (bar_len - filled)
        elapsed_str = f"{elapsed // 60}:{elapsed % 60:02d}"
        total_str = f"{total // 60}:{total % 60:02d}"
        progress_line = f"`{elapsed_str}` `{bar}` `{total_str}`"
    else:
        progress_line = f"⏱️ Duration: `{player.duration}`"

    loop_indicator = " 🔁" if loop_enabled else ""
    embed = discord.Embed(
        description=(
            f"💜 **Now Playing**{loop_indicator}\n\n"
            f"**{player.title}**\n\n"
            f"{progress_line}\n"
            f"🎙️ Artist: `{player.uploader}`\n"
            f"🔗 [Open Song]({player.web_url})"
        ),
        color=0x2B2D31
    )

    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)

    await ctx.send(embed=embed)


# ✨ NEW: Shuffle queue
@bot.command()
async def shuffle(ctx):
    """Shuffle the current queue."""
    if not queue:
        embed = discord.Embed(description="📭 Queue is empty.", color=0xFF0000)
        return await ctx.send(embed=embed)

    random.shuffle(queue)

    embed = discord.Embed(description=f"🔀 Shuffled `{len(queue)}` songs in the queue!", color=0x2B2D31)
    await ctx.send(embed=embed)


# ✨ NEW: History command
@bot.command(name="history")
async def history_command(ctx):
    """Show recently played songs."""
    recent = history[-10:][::-1]

    if not recent:
        embed = discord.Embed(description="📭 No songs played yet.", color=0x2B2D31)
        return await ctx.send(embed=embed)

    description = "\n".join(f"`{i + 1}.` {song}" for i, song in enumerate(recent))

    embed = discord.Embed(title="🕘 Recently Played", description=description, color=0x2B2D31)
    await ctx.send(embed=embed)


# ✨ NEW: Loop toggle command
@bot.command()
async def loop(ctx):
    """Toggle loop for the current song."""
    global loop_enabled
    loop_enabled = not loop_enabled

    state = "enabled 🔁" if loop_enabled else "disabled"
    embed = discord.Embed(description=f"🔁 Loop **{state}**.", color=0x2B2D31)
    await ctx.send(embed=embed)


# ✨ NEW: Help command
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(title="🎵 Music Bot Commands", color=0x2B2D31)
    embed.add_field(
        name="Playback",
        value=(
            "`!play <song>` / `!p` — Play or queue a song\n"
            "`!skip` — Skip current song\n"
            "`!pause` — Pause music\n"
            "`!resume` — Resume music\n"
            "`!stop` — Stop & clear queue\n"
            "`!leave` — Disconnect bot"
        ),
        inline=False
    )
    embed.add_field(
        name="Info",
        value=(
            "`!nowplaying` / `!np` — Current song + progress\n"
            "`!queue` — View queue\n"
            "`!history` — Recently played songs"
        ),
        inline=False
    )
    embed.add_field(
        name="Controls",
        value=(
            "`!volume <0-100>` — Set volume\n"
            "`!shuffle` — Shuffle the queue\n"
            "`!loop` — Toggle loop current song"
        ),
        inline=False
    )
    embed.set_footer(text="Use the buttons on the player for quick controls!")
    await ctx.send(embed=embed)


bot.run(TOKEN)
