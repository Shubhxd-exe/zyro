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

# ---------- YTDL ----------
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
    "geo_bypass_country": "US",
    "extract_flat": False,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        )
    },
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios", "web"]
        }
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

        # IMPORTANT: do not manually add ytsearch1:
        # default_search already handles it
        search_query = query

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
                    raise Exception("Song not found.")

                data = entries[0]

            if data.get("webpage_url"):
                full_data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(data["webpage_url"], download=False)
                )

                if full_data:
                    data = full_data

            formats = data.get("formats", [])
            audio_url = None

            audio_formats = [
                fmt for fmt in formats
                if (
                    fmt.get("url")
                    and fmt.get("acodec") not in (None, "none")
                    and fmt.get("vcodec") in (None, "none")
                )
            ]

            if audio_formats:
                audio_formats.sort(
                    key=lambda x: x.get("abr") or x.get("tbr") or 0,
                    reverse=True
                )
                audio_url = audio_formats[0]["url"]
            else:
                for fmt in formats:
                    if fmt.get("url") and fmt.get("acodec") not in (None, "none"):
                        audio_url = fmt["url"]
                        break

            if not audio_url:
                if data.get("url"):
                    audio_url = data["url"]
                else:
                    raise Exception("No playable audio format found.")

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
            return await interaction.response.send_message(
                "❌ No previous song available.",
                ephemeral=True
            )

        current_song = history.pop()
        previous_song = history.pop()

        queue.insert(0, previous_song)
        queue.insert(1, current_song)

        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()

        await interaction.response.send_message(
            "⏮ Playing previous song...",
            ephemeral=True
        )

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message(
                "❌ Bot is not connected.",
                ephemeral=True
            )

        if vc.is_playing():
            vc.pause()
            return await interaction.response.send_message(
                "⏸ Music paused.",
                ephemeral=True
            )

        if vc.is_paused():
            vc.resume()
            return await interaction.response.send_message(
                "▶ Music resumed.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.success)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            return await interaction.response.send_message(
                "⏭ Skipped current song.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global loop_enabled

        loop_enabled = not loop_enabled
        state = "enabled 🔁" if loop_enabled else "disabled"

        await interaction.response.send_message(
            f"Loop {state}.",
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
        embed = discord.Embed(
            title="❌ Failed To Play Song",
            description=f"```{e}```",
            color=0xFF0000
        )
        await ctx.send(embed=embed)

        if queue:
            await play_next(ctx)
        return

    current_player = player

    def after_play(error):
        future = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
        try:
            future.result()
        except:
            pass

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

    embed.set_footer(text="Use the buttons below to control the player")

    await ctx.send(embed=embed, view=MusicControls(ctx))


# ---------- READY ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play music"
        )
    )


# ---------- VOICE HELPER ----------
async def ensure_voice(ctx):
    if not ctx.author.voice:
        embed = discord.Embed(
            description="❌ Join a voice channel first.",
            color=0xFF0000
        )
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

    embed = discord.Embed(
        description=f"➕ Added to queue: `{query}`",
        color=0x2B2D31
    )
    await ctx.send(embed=embed)

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command()
async def p(ctx, *, query):
    await play(ctx, query=query)


@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        return await ctx.send(
            embed=discord.Embed(
                description="📭 Queue is empty.",
                color=0x2B2D31
            )
        )

    desc = "\n".join(
        f"`{i+1}.` {song}" for i, song in enumerate(queue[:10])
    )

    embed = discord.Embed(
        title="📜 Current Queue",
        description=desc,
        color=0x2B2D31
    )

    await ctx.send(embed=embed)


@bot.command()
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send(embed=discord.Embed(description="⏭ Skipped.", color=0x2B2D31))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Nothing is playing.", color=0xFF0000))


@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send(embed=discord.Embed(description="⏸ Paused.", color=0x2B2D31))


@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send(embed=discord.Embed(description="▶ Resumed.", color=0x2B2D31))


@bot.command()
async def stop(ctx):
    queue.clear()

    if ctx.voice_client:
        ctx.voice_client.stop()

    await ctx.send(embed=discord.Embed(
        description="⏹ Queue cleared and playback stopped.",
        color=0x2B2D31
    ))


@bot.command()
async def leave(ctx):
    queue.clear()
    history.clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()

    await ctx.send(embed=discord.Embed(
        description="👋 Disconnected.",
        color=0x2B2D31
    ))


@bot.command()
async def volume(ctx, vol: int):
    if not ctx.voice_client or not ctx.voice_client.source:
        return await ctx.send(embed=discord.Embed(
            description="❌ Nothing is playing.",
            color=0xFF0000
        ))

    vol = max(0, min(vol, 100))
    ctx.voice_client.source.volume = vol / 100

    await ctx.send(embed=discord.Embed(
        description=f"🔊 Volume set to `{vol}%`",
        color=0x2B2D31
    ))


@bot.command(aliases=["np"])
async def nowplaying(ctx):
    if not current_player:
        return await ctx.send(embed=discord.Embed(
            description="❌ Nothing is currently playing.",
            color=0xFF0000
        ))

    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{current_player.title}**",
        color=0x2B2D31
    )

    if current_player.thumbnail:
        embed.set_thumbnail(url=current_player.thumbnail)

    await ctx.send(embed=embed)


@bot.command()
async def shuffle(ctx):
    if not queue:
        return await ctx.send(embed=discord.Embed(
            description="📭 Queue is empty.",
            color=0xFF0000
        ))

    random.shuffle(queue)

    await ctx.send(embed=discord.Embed(
        description="🔀 Queue shuffled.",
        color=0x2B2D31
    ))


@bot.command(name="history")
async def history_command(ctx):
    if not history:
        return await ctx.send(embed=discord.Embed(
            description="📭 No songs played yet.",
            color=0x2B2D31
        ))

    desc = "\n".join(
        f"`{i+1}.` {song}" for i, song in enumerate(history[-10:][::-1])
    )

    await ctx.send(embed=discord.Embed(
        title="🕘 Recently Played",
        description=desc,
        color=0x2B2D31
    ))


@bot.command()
async def loop(ctx):
    global loop_enabled

    loop_enabled = not loop_enabled

    await ctx.send(embed=discord.Embed(
        description=f"🔁 Loop {'enabled' if loop_enabled else 'disabled'}.",
        color=0x2B2D31
    ))


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 Music Commands",
        color=0x2B2D31
    )

    embed.add_field(
        name="Playback",
        value=(
            "`!play <song>`\n"
            "`!p <song>`\n"
            "`!skip`\n"
            "`!pause`\n"
            "`!resume`\n"
            "`!stop`\n"
            "`!leave`"
        ),
        inline=False
    )

    embed.add_field(
        name="Queue",
        value=(
            "`!queue`\n"
            "`!shuffle`\n"
            "`!history`\n"
            "`!loop`"
        ),
        inline=False
    )

    embed.add_field(
        name="Other",
        value=(
            "`!volume <0-100>`\n"
            "`!nowplaying` / `!np`"
        ),
        inline=False
    )

    await ctx.send(embed=embed)


bot.run(TOKEN)
