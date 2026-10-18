import asyncio
import base64
import json
import os
import random
import re
import threading
import time
from collections import defaultdict, deque
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()

intents = discord.Intents.default()
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)

queues = defaultdict(deque)
now_playing = {}
_spotify_token = {"value": None, "expires_at": 0}
_spotify_token_lock = threading.Lock()

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


def parse_spotify_track_id(query):
    value = query.strip()
    if value.lower().startswith("spotify:"):
        parts = value.split(":")
        if len(parts) != 3 or parts[1].lower() != "track":
            raise ValueError("Use a Spotify link to one track; album and playlist links are not supported yet.")
        track_id = parts[2]
    else:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return None
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        host = (parsed.hostname or "").lower()
        if host in ("spotify.link", "www.spotify.link"):
            raise ValueError("Open the short link in Spotify, then paste the full open.spotify.com track link.")
        if host != "open.spotify.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        track_id = None
        for index, part in enumerate(parts):
            kind = part.lower()
            if kind in ("album", "playlist"):
                raise ValueError("Use a Spotify link to one track; album and playlist links are not supported yet.")
            if kind == "track":
                if index + 1 >= len(parts):
                    break
                track_id = parts[index + 1]
                break
        if track_id is None:
            raise ValueError("That Spotify URL is not a single-track link.")

    if not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
        raise ValueError("That Spotify track link has an invalid track ID.")
    return track_id


