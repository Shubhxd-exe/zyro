import os
import asyncio
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

# ---------- YTDL ----------
ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
    "cookiefile": "cookies.txt",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    }
}

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
        self.duration = data.get("duration_string", "Unknown")

    @classmethod
    async def from_query(cls, query):
        loop = asyncio.get_event_loop()

        if not query.startswith(("http://", "https://")):
            query = f"ytsearch:{query}"

        try:
            data = await loop.run_in_executor(
                None,
                lambda: ytdl.extract_info(query, download=False)
            )

            if data is None:
                raise Exception("No data returned from YouTube.")

            # If search result
            if "entries" in data:
                entries = data.get("entries")
                if not entries:
                    raise Exception("No song found.")
                data = entries[0]

            if not data:
                raise Exception("No song found.")

            audio_url = None

            # Try direct URL first
            if data.get("url"):
                audio_url = data["url"]

            # Otherwise scan formats for a valid audio stream
            if not audio_url:
                for fmt in data.get("formats", []):
                    if (
                        fmt.get("url")
                        and fmt.get("acodec") != "none"
                    ):
                        audio_url = fmt["url"]

                # fallback to any url if still not found
                if not audio_url:
                    for fmt in data.get("formats", []):
                        if fmt.get("url"):
                            audio_url = fmt["url"]

            if not audio_url:
                raise Exception("No playable audio format found for this video.")

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


# ---------- PLAY NEXT ----------
async def play_next(ctx):
    if not queue:
        return

    next_song = queue.pop(0)
    history.append(next_song)

    try:
        player = await YTDLSource.from_query(next_song)

    except Exception as e:
        embed = discord.Embed(
            description=f"❌ Failed to play: `{next_song}`\n```{e}```",
            color=0xFF0000
        )
        await ctx.send(embed=embed)

        # Continue with next song instead of stopping bot
        if queue:
            await play_next(ctx)
        return

    def after_play(error):
        if error:
            print(f"Playback error: {error}")

        future = asyncio.run_coroutine_threadsafe(
            play_next(ctx),
            bot.loop
        )

        try:
            future.result()
        except Exception as e:
            print(f"Queue error: {e}")

    ctx.voice_client.play(player, after=after_play)

    embed = discord.Embed(
        description=(
            f"💜 **Now Playing**\n\n"
            f"**{player.title}**\n\n"
            f"⏱️ Duration: `{player.duration}`\n"
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


# ---------- VOICE HELPER ----------
async def ensure_voice(ctx):
    if ctx.author.voice is None:
        embed = discord.Embed(
            description="❌ You need to join a voice channel first.",
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
        embed = discord.Embed(
            description="📭 Queue is empty.",
            color=0x2B2D31
        )
        return await ctx.send(embed=embed)

    description = "\n".join(
        f"`{i + 1}.` {song}"
        for i, song in enumerate(queue[:10])
    )

    embed = discord.Embed(
        title="📜 Current Queue",
        description=description,
        color=0x2B2D31
    )

    if len(queue) > 10:
        embed.set_footer(text=f"And {len(queue) - 10} more songs...")

    await ctx.send(embed=embed)


@bot.command()
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()

        embed = discord.Embed(
            description="⏭ Skipped current song.",
            color=0x2B2D31
        )
    else:
        embed = discord.Embed(
            description="❌ Nothing is playing.",
            color=0xFF0000
        )

    await ctx.send(embed=embed)


@bot.command()
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()

        embed = discord.Embed(
            description="⏸ Music paused.",
            color=0x2B2D31
        )
    else:
        embed = discord.Embed(
            description="❌ Nothing is playing.",
            color=0xFF0000
        )

    await ctx.send(embed=embed)


@bot.command()
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()

        embed = discord.Embed(
            description="▶ Music resumed.",
            color=0x2B2D31
        )
    else:
        embed = discord.Embed(
            description="❌ Nothing is paused.",
            color=0xFF0000
        )

    await ctx.send(embed=embed)


@bot.command()
async def stop(ctx):
    queue.clear()

    if ctx.voice_client:
        ctx.voice_client.stop()

    embed = discord.Embed(
        description="⏹ Music stopped and queue cleared.",
        color=0x2B2D31
    )
    await ctx.send(embed=embed)


@bot.command()
async def leave(ctx):
    queue.clear()
    history.clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()

    embed = discord.Embed(
        description="👋 Left the voice channel.",
        color=0x2B2D31
    )
    await ctx.send(embed=embed)


bot.run(TOKEN)
