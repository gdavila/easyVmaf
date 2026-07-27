"""Tests for FFmpegQos._escape_filter_value — FFmpeg filtergraph two-level escaping."""

import os
import subprocess

import pytest

from easyvmaf.config import ffmpeg as FFMPEG
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

    # mixed special characters
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
            ("~tilde.log", "~tilde.log"),
            ("a*b?.log", "a*b?.log"),
            ("a b.log", "a b.log"),
            ("-leading.log", "-leading.log"),
            (".hidden.log", ".hidden.log"),
            ("..dots.log", "..dots.log"),
            ("MixedCase.LOG", "MixedCase.LOG"),
            ("$var!#&{}.log", "$var!#&{}.log"),
            ("", ""),
        ],
        ids=[
            "all-seven-chars",
            "tilde",
            "asterisk-question",
            "space",
            "leading-dash",
            "leading-dot",
            "double-dot",
            "mixed-case",
            "shell-meta-chars",
            "empty-string",
        ],
    )
    def test_mixed_special_chars(self, input_str, expected):
        """Multiple special chars mixed together — interaction between L1/L2 passes."""
        assert FN(input_str) == expected

    # real-world file paths
    @pytest.mark.parametrize(
        "input_str, expected",
        [
            (
                r"D:\test\video.mp4",
                "D" + BS * 2 + ":" + BS * 4 + "test" + BS * 4 + "video.mp4",
            ),
            (
                "d:/test/video.mp4",
                "d" + BS * 2 + ":/test/video.mp4",
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
                r"\\server\share$\file.mp4",
                BS * 4 + BS * 4 + "server" + BS * 4 + "share$" + BS * 4 + "file.mp4",
            ),
        ],
        ids=[
            "windows-absolute",
            "windows-absolute-forward-slash",
            "linux-with-quote",
            "simple-filename",
            "relative-backslash",
            "unc-path",
        ],
    )
    def test_real_world_paths(self, input_str, expected):
        """File paths as they appear in practice."""
        assert FN(input_str) == expected


# Integration / round-trip tests — verify FFmpeg actually writes to the
# expected file after the escaped path goes through the filtergraph parser.

need_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not found on PATH")
need_posix = pytest.mark.skipif(os.name == "nt", reason="POSIX-only test")
need_windows = pytest.mark.skipif(os.name == "posix", reason="Windows-only test")


def _run_psnr(escaped_path):
    """Run a psnr filter writing stats_file=<escaped_path>; return CompletedProcess."""
    # fmt: off
    cmd = [
        FFMPEG, "-hide_banner", "-y",
        "-f", "lavfi", "-i", "testsrc2=s=16x16:d=0.2:r=10",
        "-f", "lavfi", "-i", "testsrc2=s=16x16:d=0.2:r=10",
        "-filter_complex",
        f"[0:v][1:v]psnr=stats_file={escaped_path}",
        "-an", "-f", "null", "-",
    ]
    # fmt: on
    return subprocess.run(cmd, capture_output=True, text=True)


def _round_trip_assert(raw_path):
    """Shared body: escape `raw_path`, run ffmpeg, assert a non-empty file
    lands at `raw_path`. Catches the silent-wrong-location failure mode
    (ffmpeg rc 0 but the file is written elsewhere — e.g. quotes stripped,
    backslash normalized to a slash). Does NOT manage cwd — the two public
    helpers below each pin a single path shape so the cwd dependency can
    never hide behind an unmarked relative path."""
    if os.path.exists(raw_path):
        os.remove(raw_path)
    escaped = FN(raw_path)
    proc = _run_psnr(escaped)
    assert proc.returncode == 0, (
        f"ffmpeg failed (rc={proc.returncode}):\n"
        f"  raw={raw_path!r}\n  escaped={escaped!r}\n"
        f"  stderr tail: {proc.stderr[-400:]!r}"
    )
    assert os.path.isfile(raw_path), (
        f"file NOT created at the raw path — silent wrong-location write.\n"
        f"  raw={raw_path!r}\n  escaped={escaped!r}"
    )
    assert os.path.getsize(raw_path) > 0, "stats file is empty"


