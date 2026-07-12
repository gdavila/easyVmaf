"""Tests for FFmpegQos._escape_filter_value — FFmpeg filtergraph two-level escaping."""

import os
import subprocess

import pytest

from easyvmaf.config import ffmpeg as _FFMPEG
from easyvmaf.ffmpeg import FFmpegQos

BS = "\\"
FN = FFmpegQos._escape_filter_value


# Unit tests — verify the exact escaped string output
class TestEscapeFilterValueUnit:
    """Unit tests: _escape_filter_value returns the correct escape sequence."""

    # single special characters
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            (BS, BS * 4),
            ("'", BS * 3 + "'"),
            (":", BS * 2 + ":"),
            (",", BS + ","),
            (";", BS + ";"),
            ("[", BS + "["),
            ("]", BS + "]"),
        ],
        ids=[
            "backslash",
            "single-quote",
            "colon",
            "comma",
            "semicolon",
            "left-bracket",
            "right-bracket",
        ],
    )
    def test_single_special_char(self, input_str, expected):
        """Each special character produces its exact escape sequence."""
        assert FN(input_str) == expected

    # combined special characters
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            (
                "a:b,c;d[e]f'g\\h",
                "a"
                + BS * 2
                + ":b"
                + BS
                + ",c"
                + BS
                + ";d"
                + BS
                + "[e"
                + BS
                + "]f"
                + BS * 3
                + "'g"
                + BS * 4
                + "h",
            ),
            (
                r"\'",
                BS * 4 + BS * 3 + "'",
            ),
            ("", ""),
        ],
        ids=["all-seven-chars", "backslash-then-quote", "empty-string"],
    )
    def test_combined_special_chars(self, input_str, expected):
        """Multiple special chars mixed together — interaction between L1/L2 passes."""
        assert FN(input_str) == expected

    # real-world file paths
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            (
                r"C:\Users\admin\video.mp4",
                "C"
                + BS * 2
                + ":"
                + BS * 4
                + "Users"
                + BS * 4
                + "admin"
                + BS * 4
                + "video.mp4",
            ),
            (
                r"/home/user/video's copy.mp4",
                "/home/user/video" + BS * 3 + "'s copy.mp4",
            ),
            (
                "output_vmaf.json",
                "output_vmaf.json",
            ),
            (
                r".\subdir\file.mp4",
                "." + BS * 4 + "subdir" + BS * 4 + "file.mp4",
            ),
            (
                r"\\server\share\file.mp4",
                BS * 4 + BS * 4 + "server" + BS * 4 + "share" + BS * 4 + "file.mp4",
            ),
        ],
        ids=[
            "windows-absolute",
            "linux-with-quote",
            "simple-filename",
            "relative-backslash",
            "unc-path",
        ],
    )
    def test_real_world_paths(self, input_str, expected):
        """File paths as they appear in practice."""
        assert FN(input_str) == expected

    # no-op safe characters
    @pytest.mark.parametrize(
        "input_str",
        [
            "",
            "stats_file_psnr.log",
            "视频测试.mp4",
            "動画テスト.mp4",
            "*",
            "$",
            "%",
            " ",
            "@",
            "#",
            "!",
            "?",
            "{}",
            "()",
            "&",
            "~",
        ],
        ids=[
            "empty",
            "alphanumeric-dot-underscore",
            "unicode-chinese",
            "unicode-japanese",
            "asterisk",
            "dollar",
            "percent",
            "space",
            "at-sign",
            "hash",
            "exclamation",
            "question-mark",
            "curly-braces",
            "parentheses",
            "ampersand",
            "tilde",
        ],
    )
    def test_noop_safe_chars(self, input_str):
        """Characters outside the escape set must pass through unchanged."""
        assert FN(input_str) == input_str


# Integration / round-trip tests — verify FFmpeg actually writes to the
# expected file after the escaped path goes through the filtergraph parser.

