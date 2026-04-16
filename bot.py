import os
from dotenv import load_dotenv

import discord
from discord.ext import commands
from discord.ui import View
import yt_dlp
import asyncio

# Load .env file
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- YTDL / FFMPEG ----------
ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "ytsearch",
}

ffmpeg_options = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# ---------- MUSIC STATE ----------
queue = []
history = []


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data
        self.title = data.get("title")
        self.url = data.get("webpage_url")
        self.thumbnail = data.get("thumbnail")
        self.duration = data.get("duration_string", "Unknown")

    @classmethod
    async def from_url(cls, query, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()

        if not query.startswith("http"):
            query = f"ytsearch:{query}"

        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(query, download=not stream)
        )

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)

        return cls(
            discord.FFmpegPCMAudio(filename, **ffmpeg_options),
            data=data
        )


# ---------- BUTTONS ----------
class MusicControls(View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="⏮ Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(history) < 2:
            await interaction.response.send_message(
                "❌ No previous song available.",
                ephemeral=True
            )
            return

        current = history.pop()
        previous = history.pop()

        queue.insert(0, current)
        queue.insert(0, previous)

        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()

        await interaction.response.send_message(
            "⏮ Playing previous song...",
            ephemeral=True
        )

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if vc is None:
            await interaction.response.send_message(
                "❌ Bot is not connected.",
                ephemeral=True
            )
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message(
                "⏸ Music paused.",
                ephemeral=True
            )
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message(
                "▶ Music resumed.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Nothing is playing.",
                ephemeral=True
            )

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.success)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(
                "⏭ Skipped current song.",
                ephemeral=True
            )
        else:
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

    player = await YTDLSource.from_url(
        next_song,
        loop=bot.loop,
        stream=True
    )

    def after_playing(error):
        if error:
            print(f"Player error: {error}")

        future = asyncio.run_coroutine_threadsafe(
            play_next(ctx),
            bot.loop
        )

        try:
            future.result()
        except Exception as e:
            print(e)

    ctx.voice_client.play(player, after=after_playing)

    embed = discord.Embed(
        description=f"""💜 **Now Playing**

**{player.title}**

⏱️ Duration: `{player.duration}`
👤 Requested by {ctx.author.mention}
🔗 [Open Song]({player.url})""",
        color=0x2B2D31
    )

    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)

    embed.set_footer(text="Use the buttons below to control the music")

    await ctx.send(
        embed=embed,
        view=MusicControls(ctx)
    )


# ---------- EVENTS ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


# ---------- COMMANDS ----------
@bot.command()
async def play(ctx, *, query):
    if ctx.author.voice is None:
        embed = discord.Embed(
            description="❌ You need to join a voice channel first.",
            color=0xFF0000
        )
        return await ctx.send(embed=embed)

    voice_channel = ctx.author.voice.channel

    if ctx.voice_client is None:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)

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
    if ctx.author.voice is None:
        embed = discord.Embed(
            description="❌ You need to join a voice channel first.",
            color=0xFF0000
        )
        return await ctx.send(embed=embed)

    voice_channel = ctx.author.voice.channel

    if ctx.voice_client is None:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)

    queue.append(query)

    embed = discord.Embed(
        description=f"➕ Added to queue: `{query}`",
        color=0x2B2D31
    )
    await ctx.send(embed=embed)

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        embed = discord.Embed(
            description="📭 Queue is empty.",
            color=0x2B2D31
        )
        return await ctx.send(embed=embed)

    description = "\n".join(
        f"`{index + 1}.` {song}"
        for index, song in enumerate(queue[:10])
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


if TOKEN is None:
    raise ValueError("DISCORD_TOKEN not found in .env file")

bot.run(TOKEN)