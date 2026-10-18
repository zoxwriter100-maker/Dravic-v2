import os
import asyncio
from collections import defaultdict, deque
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

queues = defaultdict(deque)
now_playing = {}

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "extract_flat": False,
}
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

def get_track(query: str):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info:
            raise ValueError("No results found.")
        return {
            "title": info.get("title", "Unknown title"),
            "url": info.get("url"),
            "webpage_url": info.get("webpage_url", ""),
            "duration": info.get("duration"),
        }

def fmt_duration(seconds):
    if not seconds:
        return "Live / Unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

async def start_next(guild: discord.Guild):
    voice = guild.voice_client
    if not voice or voice.is_playing() or voice.is_paused():
        return
    if not queues[guild.id]:
        now_playing.pop(guild.id, None)
        return
    track = queues[guild.id].popleft()
    now_playing[guild.id] = track
    source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
    source = discord.PCMVolumeTransformer(source, volume=0.5)
    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        bot.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(start_next(guild))
        )
    voice.play(source, after=after_play)

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Command sync failed: {exc}")
    print(f"Logged in as {bot.user} — Dravixplayz Bot | Private V2")

@bot.tree.command(name="play", description="Play a song by name or YouTube URL")
@app_commands.describe(song="Song name or YouTube URL")
async def play(interaction: discord.Interaction, song: str):
    await interaction.response.defer(thinking=True)
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("❌ Pehle kisi voice channel mein join karo.")
        return
    try:
        track = await asyncio.to_thread(get_track, song)
    except Exception as exc:
        await interaction.followup.send(f"❌ Song search nahi ho saka: {exc}")
        return

    guild = interaction.guild
    if not guild.voice_client:
        try:
            await interaction.user.voice.channel.connect()
        except Exception as exc:
            await interaction.followup.send(f"❌ Voice channel join nahi kar saka: {exc}")
            return
    elif guild.voice_client.channel != interaction.user.voice.channel:
        await guild.voice_client.move_to(interaction.user.voice.channel)

    queues[guild.id].append(track)
    if not guild.voice_client.is_playing() and not guild.voice_client.is_paused():
        await start_next(guild)
        await interaction.followup.send(
            f"🎶 **Now playing:** [{track['title']}]({track['webpage_url']})"
        )
    else:
        await interaction.followup.send(
            f"➕ Queue mein add kiya: [{track['title']}]({track['webpage_url']})"
        )

@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if not voice or not (voice.is_playing() or voice.is_paused()):
        await interaction.response.send_message("❌ Abhi koi song nahi chal raha.")
        return
    voice.stop()
    await interaction.response.send_message("⏭️ Song skipped.")

@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if voice and voice.is_playing():
        voice.pause()
        await interaction.response.send_message("⏸️ Music paused.")
    else:
        await interaction.response.send_message("❌ Koi song play nahi ho raha.")

@bot.tree.command(name="resume", description="Resume music")
async def resume(interaction: discord.Interaction):
    voice = interaction.guild.voice_client
    if voice and voice.is_paused():
        voice.resume()
        await interaction.response.send_message("▶️ Music resumed.")
    else:
        await interaction.response.send_message("❌ Music paused nahi hai.")

@bot.tree.command(name="stop", description="Stop music and clear the queue")
async def stop(interaction: discord.Interaction):
    guild = interaction.guild
    queues[guild.id].clear()
    voice = guild.voice_client
    if voice:
        voice.stop()
    now_playing.pop(guild.id, None)
    await interaction.response.send_message("⏹️ Music stopped aur queue clear kar di.")

@bot.tree.command(name="queue", description="Show the upcoming songs")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    upcoming = list(queues[guild_id])[:10]
    current = now_playing.get(guild_id)
    lines = []
    if current:
        lines.append(f"**Now:** {current['title']}")
    if upcoming:
        lines.extend(f"`{i}.` {t['title']}" for i, t in enumerate(upcoming, 1))
    if not lines:
        await interaction.response.send_message("Queue khaali hai.")
        return
    embed = discord.Embed(title="🎵 Dravixplayz Music Queue", description="\n".join(lines), color=0x8B5CF6)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="Show the current song")
async def nowplaying(interaction: discord.Interaction):
    track = now_playing.get(interaction.guild_id)
    if not track:
        await interaction.response.send_message("Abhi koi song nahi chal raha.")
        return
    embed = discord.Embed(
        title="🎧 Now Playing",
        description=f"[{track['title']}]({track['webpage_url']})",
        color=0x06B6D4
    )
    embed.add_field(name="Duration", value=fmt_duration(track["duration"]))
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="volume", description="Set music volume from 1 to 100")
@app_commands.describe(level="Volume level (1-100)")
async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]):
    voice = interaction.guild.voice_client
    if not voice or not voice.source:
        await interaction.response.send_message("❌ Abhi music play nahi ho raha.")
        return
    if isinstance(voice.source, discord.PCMVolumeTransformer):
        voice.source.volume = level / 100
        await interaction.response.send_message(f"🔊 Volume set to **{level}%**.")
    else:
        await interaction.response.send_message("Volume control unavailable for this audio source.")

@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    guild = interaction.guild
    queues[guild.id].clear()
    now_playing.pop(guild.id, None)
    if guild.voice_client:
        await guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Voice channel se disconnect ho gaya.")
    else:
        await interaction.response.send_message("Main kisi voice channel mein nahi hoon.")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing. Add your bot token to the .env file.")
bot.run(TOKEN)
