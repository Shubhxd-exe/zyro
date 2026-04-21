import os
import random
import discord
import wavelink

from dotenv import load_dotenv
from discord.ext import commands
from discord.ui import View, button

# ─────────────────────────────────────────────
# ENVIRONMENT
# ─────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env")

# ─────────────────────────────────────────────
# BOT SETUP
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

song_queue: dict[int, list] = {}
loop_mode: dict[int, bool] = {}


# ─────────────────────────────────────────────
# CONTROL BUTTONS
# ─────────────────────────────────────────────
class MusicControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @button(label="⏯", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, _):
        player: wavelink.Player = interaction.guild.voice_client

        if not player:
            return await interaction.response.send_message("❌ Not connected.", ephemeral=True)

        if player.paused:
            await player.pause(False)
            return await interaction.response.send_message("▶ Resumed.", ephemeral=True)

        await player.pause(True)
        await interaction.response.send_message("⏸ Paused.", ephemeral=True)

    @button(label="⏭", style=discord.ButtonStyle.success)
    async def skip(self, interaction: discord.Interaction, _):
        player: wavelink.Player = interaction.guild.voice_client

        if not player or not player.current:
            return await interaction.response.send_message("❌ Nothing is playing.", ephemeral=True)

        await player.skip(force=True)
        await interaction.response.send_message("⏭ Skipped.", ephemeral=True)

    @button(label="🔁", style=discord.ButtonStyle.secondary)
    async def loop(self, interaction: discord.Interaction, _):
        gid = interaction.guild.id
        loop_mode[gid] = not loop_mode.get(gid, False)

        await interaction.response.send_message(
            f"🔁 Loop {'enabled' if loop_mode[gid] else 'disabled'}.",
            ephemeral=True
        )


# ─────────────────────────────────────────────
# READY + LAVALINK
# ─────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play"
        )
    )

    if not wavelink.Pool.nodes:
        await wavelink.Pool.connect(
            nodes=[
                wavelink.Node(
                    uri="ws://paid1.spidercloud.fun:25575",
                    password="lavalinknode88"
                )
            ],
            client=bot
        )


# ─────────────────────────────────────────────
# TRACK END EVENT
# ─────────────────────────────────────────────
@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player: wavelink.Player = payload.player
    guild_id = player.guild.id

    if loop_mode.get(guild_id, False) and payload.track:
        await player.play(payload.track)
        return

    queue = song_queue.get(guild_id, [])

    if queue:
        next_track = queue.pop(0)
        await player.play(next_track)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
async def get_player(ctx) -> wavelink.Player | None:
    if not ctx.author.voice:
        await ctx.send("❌ Join a voice channel first.")
        return None

    player: wavelink.Player = ctx.voice_client

    if not player:
        player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
    elif player.channel != ctx.author.voice.channel:
        await player.move_to(ctx.author.voice.channel)

    return player


# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────
@bot.command(aliases=["p"])
async def play(ctx, *, query: str):
    player = await get_player(ctx)

    if not player:
        return

    results = await wavelink.Playable.search(query)

    if not results:
        return await ctx.send("❌ No results found.")

    if isinstance(results, wavelink.Playlist):
        track = results.tracks[0]
    else:
        track = results[0]

    guild_queue = song_queue.setdefault(ctx.guild.id, [])

    if player.current:
        guild_queue.append(track)
        return await ctx.send(f"➕ Added to queue: **{track.title}**")

    await player.play(track)

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**{track.title}**\n\nRequested by {ctx.author.mention}",
        color=0x2B2D31
    )

    artwork = getattr(track, "artwork", None)
    if artwork:
        embed.set_thumbnail(url=artwork)

    await ctx.send(embed=embed, view=MusicControls())


@bot.command()
async def skip(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player or not player.current:
        return await ctx.send("❌ Nothing is playing.")

    await player.skip(force=True)
    await ctx.send("⏭ Skipped.")


@bot.command()
async def pause(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player:
        return await ctx.send("❌ Not connected.")

    await player.pause(True)
    await ctx.send("⏸ Paused.")


@bot.command()
async def resume(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player:
        return await ctx.send("❌ Not connected.")

    await player.pause(False)
    await ctx.send("▶ Resumed.")


@bot.command()
async def stop(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player:
        return await ctx.send("❌ Not connected.")

    song_queue[ctx.guild.id] = []
    await player.stop()
    await ctx.send("⏹ Stopped and cleared queue.")


@bot.command()
async def leave(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player:
        return await ctx.send("❌ Not connected.")

    song_queue.pop(ctx.guild.id, None)
    loop_mode.pop(ctx.guild.id, None)

    await player.disconnect()
    await ctx.send("👋 Disconnected.")


@bot.command(name="queue")
async def queue_cmd(ctx):
    queue = song_queue.get(ctx.guild.id, [])

    if not queue:
        return await ctx.send("📭 Queue is empty.")

    description = "\n".join(
        f"`{i + 1}.` {track.title}"
        for i, track in enumerate(queue[:10])
    )

    embed = discord.Embed(
        title="📜 Queue",
        description=description,
        color=0x2B2D31
    )

    await ctx.send(embed=embed)


@bot.command()
async def shuffle(ctx):
    queue = song_queue.get(ctx.guild.id, [])

    if not queue:
        return await ctx.send("📭 Queue is empty.")

    random.shuffle(queue)
    await ctx.send("🔀 Queue shuffled.")


@bot.command()
async def volume(ctx, amount: int):
    player: wavelink.Player = ctx.voice_client

    if not player:
        return await ctx.send("❌ Not connected.")

    amount = max(0, min(amount, 100))
    await player.set_volume(amount)

    await ctx.send(f"🔊 Volume set to {amount}%")


@bot.command(aliases=["np"])
async def nowplaying(ctx):
    player: wavelink.Player = ctx.voice_client

    if not player or not player.current:
        return await ctx.send("❌ Nothing is playing.")

    track = player.current

    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{track.title}**",
        color=0x2B2D31
    )

    artwork = getattr(track, "artwork", None)
    if artwork:
        embed.set_thumbnail(url=artwork)

    await ctx.send(embed=embed)


@bot.command()
async def loop(ctx):
    gid = ctx.guild.id
    loop_mode[gid] = not loop_mode.get(gid, False)

    await ctx.send(f"🔁 Loop {'enabled' if loop_mode[gid] else 'disabled'}")


@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 Music Commands",
        color=0x2B2D31
    )

    embed.add_field(
        name="Playback",
        value="`!play` `!pause` `!resume` `!skip` `!stop` `!leave`",
        inline=False
    )

    embed.add_field(
        name="Queue",
        value="`!queue` `!shuffle` `!loop` `!volume` `!nowplaying`",
        inline=False
    )

    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────
bot.run(TOKEN)