def spotify_access_token():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise ValueError("Spotify links need SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET configured in the environment.")

    now = time.monotonic()
    if _spotify_token["value"] and now < _spotify_token["expires_at"]:
        return _spotify_token["value"]

    with _spotify_token_lock:
        now = time.monotonic()
        if _spotify_token["value"] and now < _spotify_token["expires_at"]:
            return _spotify_token["value"]
        credentials = base64.b64encode(
            f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            "https://accounts.spotify.com/api/token",
            data=urlencode({"grant_type": "client_credentials"}).encode("ascii"),
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise ValueError("Spotify authentication failed. Check the Spotify app credentials in the environment.") from None

        token = payload.get("access_token")
        if not token:
            raise ValueError("Spotify did not return an access token. Check the Spotify app configuration.")
        expires_in = int(payload.get("expires_in", 3600))
        _spotify_token["value"] = token
        _spotify_token["expires_at"] = time.monotonic() + max(0, expires_in - 60)
        return token


def get_spotify_track(track_id):
    request = Request(
        f"https://api.spotify.com/v1/tracks/{track_id}",
        headers={"Authorization": f"Bearer {spotify_access_token()}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise ValueError("Spotify could not find that track.") from None
        if exc.code == 429:
            raise ValueError("Spotify is rate-limiting requests; try again in a moment.") from None
        if exc.code in (401, 403):
            raise ValueError("Spotify rejected the request. Check the Spotify app credentials and API access.") from None
        raise ValueError(f"Spotify lookup failed (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        raise ValueError("Could not reach Spotify right now; try again shortly.") from None

    name = data.get("name")
    artists = [artist.get("name", "") for artist in data.get("artists", []) if artist.get("name")]
    if not name:
        raise ValueError("Spotify returned incomplete track details.")
    return {
        "title": name,
        "artist": ", ".join(artists),
        "spotify_url": data.get("external_urls", {}).get("spotify") or f"https://open.spotify.com/track/{track_id}",
        "duration": (data.get("duration_ms") or 0) / 1000 or None,
    }


def get_track(query):
    spotify_id = parse_spotify_track_id(query)
    spotify_track = get_spotify_track(spotify_id) if spotify_id else None
    search_query = query
    if spotify_track:
        search_query = f"ytsearch1:{spotify_track['title']} {spotify_track['artist']} official audio"

    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(search_query, download=False)
        if info and "entries" in info:
            info = next((entry for entry in info["entries"] if entry), None)
        if not info or not info.get("url"):
            raise ValueError("No playable audio result found.")

    track = {
        "title": spotify_track["title"] if spotify_track else info.get("title", "Unknown title"),
        "artist": spotify_track["artist"] if spotify_track else "",
        "url": info.get("url"),
        "webpage_url": info.get("webpage_url", ""),
        "duration": (spotify_track.get("duration") if spotify_track else None) or info.get("duration"),
        "spotify_url": spotify_track.get("spotify_url") if spotify_track else None,
        "resolved_at": time.monotonic(),
    }
    return track


def refresh_audio_url(track):
    page_url = track.get("webpage_url")
    if not page_url:
        raise ValueError("The selected result has no refreshable video page URL.")
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(page_url, download=False)
    if not info or not info.get("url"):
        raise ValueError("Could not refresh the audio stream.")
    refreshed = dict(track)
    refreshed["url"] = info["url"]
    refreshed["resolved_at"] = time.monotonic()
    if not refreshed.get("duration"):
        refreshed["duration"] = info.get("duration")
    return refreshed


def display_track(track):
    title = track.get("title", "Unknown title")
    artist = track.get("artist")
    return f"{title} — {artist}" if artist else title


def fmt_duration(seconds):
    if not seconds:
        return "Live / Unknown"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


async def start_next(guild):
    voice = guild.voice_client
    if not voice or voice.is_playing() or voice.is_paused():
        return

    while queues[guild.id]:
        track = queues[guild.id].popleft()
        try:
            if time.monotonic() - track.get("resolved_at", 0) > 120:
                track = await asyncio.to_thread(refresh_audio_url, track)
            source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(source, volume=0.5)
            now_playing[guild.id] = track

            def after_play(error):
                if error:
                    print(f"Playback error: {error}")
                try:
                    bot.loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(start_next(guild))
                    )
                except RuntimeError:
                    pass

            voice.play(source, after=after_play)
            return
        except Exception as exc:
            now_playing.pop(guild.id, None)
            print(f"Skipping an unplayable track ({type(exc).__name__}).")

    now_playing.pop(guild.id, None)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Command sync failed: {exc}")
    print(f"Logged in as {bot.user} — Dravix V2 music bot is online.")


@bot.tree.command(name="play", description="Play a song search, YouTube URL, or Spotify track link")
@app_commands.describe(song="Song name, YouTube URL, or Spotify single-track URL")
async def play(interaction: discord.Interaction, song: str):
    await interaction.response.defer(thinking=True)
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("Join a voice channel first.")
        return
    try:
        track = await asyncio.to_thread(get_track, song)
    except Exception as exc:
        await interaction.followup.send(f"Could not find that song: {exc}")
        return

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("Use this command in a server voice channel.")
        return
    if not guild.voice_client:
        try:
            await interaction.user.voice.channel.connect()
        except Exception:
            await interaction.followup.send("I could not join that voice channel. Check my Connect and Speak permissions.")
            return
    elif guild.voice_client.channel != interaction.user.voice.channel:
        await guild.voice_client.move_to(interaction.user.voice.channel)

    queues[guild.id].append(track)
    if not guild.voice_client.is_playing() and not guild.voice_client.is_paused():
        await start_next(guild)
        if now_playing.get(guild.id) == track:
            if track.get("spotify_url"):
                await interaction.followup.send(
                    f"Now playing (YouTube audio match): **{display_track(track)}** · [Spotify track]({track['spotify_url']})"
                )
            else:
                await interaction.followup.send(
                    f"Now playing: [{display_track(track)}]({track['webpage_url']})"
                )
        else:
            await interaction.followup.send("I could not start that audio. Check that FFmpeg is installed and try another result.")
    else:
        if track.get("spotify_url"):
            await interaction.followup.send(
                f"Added to the queue: **{display_track(track)}** · [Spotify track]({track['spotify_url']})"
            )
        else:
            await interaction.followup.send(f"Added to the queue: **{display_track(track)}**")


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if not voice or not (voice.is_playing() or voice.is_paused()):
        await interaction.response.send_message("Nothing is playing right now.")
        return
    voice.stop()
    await interaction.response.send_message("Skipped the current song.")


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if voice and voice.is_playing():
        voice.pause()
        await interaction.response.send_message("Music paused.")
    else:
        await interaction.response.send_message("Nothing is playing right now.")


@bot.tree.command(name="resume", description="Resume music")
async def resume(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if voice and voice.is_paused():
        voice.resume()
        await interaction.response.send_message("Music resumed.")
    else:
        await interaction.response.send_message("Music is not paused.")


@bot.tree.command(name="stop", description="Stop music and clear the queue")
async def stop(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Use this command in a server.")
        return
    queues[guild.id].clear()
    voice = guild.voice_client
    if voice:
        voice.stop()
    now_playing.pop(guild.id, None)
    await interaction.response.send_message("Music stopped and the queue was cleared.")


@bot.tree.command(name="queue", description="Show the current song and upcoming queue")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("Use this command in a server.")
        return
    upcoming = list(queues[guild_id])[:10]
    current = now_playing.get(guild_id)
    lines = []
    if current:
        lines.append(f"Now: {display_track(current)[:180]}")
    if upcoming:
        lines.extend(f"{index}. {display_track(track)[:180]}" for index, track in enumerate(upcoming, 1))
    if not lines:
        await interaction.response.send_message("The queue is empty.")
        return
    embed = discord.Embed(
        title="Dravix V2 Music Queue",
        description="\n".join(lines),
        color=0x8B5CF6,
    )
    embed.set_footer(text=f"Showing up to 10 upcoming tracks · {len(queues[guild_id])} queued")
    await interaction.response.send_message(embed=embed)


async def send_now_playing(interaction):
    track = now_playing.get(interaction.guild_id)
    if not track:
        await interaction.response.send_message("Nothing is playing right now.")
        return
    audio_link = track.get("webpage_url")
    description = f"**{track.get('artist')}**" if track.get("artist") else ""
    if audio_link:
        description = f"{description}\n[Audio match]({audio_link})" if description else f"[Audio match]({audio_link})"
    embed = discord.Embed(
        title=f"Now Playing: {track.get('title', 'Unknown title')}",
        description=description or "Audio is playing.",
        color=0x06B6D4,
    )
    embed.add_field(name="Duration", value=fmt_duration(track.get("duration")), inline=True)
    if track.get("spotify_url"):
        embed.add_field(name="Spotify track", value=f"[Open in Spotify]({track['spotify_url']})", inline=True)
        embed.set_footer(text="Spotify metadata · audio matched from YouTube")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="Show the current song")
async def nowplaying(interaction: discord.Interaction):
    await send_now_playing(interaction)


@bot.tree.command(name="now-playing", description="Show the current song")
async def now_playing_alias(interaction: discord.Interaction):
    await send_now_playing(interaction)


@bot.tree.command(name="volume", description="Set music volume from 1 to 100")
@app_commands.describe(level="Volume level (1-100)")
async def volume(interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]):
    voice = interaction.guild.voice_client if interaction.guild else None
    if not voice or not voice.source:
        await interaction.response.send_message("Nothing is playing right now.")
        return
    if isinstance(voice.source, discord.PCMVolumeTransformer):
        voice.source.volume = level / 100
        await interaction.response.send_message(f"Volume set to {level}%.")
    else:
        await interaction.response.send_message("Volume control is unavailable for this audio source.")


@bot.tree.command(name="clear", description="Clear upcoming songs without stopping the current one")
async def clear_queue(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this command in a server.")
        return
    count = len(queues[interaction.guild_id])
    queues[interaction.guild_id].clear()
    await interaction.response.send_message(f"Cleared {count} queued track(s). The current song was not stopped.")


@bot.tree.command(name="shuffle", description="Shuffle the upcoming queue")
async def shuffle_queue(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this command in a server.")
        return
    queued = list(queues[interaction.guild_id])
    if len(queued) < 2:
        await interaction.response.send_message("Add at least two tracks before shuffling.")
        return
    random.shuffle(queued)
    queues[interaction.guild_id] = deque(queued)
    await interaction.response.send_message(f"Shuffled {len(queued)} queued tracks.")


@bot.tree.command(name="remove", description="Remove a song from the upcoming queue")
@app_commands.describe(position="Queue position, starting at 1")
async def remove_queued_track(interaction: discord.Interaction, position: app_commands.Range[int, 1, 100]):
    if interaction.guild_id is None:
        await interaction.response.send_message("Use this command in a server.")
        return
    queued = queues[interaction.guild_id]
    if position > len(queued):
        await interaction.response.send_message(f"There is no track at queue position {position}.")
        return
    tracks = list(queued)
    track = tracks.pop(position - 1)
    queues[interaction.guild_id] = deque(tracks)
    await interaction.response.send_message(f"Removed **{display_track(track)}** from the queue.")


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Use this command in a server.")
        return
    queues[guild.id].clear()
    now_playing.pop(guild.id, None)
    if guild.voice_client:
        await guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected from voice and cleared the queue.")
    else:
        await interaction.response.send_message("I am not in a voice channel.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN missing. Add your bot token to the .env file or environment.")
    bot.run(TOKEN)
