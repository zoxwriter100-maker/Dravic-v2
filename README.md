# Dravic V2 Music Bot

A Discord slash-command music bot. Use **Spotify track links** with **/play**: Spotify supplies the official track metadata and link, and the bot searches YouTube for a playable audio match. It does not stream Spotify audio into Discord. Album and playlist links are not supported in this version.

## Features

- **/play** accepts a song search, YouTube URL, or Spotify single-track URL/URI.
- **/queue** and **/now-playing** (also **/nowplaying**) show playback status.
- **/pause**, **/resume**, **/skip**, **/stop**, **/volume**, and **/leave** control playback.
- **/clear**, **/shuffle**, and **/remove** manage upcoming tracks.
- The Spotify track is linked in responses for attribution; audio comes from the matched YouTube result.

## Setup

1. Install Python 3.10+ and FFmpeg, and make sure the **ffmpeg** executable is on PATH.
2. Create a Discord application and bot in the Discord Developer Portal. Enable the bot and invite it with the **bot** and **applications.commands** scopes, plus Connect, Speak, Send Messages, and Embed Links permissions.
3. Create an app in the Spotify Developer Dashboard and copy its Client ID and Client Secret.
4. Install dependencies:

   **python -m venv .venv**
   **pip install -r requirements.txt**

5. Copy **.env.example** to **.env** and fill in **DISCORD_TOKEN**, **SPOTIFY_CLIENT_ID**, and **SPOTIFY_CLIENT_SECRET**. Keep **.env** private; it is ignored by Git.
6. Run **python bot.py**. Slash commands sync on startup.

Spotify credentials are only needed when someone uses a Spotify track link. Ordinary song searches and YouTube URLs work without them. For hosted deployments, configure these values as environment secrets instead of committing them.

## Commands

| Command | Action |
| --- | --- |
| **/play <song or link>** | Play a search result, YouTube URL, or Spotify track link |
| **/queue** | Show the current song and up to 10 upcoming tracks |
| **/now-playing** or **/nowplaying** | Show the current track and duration |
| **/skip** | Skip the current track |
| **/pause** and **/resume** | Pause or resume playback |
| **/volume <1-100>** | Change volume |
| **/clear** | Clear upcoming tracks, keeping the current song |
| **/shuffle** | Shuffle upcoming tracks |
| **/remove <position>** | Remove an upcoming track by its queue position |
| **/stop** | Stop playback and clear the queue |
| **/leave** | Disconnect from voice and clear the queue |