need_ffmpeg = pytest.mark.skipif(not _FFMPEG, reason="ffmpeg not found on PATH")
need_posix = pytest.mark.skipif(os.name == "nt", reason="POSIX-only test")
need_windows = pytest.mark.skipif(os.name == "posix", reason="Windows-only test")


def _run_psnr(escaped_value: str, cwd: str) -> None:
    """Run psnr with stats_file=<escaped_value>. Fails the test on error."""
    # fmt: off
    cmd = [
        _FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=16x16:d=0.2:r=10",
        "-f", "lavfi", "-i", "testsrc2=s=16x16:d=0.2:r=10",
        "-filter_complex", f"[0:v][1:v]psnr=stats_file={escaped_value}",
        "-an", "-f", "null", "-",
    ]
    # fmt: on
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        pytest.fail(
            f"ffmpeg failed (exit {result.returncode}):\n"
            f"  escaped_value={escaped_value!r}\n"
            f"  stderr={result.stderr.strip()}"
        )


class TestEscapeFilterValueRoundTrip:
    """Integration tests: FFmpeg round-trips the escaped value and writes the
    file at the expected path."""

    @staticmethod
    def _assert_roundtrip(target: str, tmp_path) -> None:
        """Escape *target*, run ffmpeg psnr, assert the file is written."""
        cwd = str(tmp_path)
        target_abs = (
            os.path.normpath(os.path.join(cwd, target))
            if not os.path.isabs(target)
            else target
        )
        # ensure parent directories exist (e.g. for r"subdir\file.log")
        parent = os.path.dirname(target_abs)
        os.makedirs(parent, exist_ok=True)
        escaped = FN(target)
        _run_psnr(escaped, cwd)
        assert os.path.exists(target_abs), (
            f"FFmpeg should have written {target_abs!r} (escaped={escaped!r})"
        )

    @need_ffmpeg
    def test_roundtrip_safe_chars(self, tmp_path):
        """Safe filenames — no special chars, no escaping — must hit the target."""
        self._assert_roundtrip("stats_safe.log", tmp_path)

    @need_ffmpeg
    def test_roundtrip_special_chars(self, tmp_path):
        """Filename with several special characters should round-trip."""
        self._assert_roundtrip("a'b;c,d[e]f.log", tmp_path)

    @need_ffmpeg
    @need_posix
    def test_roundtrip_posix_abs(self, tmp_path):
        """POSIX absolute filename with colon and single-quote."""
        cwd = str(tmp_path)
        abspath_target = os.path.normpath(os.path.join(cwd, "stats_posix.log"))
        self._assert_roundtrip(abspath_target, tmp_path)

    @need_ffmpeg
    @need_posix
    def test_roundtrip_posix_complex(self, tmp_path):
        """POSIX filename containing colon, single-quote and space in one name."""
        self._assert_roundtrip("Crime d'Amour.log", tmp_path)
        self._assert_roundtrip(r'a?b\c*d".log', tmp_path)  # only valid on POSIX

    @need_ffmpeg
    @need_posix
    def test_roundtrip_posix_subdir(self, tmp_path):
        """POSIX filename in a subdirectory."""
        self._assert_roundtrip("subdir/stats_posix.log", tmp_path)

    @need_ffmpeg
    @need_windows
    def test_roundtrip_windows_abs(self, tmp_path):
        """Windows absolute filename with colon and single-quote."""
        cwd = str(tmp_path)
        abspath_target = os.path.normpath(os.path.join(cwd, "stats_win.log"))
        self._assert_roundtrip(abspath_target, tmp_path)

    @need_ffmpeg
    @need_windows
    def test_roundtrip_windows_complex(self, tmp_path):
        """Windows filename with spaces, quote, and parentheses."""
        self._assert_roundtrip(r"test video's copy(1).log", tmp_path)

    @need_ffmpeg
    @need_windows
    def test_roundtrip_windows_backslash_abs(self, tmp_path):
        """Windows path with backslashes as directory separators."""
        self._assert_roundtrip(r".\subdir\stats_win.log", tmp_path)