def _assert_file_written(raw_path):
    """Round-trip oracle for an ABSOLUTE path. `raw_path` MUST be absolute —
    a relative path's meaning depends on cwd, which this helper deliberately
    does NOT manage. Passing one fails immediately and explicitly instead of
    producing a mysterious 'file not created'. Use
    `_assert_relative_file_written` for relative paths."""
    assert os.path.isabs(raw_path), (
        f"_assert_file_written needs an ABSOLUTE path; got {raw_path!r}. "
        f"A relative path depends on cwd — use _assert_relative_file_written."
    )
    _round_trip_assert(raw_path)


def _assert_relative_file_written(rel_path, cwd, monkeypatch):
    """Round-trip oracle for a RELATIVE path, resolved against `cwd`. A
    relative path has no meaning without a cwd, so `cwd` is an explicit
    parameter, not a hidden out-of-band precondition."""
    monkeypatch.chdir(cwd)
    _round_trip_assert(rel_path)


def _assert_file_written_in_subdir(raw_path):
    """Like `_assert_file_written` but pre-creates any parent directories in
    `raw_path` (for cases whose DIRECTORY component carries special chars)."""
    parent = os.path.dirname(raw_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    _assert_file_written(raw_path)


@need_ffmpeg
@need_windows
class TestEscapeFilterValueRoundTripWindows:
    """Integration tests(Windows): On Windows, backslashes are used as path
    separators, and there are more restrictions regarding file names."""

    def test_safe_chars(self, tmp_path):
        """Safe filenames — no special chars, no escaping — must hit the target."""
        raw = str(tmp_path / "stats_safe.log")
        _assert_file_written(raw)

    def test_special_chars(self, tmp_path):
        """Filename with several special characters should round-trip."""
        raw = str(tmp_path / "a'b;c,d[e]f.log")
        _assert_file_written(raw)

    def test_forward_slash_path(self, tmp_path):
        """Windows path with forward slashes as directory separators."""
        raw = str(tmp_path).replace("\\", "/") + "/stats_fwd.log"
        _assert_file_written(raw)

    def test_relative_path(self, tmp_path, monkeypatch):
        """Relative path with special characters should round-trip."""
        rel_path = r".\stats_rel.log"
        _assert_relative_file_written(rel_path, tmp_path, monkeypatch)

    def test_windows_complex(self, tmp_path):
        """Windows filename with several special characters."""
        raw = str(tmp_path / r"test video's copy(1).log")
        _assert_file_written(raw)

    def test_windows_kitchen_sink(self, tmp_path):
        """Windows filename with a wide variety of special characters."""
        # no colons or double quotes, which are forbidden on Windows
        raw = str(tmp_path / ",!@#$%^&()_+-={}[];' .log")
        _assert_file_written(raw)

    def test_windows_subdir(self, tmp_path):
        """Windows path with backslashes as directory separators."""
        raw = str(tmp_path / r".\subdir\stats_win.log")
        _assert_file_written_in_subdir(raw)

    def test_unc_path(self, tmp_path):
        r"""UNC path `\\localhost\c$\...` must round-trip."""
        # Derive the UNC target from tmp_path so the physical file lands inside
        # tmp_path rather than a persistent system directory.
        local = str(tmp_path / "stats_unc.log")  # C:\Users\...\stats_unc.log
        # C:\Users\... -> \\localhost\c$\Users\... (same physical file via the
        # admin share, but addressed through a UNC path to exercise that form).
        unc = f"\\\\localhost\\{local[0].lower()}$" + local[2:]
        try:
            os.makedirs(os.path.dirname(unc), exist_ok=True)
            probe = os.path.join(os.path.dirname(unc), ".write_probe")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except OSError:
            pytest.skip("UNC admin share not writable in this environment")
        _assert_file_written(unc)


@need_ffmpeg
@need_posix
class TestEscapeFilterValueRoundTripPosix:
    """Integration tests (POSIX): round-trip file paths through FFmpeg's
    filtergraph parser on POSIX (forward-slash path separators)."""

    @pytest.mark.parametrize(
        "fname",
        [
            "stats_safe.log",
            "stats~tilde.log",
            "!#$%&(){}^@=+.log",
            "-leading.log",
            "mid-dle.log",
            ".hidden.log",
            "..dots.log",
            "my stats.log",
            "!@#$%^&*()_+-={}[];:,' ~?.log",
            r"x\y.log",
            r"x\y\z.log",
        ],
        ids=[
            "safe",
            "tilde",
            "shell-meta",
            "leading-hyphen",
            "mid-hyphen",
            "dotfile",
            "dotdot-prefix",
            "space",
            "kitchen-sink",
            "backslash1",
            "backslash2",
        ],
    )
    def test_simple_filenames(self, tmp_path, fname):
        """Filenames with chars inert to the filter-graph parser must
        round-trip unchanged. Covers shell metachars, hyphens, dots,
        spaces, backslashes, tildes, and the full kitchen-sink mix."""
        _assert_file_written(str(tmp_path / fname))

    @pytest.mark.parametrize(
        "rel",
        ["./stats_rel.log", "stats_bare.log"],
        ids=["leading-dot-slash", "bare"],
    )
    def test_relative_paths(self, tmp_path, monkeypatch, rel):
        """Relative paths (with and without ``./`` prefix) — ffmpeg
        resolves them against cwd, not the escape function."""
        _assert_relative_file_written(rel, tmp_path, monkeypatch)

    @pytest.mark.parametrize(
        "subdir",
        ["~sub", "my dir"],
        ids=["tilde-dir", "space-dir"],
    )
    def test_special_dir_components(self, tmp_path, subdir):
        r"""Directory names containing ``~`` or spaces — must survive
        without shell expansion or word-splitting inside the filter
        option value."""
        _assert_file_written_in_subdir(str(tmp_path / subdir / "stats.log"))

    def test_dotdot_traversal(self, tmp_path):
        r"""``..`` as a real path component:
        ``tmp_path/sub/../out.log`` resolves to ``tmp_path/out.log``.
        The escape function passes ``..`` through so the OS resolves it."""
        os.makedirs(tmp_path / "sub", exist_ok=True)
        _assert_file_written_in_subdir(str(tmp_path / "sub" / ".." / "out.log"))

    @pytest.mark.parametrize("char", ["*", "?"], ids=["asterisk", "question"])
    def test_wildcard_chars(self, tmp_path, char):
        """``*`` and ``?`` are legal POSIX filename chars — the shell
        expands them, the filesystem stores them literally. Must not be
        prematurely glob-escaped."""
        _assert_file_written(str(tmp_path / f"p{char}x.log"))

    @pytest.mark.parametrize(
        "char",
        [";", ",", "[", "]"],
        ids=["semicolon", "comma", "lbracket", "rbracket"],
    )
    def test_filter_graph_structural_chars(self, tmp_path, char):
        """Characters ``; , [ ]`` are structural to the filter-graph
        parser and legal in POSIX filenames. The single-backslash escape
        must make them round-trip."""
        _assert_file_written(str(tmp_path / f"p{char}x.log"))

    def test_case_preserving_round_trip(self, tmp_path):
        """The output filename must preserve the requested letter case."""
        filename = "Mixed.Case.LOG"
        raw = str(tmp_path / filename)

        _assert_file_written(raw)

        assert filename in os.listdir(tmp_path), (
            f"filename case was not preserved: expected {filename!r}, "
            f"found {os.listdir(tmp_path)!r}"
        )
