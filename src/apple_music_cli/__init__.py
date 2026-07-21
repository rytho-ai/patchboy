"""apple-music-cli — Apple Music API CLI for coding agents.

Catalog search + your own library (playlists, saved songs) over Apple Music's
`api.music.apple.com`. Thin-shell shape (cli → client → render) borrowed from
sibling spotify-cli; the caged auth is a *borrowed-token* flow (splice-cli's
reverse-engineering lineage, not OAuth): a public developer token scraped from
the music.apple.com web player, plus a media-user-token the user pastes once.
"""

__version__ = "0.1.0"
