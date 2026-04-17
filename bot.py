import os
import asyncio
import random
import time
import discord
import wavelink

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


# ---------- LAVALINK CONNECT ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play music"
        )
    )

    # Prevent reconnect spam
    if not wavelink.Pool.nodes:
        node = wavelink.Node(
            uri="https://YOUR-RAILWAY-URL.up.railway.app",
            password="youshallnotpass"
        )
        await wavelink.Pool.connect(nodes=[node], client=bot)


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

        vc = interaction.guild.voice_client
        if vc:
            vc.stop()

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
        tracks = await wavelink.YouTubeTrack.search(next_song)

        if not tracks:
            await ctx.send("❌ Song not found.")
            return

        track = tracks[0]
        current_player = track

    except Exception as e:
        await ctx.send(f"❌ Error: `{e}`")
        return

    def after_play(error):
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    vc = ctx.voice_client
    await vc.play(track, after=after_play)

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=(
            f"**{track.title}**\n\n"
            f"👤 Requested by: {ctx.author.mention}"
        ),
        color=0x2B2D31
    )

    if getattr(track, "thumbnail", None):
        embed.set_thumbnail(url=track.thumbnail)

    await ctx.send(embed=embed, view=MusicControls(ctx))


# ---------- VOICE HELPER ----------
async def ensure_voice(ctx):
    if not ctx.author.voice:
        await ctx.send(embed=discord.Embed(
            description="❌ Join a voice channel first.",
            color=0xFF0000
        ))
        return False

    channel = ctx.author.voice.channel

    if not ctx.voice_client:
        await channel.connect(cls=wavelink.Player)
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    return True


# ---------- COMMANDS ----------
@bot.command()
async def play(ctx, *, query):
    if not await ensure_voice(ctx):
        return

    queue.append(query)

    await ctx.send(embed=discord.Embed(
        description=f"➕ Added to queue: `{query}`",
        color=0x2B2D31
    ))

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command()
async def p(ctx, *, query):
    await play(ctx, query=query)


@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        return await ctx.send(embed=discord.Embed(
            description="📭 Queue is empty.",
            color=0x2B2D31
        ))

    desc = "\n".join(f"`{i+1}.` {song}" for i, song in enumerate(queue[:10]))

    await ctx.send(embed=discord.Embed(
        title="📜 Current Queue",
        description=desc,
        color=0x2B2D31
    ))


@bot.command()
async def skip(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send("⏭ Skipped.")


@bot.command()
async def pause(ctx):
    if ctx.voice_client:
        ctx.voice_client.pause()
        await ctx.send("⏸ Paused.")


@bot.command()
async def resume(ctx):
    if ctx.voice_client:
        ctx.voice_client.resume()
        await ctx.send("▶ Resumed.")


@bot.command()
async def stop(ctx):
    queue.clear()
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.send("⏹ Stopped.")


@bot.command()
async def leave(ctx):
    queue.clear()
    history.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("👋 Left voice channel.")


@bot.command()
async def volume(ctx, vol: int):
    vol = max(0, min(vol, 100))

    if ctx.voice_client:
        try:
            await ctx.voice_client.set_volume(vol)
        except:
            pass

    await ctx.send(f"🔊 Volume set to {vol}%")


@bot.command(aliases=["np"])
async def nowplaying(ctx):
    if not current_player:
        return await ctx.send("❌ Nothing playing.")

    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{current_player.title}**",
        color=0x2B2D31
    )

    await ctx.send(embed=embed)


@bot.command()
async def shuffle(ctx):
    random.shuffle(queue)
    await ctx.send("🔀 Shuffled queue.")


@bot.command()
async def loop(ctx):
    global loop_enabled
    loop_enabled = not loop_enabled
    await ctx.send(f"🔁 Loop {'enabled' if loop_enabled else 'disabled'}")


@bot.command()
async def history(ctx):
    if not history:
        return await ctx.send("📭 No history.")

    desc = "\n".join(f"`{i+1}.` {s}" for i, s in enumerate(history[-10:][::-1]))

    await ctx.send(embed=discord.Embed(
        title="🕘 History",
        description=desc,
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
        value="`!play` `!skip` `!pause` `!resume` `!stop` `!leave`",
        inline=False
    )

    embed.add_field(
        name="Queue",
        value="`!queue` `!shuffle` `!history` `!loop`",
        inline=False
    )

    await ctx.send(embed=embed)


# ---------- RUN ----------
bot.run(TOKEN)
