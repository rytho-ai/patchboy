"""CliRunner tests for cli.py's error presentation + flag-fidelity fixes.

Mocks the toolkit at its sub-API boundary (`cli._toolkit()`), never hitting
the network. Covers:
- a toolkit RuntimeError -> exit 1, clean message, no raw traceback
- RateLimitedError -> the distinct IP-block message
- `--sort date` maps to `upload_date` (no KeyError) and rejects unknown values
- markup escape: an untrusted title with "[official video]" survives in output
- `comments` with `result.error` set -> exit 1, no "0 comments" success look
- `--audio --sponsorblock` prints the ignore-warning and still cuts a video
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner
from youtube_toolkit import Comment, CommentAuthor, CommentMetrics, CommentResult, RateLimitedError

from youtube_cli import cli
from youtube_cli.config import Settings

runner = CliRunner()


def _patch_settings(monkeypatch, api_key: str | None = None) -> None:
    """Bypass real .env/env-var lookup so tests are hermetic."""

    def _load(env_file=None) -> Settings:
        return Settings(api_key=api_key, download_dir=Path("/tmp/youtube-cli-test"))

    monkeypatch.setattr(cli.Settings, "load", staticmethod(_load))


def _patch_toolkit(monkeypatch) -> MagicMock:
    toolkit = MagicMock()
    monkeypatch.setattr(cli, "_toolkit", lambda: toolkit)
    return toolkit


# --- FIX A: clean error presentation, no raw traceback ---------------------


def test_toolkit_runtime_error_clean_exit_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.video.side_effect = RuntimeError("All backends failed for this video.")

    result = runner.invoke(cli.app, ["info", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "All backends failed for this video." in result.output
    assert "Traceback" not in result.output


def test_rate_limited_error_gets_distinct_message(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.transcript.side_effect = RateLimitedError(
        "YouTube appears to be rate-limiting or blocking transcript requests."
    )

    result = runner.invoke(cli.app, ["transcript", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "rate-limiting" in result.output
    output_normalized = " ".join(result.output.split())
    assert "try later or from a different network" in output_normalized
    # Must read differently from the plain "no captions" case.
    assert "No transcript available" not in result.output
    assert "Traceback" not in result.output


def test_transcript_no_captions_still_distinct_from_rate_limit(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.transcript.return_value = None

    result = runner.invoke(cli.app, ["transcript", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "No transcript available" in result.output
    assert "rate-limiting" not in result.output


# --- FIX C: --sort mapping, no KeyError -------------------------------------


def test_search_sort_date_maps_to_upload_date(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.search.videos.return_value = []
    _patch_settings(monkeypatch)

    result = runner.invoke(cli.app, ["search", "lofi", "--sort", "date"])

    assert result.exit_code == 0
    assert "Traceback" not in result.output
    _, kwargs = toolkit.search.videos.call_args
    assert kwargs["sort_by"] == "upload_date"


def test_search_sort_views_maps_to_view_count(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.search.videos.return_value = []
    _patch_settings(monkeypatch)

    result = runner.invoke(cli.app, ["search", "lofi", "--sort", "views"])

    assert result.exit_code == 0
    _, kwargs = toolkit.search.videos.call_args
    assert kwargs["sort_by"] == "view_count"


def test_search_invalid_sort_exits_clean_not_keyerror(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    _patch_settings(monkeypatch)

    result = runner.invoke(cli.app, ["search", "lofi", "--sort", "bogus"])

    assert result.exit_code == 1
    assert "Invalid --sort" in result.output
    assert "Traceback" not in result.output
    toolkit.search.videos.assert_not_called()


def test_search_api_with_nondefault_sort_warns_but_does_not_fail(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.search.videos.return_value = []
    _patch_settings(monkeypatch, api_key="fake-key")

    result = runner.invoke(cli.app, ["search", "lofi", "--api", "--sort", "date"])

    assert result.exit_code == 0
    assert "--sort doesn't apply to --api search" in result.output


# --- FIX B: markup escape ----------------------------------------------------


def test_untrusted_title_bracket_text_survives_markup_escape(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.analyze.metadata.return_value = {"title": "Cool Song [official video]"}
    toolkit.analyze.engagement.return_value = {}

    result = runner.invoke(cli.app, ["analyze", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 0
    assert "Cool Song [official video]" in result.output


# --- FIX D: comments error must not look like a silent empty success -------


def test_comments_error_result_exits_clean_not_empty_success(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.comments.return_value = CommentResult(
        comments=[],
        total_results=0,
        error="Comments are disabled for this video.",
    )
    _patch_settings(monkeypatch, api_key="fake-key")

    result = runner.invoke(cli.app, ["comments", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "Comments are disabled for this video." in result.output


def test_comments_missing_key_still_exits_clean(monkeypatch):
    _patch_toolkit(monkeypatch)
    _patch_settings(monkeypatch, api_key=None)

    result = runner.invoke(cli.app, ["comments", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "YOUTUBE_API_KEY" in result.output


# --- FIX F: --audio --sponsorblock warns and ignores audio -----------------


def test_download_audio_sponsorblock_prints_ignore_warning(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.with_sponsorblock.return_value = "/tmp/out/video.mp4"
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app, ["download", "https://youtube.com/watch?v=xyz", "--audio", "--sponsorblock"]
    )

    assert result.exit_code == 0
    assert "--audio" in result.output
    assert "ignored in --sponsorblock mode" in result.output
    toolkit.download.with_sponsorblock.assert_called_once()
    toolkit.download.audio.assert_not_called()


def test_download_sponsorblock_alone_has_no_warning(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.with_sponsorblock.return_value = "/tmp/out/video.mp4"
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app, ["download", "https://youtube.com/watch?v=xyz", "--sponsorblock"]
    )

    assert result.exit_code == 0
    assert "ignored in --sponsorblock mode" not in result.output


# --- PART 1: quick flags on existing commands -------------------------------


def test_download_audio_bitrate_and_prefer_yt_dlp_reach_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.audio.return_value = "/tmp/out/audio.mp3"
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "https://youtube.com/watch?v=xyz",
            "--audio",
            "--bitrate",
            "320k",
            "--prefer-yt-dlp",
        ],
    )

    assert result.exit_code == 0
    _, kwargs = toolkit.download.audio.call_args
    assert kwargs["bitrate"] == "320k"
    assert kwargs["prefer_yt_dlp"] is True


def test_download_video_prefer_yt_dlp_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.video.return_value = "/tmp/out/video.mp4"
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app,
        ["download", "https://youtube.com/watch?v=xyz", "--video", "--prefer-yt-dlp"],
    )

    assert result.exit_code == 0
    _, kwargs = toolkit.download.video.call_args
    assert kwargs["prefer_yt_dlp"] is True


def test_download_sponsorblock_action_and_categories_reach_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.with_sponsorblock.return_value = "/tmp/out/video.mp4"
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "https://youtube.com/watch?v=xyz",
            "--sponsorblock",
            "--sponsorblock-action",
            "mark",
            "--sponsorblock-categories",
            "sponsor,intro",
        ],
    )

    assert result.exit_code == 0
    _, kwargs = toolkit.download.with_sponsorblock.call_args
    assert kwargs["action"] == "mark"
    assert kwargs["categories"] == ["sponsor", "intro"]


def test_info_include_comma_separated_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    video = MagicMock()
    video.chapters = []
    video.heatmap = []
    toolkit.get.video.return_value = video

    result = runner.invoke(
        cli.app,
        ["info", "https://youtube.com/watch?v=xyz", "--include", "chapters,heatmap"],
    )

    assert result.exit_code == 0
    _, kwargs = toolkit.get.video.call_args
    assert sorted(kwargs["include"]) == ["chapters", "heatmap"]


def test_info_chapters_flag_is_shorthand_for_include_chapters(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    video = MagicMock()
    video.chapters = []
    toolkit.get.video.return_value = video

    result = runner.invoke(cli.app, ["info", "https://youtube.com/watch?v=xyz", "--chapters"])

    assert result.exit_code == 0
    _, kwargs = toolkit.get.video.call_args
    assert kwargs["include"] == ["chapters"]


def test_info_invalid_include_exits_clean(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)

    result = runner.invoke(
        cli.app, ["info", "https://youtube.com/watch?v=xyz", "--include", "bogus"]
    )

    assert result.exit_code == 1
    assert "Invalid --include" in result.output
    assert "Traceback" not in result.output
    toolkit.get.video.assert_not_called()


# --- PART 2: new commands ---------------------------------------------------


def test_channel_metadata_calls_get_channel(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.channel.return_value = {
        "channel_name": "Cool Channel [official]",
        "channel_id": "UC123",
        "video_count": 42,
    }

    result = runner.invoke(cli.app, ["channel", "@cool"])

    assert result.exit_code == 0
    assert "Traceback" not in result.output
    # Untrusted channel name with brackets must survive markup escaping
    # (Rich table titles wrap at narrow widths, so compare whitespace-normalized).
    normalized = " ".join(result.output.split())
    assert "Cool Channel [official]" in normalized
    toolkit.get.channel.assert_called_once_with("@cool")


def test_channel_videos_flag_reaches_sub_api_with_sort_and_limit(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.channel.videos.return_value = []

    result = runner.invoke(
        cli.app, ["channel", "@cool", "--videos", "-n", "5", "--sort", "oldest"]
    )

    assert result.exit_code == 0
    args, kwargs = toolkit.get.channel.videos.call_args
    assert args[0] == "@cool"
    assert kwargs["limit"] == 5
    assert kwargs["sort_by"] == "oldest"


def test_channel_shorts_flag_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.channel.shorts.return_value = []

    result = runner.invoke(cli.app, ["channel", "@cool", "--shorts", "-n", "3", "--sort", "popular"])

    assert result.exit_code == 0
    args, kwargs = toolkit.get.channel.shorts.call_args
    assert args[0] == "@cool"
    assert kwargs["limit"] == 3
    assert kwargs["sort_by"] == "popular"  # --sort now applies to shorts too


def test_channel_streams_flag_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.channel.streams.return_value = []

    result = runner.invoke(cli.app, ["channel", "@cool", "--streams", "--sort", "oldest"])

    assert result.exit_code == 0
    _, kwargs = toolkit.get.channel.streams.call_args
    assert kwargs["sort_by"] == "oldest"  # --sort now applies to streams too


def test_channel_invalid_sort_with_shorts_exits_clean(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)

    result = runner.invoke(cli.app, ["channel", "@cool", "--shorts", "--sort", "bogus"])

    assert result.exit_code == 1
    assert "Invalid --sort" in result.output
    toolkit.get.channel.shorts.assert_not_called()


def test_channel_mutually_exclusive_flags_exit_clean(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)

    result = runner.invoke(cli.app, ["channel", "@cool", "--videos", "--shorts"])

    assert result.exit_code == 1
    assert "only one of" in result.output
    toolkit.get.channel.videos.assert_not_called()


def test_channel_invalid_sort_exits_clean(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)

    result = runner.invoke(cli.app, ["channel", "@cool", "--videos", "--sort", "bogus"])

    assert result.exit_code == 1
    assert "Invalid --sort" in result.output
    toolkit.get.channel.videos.assert_not_called()


def test_channel_toolkit_error_exits_clean_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.channel.side_effect = RuntimeError("Channel not found.")

    result = runner.invoke(cli.app, ["channel", "@cool"])

    assert result.exit_code == 1
    assert "Channel not found." in result.output
    assert "Traceback" not in result.output


def test_playlist_metadata_calls_get_playlist(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.playlist.return_value = {"title": "My Mix", "video_count": 10}

    result = runner.invoke(cli.app, ["playlist", "https://youtube.com/playlist?list=abc"])

    assert result.exit_code == 0
    toolkit.get.playlist.assert_called_once_with("https://youtube.com/playlist?list=abc")


def test_playlist_videos_flag_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.playlist.videos.return_value = [
        {"url": "https://youtube.com/watch?v=1", "video_id": "1", "title": "Vid"}
    ]

    result = runner.invoke(
        cli.app, ["playlist", "https://youtube.com/playlist?list=abc", "--videos", "-n", "5"]
    )

    assert result.exit_code == 0
    args, kwargs = toolkit.get.playlist.videos.call_args
    assert args[0] == "https://youtube.com/playlist?list=abc"
    assert kwargs["limit"] == 5
    assert kwargs["use_scrapetube"] is True  # CLI opts into rich detail


def test_playlist_download_flag_reaches_download_playlist(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.playlist.return_value = {
        "success": True,
        "playlist_dir": "/tmp/x/My Mix",
        "metadata_path": "/tmp/x/My Mix/metadata.json",
        "metadata": {
            "download_summary": {
                "total_videos": 3,
                "successful_downloads": 3,
                "failed_downloads": 0,
            }
        },
    }
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app,
        [
            "playlist",
            "https://youtube.com/playlist?list=abc",
            "--download",
            "--type",
            "video",
            "--format",
            "mp4",
            "--dest",
            "/tmp/x",
        ],
    )

    assert result.exit_code == 0
    args, kwargs = toolkit.download.playlist.call_args
    assert args[0] == "https://youtube.com/playlist?list=abc"
    assert kwargs["type"] == "video"
    assert kwargs["format"] == "mp4"
    assert kwargs["output_path"] == "/tmp/x"


def test_playlist_mutually_exclusive_flags_exit_clean(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app, ["playlist", "https://youtube.com/playlist?list=abc", "--videos", "--download"]
    )

    assert result.exit_code == 1
    assert "only one of" in result.output
    toolkit.download.playlist.assert_not_called()


def test_playlist_toolkit_error_exits_clean_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.playlist.side_effect = RuntimeError("Playlist not found.")

    result = runner.invoke(cli.app, ["playlist", "https://youtube.com/playlist?list=abc"])

    assert result.exit_code == 1
    assert "Playlist not found." in result.output
    assert "Traceback" not in result.output


def test_captions_flags_reach_download_captions(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.captions.return_value = "/tmp/out/video.vtt"

    result = runner.invoke(
        cli.app,
        [
            "captions",
            "https://youtube.com/watch?v=xyz",
            "--lang",
            "es",
            "--format",
            "vtt",
            "--dest",
            "/tmp/out.vtt",
        ],
    )

    assert result.exit_code == 0
    args, kwargs = toolkit.download.captions.call_args
    assert args[0] == "https://youtube.com/watch?v=xyz"
    assert kwargs["lang"] == "es"
    assert kwargs["format"] == "vtt"
    assert kwargs["output_path"] == "/tmp/out.vtt"


def test_captions_toolkit_error_exits_clean_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.download.captions.side_effect = RuntimeError("No captions available.")

    result = runner.invoke(cli.app, ["captions", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "No captions available." in result.output
    assert "Traceback" not in result.output


def test_formats_calls_get_formats_and_renders_table(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.formats.return_value = {
        "video_formats": {"1080p": [{"itag": 137, "filesize": 12345, "mime_type": "video/mp4"}]},
        "audio_formats": {"128kbps": [{"itag": 140, "filesize": 999, "mime_type": "audio/mp4"}]},
        "best_video": "1080p",
        "best_audio": "128kbps",
    }

    result = runner.invoke(cli.app, ["formats", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 0
    toolkit.get.formats.assert_called_once_with("https://youtube.com/watch?v=xyz")
    assert "1080p" in result.output
    assert "video/mp4" in result.output


def test_formats_toolkit_error_exits_clean_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.formats.side_effect = RuntimeError("Video unavailable.")

    result = runner.invoke(cli.app, ["formats", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "Video unavailable." in result.output
    assert "Traceback" not in result.output


def test_sponsorblock_calls_analyze_sponsorblock_and_escapes_description(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.analyze.sponsorblock.return_value = [
        {
            "category": "sponsor",
            "description": "Sponsor segment [ad]",
            "start_time": 10.0,
            "end_time": 20.0,
            "duration": 10.0,
            "votes": 5,
        }
    ]

    result = runner.invoke(cli.app, ["sponsorblock", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 0
    toolkit.analyze.sponsorblock.assert_called_once_with("https://youtube.com/watch?v=xyz")
    # Untrusted description with brackets must survive markup escaping.
    assert "Sponsor segment [ad]" in result.output


def test_sponsorblock_toolkit_error_exits_clean_no_traceback(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.analyze.sponsorblock.side_effect = RuntimeError("Video unavailable.")

    result = runner.invoke(cli.app, ["sponsorblock", "https://youtube.com/watch?v=xyz"])

    assert result.exit_code == 1
    assert "Video unavailable." in result.output
    assert "Traceback" not in result.output


def test_search_with_filters_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.search.with_filters.return_value = {"videos": []}
    _patch_settings(monkeypatch)

    result = runner.invoke(
        cli.app,
        [
            "search",
            "lofi",
            "--duration",
            "short",
            "--upload-date",
            "week",
            "--features",
            "hd,4k",
        ],
    )

    assert result.exit_code == 0
    args, kwargs = toolkit.search.with_filters.call_args
    assert args[0] == "lofi"
    assert kwargs["duration"] == "short"
    assert kwargs["upload_date"] == "week"
    assert kwargs["features"] == ["hd", "4k"]
    toolkit.search.videos.assert_not_called()


def test_search_with_filters_ignores_api_flag_with_warning(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.search.with_filters.return_value = {"videos": []}
    _patch_settings(monkeypatch, api_key="fake-key")

    result = runner.invoke(
        cli.app, ["search", "lofi", "--api", "--duration", "short"]
    )

    assert result.exit_code == 0
    assert "ignoring --api" in result.output
    toolkit.search.with_filters.assert_called_once()


def test_comments_search_flag_reaches_sub_api(monkeypatch):
    toolkit = _patch_toolkit(monkeypatch)
    toolkit.get.comments.search.return_value = [
        Comment(
            comment_id="c1",
            text="Great [video]!",
            author=CommentAuthor(display_name="Fan1"),
            published_at=datetime.now(),
            metrics=CommentMetrics(like_count=3),
        )
    ]
    _patch_settings(monkeypatch, api_key="fake-key")

    result = runner.invoke(
        cli.app, ["comments", "https://youtube.com/watch?v=xyz", "--search", "great"]
    )

    assert result.exit_code == 0
    toolkit.get.comments.search.assert_called_once_with(
        "https://youtube.com/watch?v=xyz", "great", limit=20
    )
    # Untrusted comment text with brackets must survive markup escaping.
    assert "Great [video]!" in result.output
    toolkit.get.comments.assert_not_called()
