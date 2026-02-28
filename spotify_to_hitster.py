#!/usr/bin/env python3
"""
Convert a Spotify playlist to a Hitster-compatible JSON file.

For each track, the original release year is looked up via MusicBrainz
rather than relying on Spotify's (often incorrect) release dates.
Covers are filtered out by matching artist names.

Usage:
    python spotify_to_hitster.py <spotify_playlist_url_or_id> [output.json]

Requirements:
    pip install spotipy musicbrainzngs

Credentials: put these in a .env file next to this script (never commit it!):
    SPOTIFY_CLIENT_ID=...
    SPOTIFY_CLIENT_SECRET=...

Setup: In the Spotify Developer Dashboard, add https://localhost:8888/callback
       as a Redirect URI for your app.
"""

import json
import os
import re
import sys
import time

def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())

_load_dotenv()

import musicbrainzngs
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ---------------------------------------------------------------------------
# MusicBrainz setup – identify your app as required by their API policy
# ---------------------------------------------------------------------------
musicbrainzngs.set_useragent("HitsterPlaylistConverter", "1.0", "hitster@example.com")


# ---------------------------------------------------------------------------
# Spotify helpers
# ---------------------------------------------------------------------------

def get_playlist_tracks(playlist_url: str, client_id: str, client_secret: str) -> list[dict]:
    """Return a list of track dicts from a Spotify playlist."""
    cache_path = os.path.join(os.path.dirname(__file__), ".spotify_cache")
    sp = spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri="https://localhost:8888/callback",
            scope="playlist-read-private playlist-read-collaborative",
            cache_path=cache_path,
        )
    )

    # Accept full URLs or bare playlist IDs
    match = re.search(r"playlist/([A-Za-z0-9]+)", playlist_url)
    playlist_id = match.group(1) if match else playlist_url

    tracks = []
    results = sp.playlist_tracks(playlist_id)

    while results:
        for item in results["items"]:
            track = item.get("track")
            if not track or track.get("is_local"):
                continue
            raw_date = track["album"].get("release_date", "")
            tracks.append(
                {
                    "title": track["name"],
                    "artist": track["artists"][0]["name"],
                    "spotify_year": int(raw_date[:4]) if raw_date else None,
                }
            )
        results = sp.next(results) if results.get("next") else None

    return tracks


# ---------------------------------------------------------------------------
# Text normalisation for artist matching
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)          # remove (Remaster), (feat. X), …
    name = re.sub(r"\[.*?\]", "", name)          # remove [Live], …
    name = re.sub(r"\bfeat\..*", "", name)       # remove "feat. someone"
    name = re.sub(r"\bft\..*", "", name)
    name = re.sub(r"[^\w\s]", "", name)          # strip punctuation
    name = re.sub(r"\bthe\b", "", name)          # ignore leading "The"
    return name.strip()


def _artists_match(a: str, b: str) -> bool:
    na, nb = _normalise(a), _normalise(b)
    return na == nb or na in nb or nb in na


# ---------------------------------------------------------------------------
# MusicBrainz helpers
# ---------------------------------------------------------------------------

def _year(date_str: str) -> int | None:
    """Extract a 4-digit year from a MusicBrainz date string."""
    if not date_str:
        return None
    try:
        return int(date_str[:4])
    except ValueError:
        return None


def _mb_call(fn, *args, **kwargs):
    """Call a MusicBrainz API function with automatic rate-limiting (1 req/s)."""
    time.sleep(1.1)
    return fn(*args, **kwargs)


def find_original_year(title: str, artist: str) -> int | None:
    """
    Search MusicBrainz for the earliest official release of a song by the
    given artist.  Returns None if no reliable result is found.
    """
    # --- Step 1: search for recordings ---
    try:
        result = _mb_call(
            musicbrainzngs.search_recordings,
            recording=title,
            artist=artist,
            limit=10,
        )
    except musicbrainzngs.WebServiceError as exc:
        print(f"  [MB] Search failed: {exc}")
        return None

    recordings = result.get("recording-list", [])
    if not recordings:
        print("  [MB] No recordings found")
        return None

    earliest: int | None = None

    for rec in recordings:
        # --- Step 2: filter by artist to avoid covers ---
        credits = rec.get("artist-credit", [])
        mb_artist = next(
            (c["artist"]["name"] for c in credits if isinstance(c, dict) and "artist" in c),
            None,
        )
        if mb_artist and not _artists_match(artist, mb_artist):
            continue  # Different artist → likely a cover, skip

        # --- Step 3: get all releases for this recording ---
        try:
            detail = _mb_call(
                musicbrainzngs.get_recording_by_id,
                rec["id"],
                includes=["releases"],
            )
        except musicbrainzngs.WebServiceError as exc:
            print(f"  [MB] Detail fetch failed: {exc}")
            continue

        releases = detail["recording"].get("release-list", [])
        for release in releases:
            y = _year(release.get("date", ""))
            if y and (earliest is None or y < earliest):
                earliest = y

    return earliest


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        print(
            "ERROR: Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET as environment variables.\n"
            "Create an app at https://developer.spotify.com/dashboard to get them."
        )
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python spotify_to_hitster.py <spotify_playlist_url_or_id> [output.json]")
        sys.exit(1)

    playlist_input = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else "hitster_songs.json"

    print("Fetching Spotify playlist …")
    tracks = get_playlist_tracks(playlist_input, client_id, client_secret)
    print(f"Found {len(tracks)} tracks.\n")
    print("Looking up original release years via MusicBrainz (this takes ~2 s per track) …\n")

    songs = []
    skipped = []

    for i, track in enumerate(tracks, 1):
        title = track["title"]
        artist = track["artist"]
        spotify_year = track["spotify_year"]

        print(f"[{i:>3}/{len(tracks)}] {artist} – {title}  (Spotify: {spotify_year})")

        mb_year = find_original_year(title, artist)

        if mb_year:
            year = mb_year
            source = "MusicBrainz"
        elif spotify_year:
            year = spotify_year
            source = "Spotify (fallback)"
        else:
            year = None
            source = "—"

        if year:
            print(f"         => {year}  [{source}]")
            songs.append([title, artist, year])
        else:
            print(f"         => SKIPPED (no year found)")
            skipped.append(f"{artist} – {title}")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

    print(f"\nDone!  Saved {len(songs)} songs to '{output_file}'.")
    if skipped:
        print(f"\nSkipped {len(skipped)} tracks (no year found):")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
