import contextlib
import importlib
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import stream_stability as stability
import xtream_to_m3u as generator

TS = (b"\x47" + b"\x00" * 187) * 40


class Handler(BaseHTTPRequestHandler):
    counts = {}
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        self.counts[path] = self.counts.get(path, 0) + 1
        if path == "/denied":
            self.send_error(403)
            return
        self.send_response(200)
        # Intentionally wrong MIME: evidence must come from media bytes.
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        try:
            if path == "/html":
                self.wfile.write(b"<html>please login</html>")
            elif path == "/junk":
                self.wfile.write(b"some random non-media bytes" * 50)
            elif path == "/ts":
                for _ in range(8):
                    self.wfile.write(TS)
                    self.wfile.flush()
                    time.sleep(.025)
            elif path == "/fast-ts":
                for _ in range(8):
                    self.wfile.write(TS * 32)
                    self.wfile.flush()
                    time.sleep(.025)
            elif path == "/headers":
                self.wfile.write(TS if self.headers.get("User-Agent") == "Required-Agent" else b"<html>denied</html>")
            elif path.endswith(".ts"):
                self.wfile.write(TS)
            elif path == "/master":
                self.wfile.write(b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n/live.m3u8\n")
            else:
                advance = self.counts[path] if path == "/live.m3u8" else 1
                suffix = "#EXT-X-ENDLIST\n" if path == "/vod" else ""
                encrypted = '#EXT-X-KEY:METHOD=AES-128,URI="key"\n' if path == "/encrypted" else ""
                self.wfile.write((f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:{advance}\n"
                    f"{encrypted}#EXTINF:1,\n{advance}.ts\n#EXTINF:1,\n{advance+1}.ts\n{suffix}").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.counts = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def probe(self, path, headers=None):
        return stability.Probe(timeout=.5, window=.08, hls_wait=.02, deadline=2).run(self.base + path, headers)

    def test_html_and_arbitrary_plain_text_never_pass(self):
        for path in ("/html", "/junk", "/denied"):
            self.assertEqual(self.probe(path)["state"], "fail")

    def test_ts_wrong_mime_can_pass_with_continuous_bytes(self):
        result = self.probe("/ts")
        self.assertEqual(result["state"], "pass")
        self.assertGreater(result["duration"], .06)

    def test_hls_requires_segments_and_progress(self):
        self.assertEqual(self.probe("/live.m3u8")["state"], "pass")
        self.assertEqual(self.probe("/stalled")["state"], "partial")
        self.assertEqual(self.probe("/master")["state"], "pass")

    def test_fast_hd_does_not_stop_at_memory_sample_limit(self):
        # Deterministic clock: runner/network scheduling must not change whether
        # the fixture represents a high-bitrate source during the whole window.
        class FastStream:
            status = 200
            def read1(self, limit):
                return TS
        clock = iter(i * .0002 for i in range(10000))
        probe = stability.Probe(window=.08, verify_media=True)
        with patch.object(probe, '_open', return_value=contextlib.nullcontext(FastStream())), \
                patch.object(stability.time, 'monotonic', side_effect=lambda: next(clock)), \
                patch.object(probe, '_verify_sample', return_value=True) as verify:
            result = probe.run('https://example.test/fast')
        self.assertEqual(result["state"], "pass")
        self.assertGreater(result["bytes"], stability.MAX_SAMPLE)
        self.assertLessEqual(len(verify.call_args.args[0]), stability.MAX_SAMPLE)

    def test_vod_excluded_and_encryption_not_claimed_decoded(self):
        self.assertEqual(self.probe("/vod")["reason"], "vod")
        result = self.probe("/encrypted")
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["reason"], "encrypted_not_decoded")

    def test_required_headers_used(self):
        self.assertEqual(self.probe("/headers")["state"], "fail")
        self.assertEqual(self.probe("/headers", {"user-agent": "Required-Agent"})["state"], "partial")


def candidate(key, provider=1, name="channel"):
    return {"key": key, "has_logo": False, "provider": provider, "name": name}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.history = {"version": 1, "streams": {}}

    def test_history_outweighs_tiny_startup_difference(self):
        for _ in range(8):
            stability.observe(self.history, "stable", {"state": "pass", "latency": .5})
            stability.observe(self.history, "flaky", {"state": "fail", "latency": .1})
        results = {"stable": {"state": "pass", "latency": .5},
                   "flaky": {"state": "pass", "latency": .1}}
        self.assertEqual(stability.choose([candidate("flaky"), candidate("stable")], results, self.history)["key"], "stable")

    def test_success_replaces_failed_incumbent(self):
        results = {"old": {"state": "fail"}, "new": {"state": "pass", "latency": 2}}
        self.assertEqual(stability.choose([candidate("old"), candidate("new")], results, self.history, "old")["key"], "new")

    def test_hysteresis_keeps_equivalent_incumbent(self):
        results = {"old": {"state": "pass", "latency": .3}, "new": {"state": "pass", "latency": .2}}
        self.assertEqual(stability.choose([candidate("old"), candidate("new")], results, self.history, "old")["key"], "old")

    def test_unmeasured_is_not_success(self):
        results = {"bad": {"state": "fail"}}
        self.assertEqual(stability.choose([candidate("bad"), candidate("unmeasured")], results, self.history)["key"], "unmeasured")

    def test_budget_fairness_rotates_unmeasured_channels_first(self):
        self.history["streams"]["a"] = {"updated": time.time(), "observations": []}
        groups = {"one": [candidate("a", name="one"), candidate("b", name="one")],
                  "two": [candidate("c", name="two")]}
        order = stability.probe_order(groups, self.history, {})
        self.assertEqual({row["name"] for row in order[:2]}, {"one", "two"})
        self.assertEqual(order[-1]["key"], "a")

    def test_mass_loss_and_replacement_rejected(self):
        for current in (set(), {"a"}, {"z", "x", "y"}):
            with self.assertRaises(ValueError):
                stability.validate_retention({"a", "b", "c"}, current)
        stability.validate_retention({"a", "b", "c"}, {"a", "b", "c", "d"})

    def test_backups_diversify_and_never_keep_failed_incumbent(self):
        options = [candidate("old"), candidate("one"), candidate("same"),
                   candidate("two", 2), candidate("three", 3), candidate("unknown", 4)]
        results = {c["key"]: {"state": "pass"} for c in options[:-1]}
        results["old"] = {"state": "fail"}
        sources = stability.choose_sources(options, results, self.history, "old")
        self.assertEqual([s["key"] for s in sources], ["one", "two", "three"])

    def test_recent_history_allowed_expired_and_last_failure_excluded(self):
        for key, state in [("fresh", "pass"), ("stale", "pass"), ("bad", "fail")]:
            stability.observe(self.history, key, {"state": state})
        self.history["streams"]["stale"]["updated"] -= stability.EVIDENCE_TTL + 1
        sources = stability.choose_sources([candidate(k) for k in ("fresh", "stale", "bad", "new")], {}, self.history)
        self.assertEqual([s["key"] for s in sources], ["fresh"])
        self.assertEqual(stability.choose_sources([candidate("fresh")], {"fresh": {"state": "fail"}}, self.history), [])

    def test_probe_backup_round_prioritizes_other_account(self):
        options = [candidate("a", 1), candidate("b", 1), candidate("c", 2)]
        self.assertEqual([c["key"] for c in stability.probe_order({"channel": options}, self.history, {})], ["a", "c", "b"])

    def test_backup_metadata_roundtrip_has_independent_headers(self):
        sources = [dict(candidate("a"), url="https://one.test/live", headers={"cookie": "one"},
                        entry=['#EXTINF:-1,Canal', '#EXTHTTP:{"Cookie":"one"}', 'https://one.test/live']),
                   dict(candidate("b", 2), url="https://two.test/live", headers={"authorization": "two"})]
        entry = stability.with_backups(sources)
        self.assertEqual(stability.request_target(entry), ("https://one.test/live", {"cookie": "one"}))
        backup = json.loads(next(line[len(stability.BACKUP_TAG):] for line in entry if line.startswith(stability.BACKUP_TAG)))
        self.assertEqual(backup, {"url": "https://two.test/live", "headers": {"authorization": "two"}})

    def test_history_has_no_credentials_and_prunes_stale_records(self):
        key = stability.identity("https://example.test/live/user/password/1.ts", {"cookie": "private"})
        stability.observe(self.history, key, {"state": "pass", "latency": .3})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            stability.atomic_json(path, self.history)
            content = path.read_text()
            self.assertNotIn("password", content)
            self.assertNotIn("private", content)
            self.assertEqual(len(stability.load_history(path)["streams"]), 1)
            self.history["streams"][key]["updated"] = 1
            stability.atomic_json(path, self.history)
            self.assertEqual(stability.load_history(path)["streams"], {})

    def test_corrupt_history_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text("not json")
            self.assertEqual(stability.load_history(path)["streams"], {})

    def test_header_directives_and_inline_override(self):
        entry = ['#EXTINF:-1,Test', '#EXTVLCOPT:http-user-agent=old',
                 '#EXTHTTP:{"Cookie":"session"}', 'https://example.test/live|User-Agent=new']
        url, headers = stability.request_target(entry)
        self.assertEqual(url, "https://example.test/live")
        self.assertEqual(headers, {"user-agent": "new", "cookie": "session"})

    def test_ts_alignment_and_mime_not_enough(self):
        self.assertEqual(stability.media_signature(b"abc" + TS), "ts")
        self.assertIsNone(stability.media_signature(b"arbitrary" * 100))

    @unittest.skipUnless(shutil.which("ffprobe") and shutil.which("ffmpeg"), "FFmpeg not available")
    def test_ffprobe_identifies_real_video_and_rejects_synthetic_packets(self):
        completed = subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=10",
            "-t", "1", "-c:v", "mpeg2video", "-f", "mpegts", "pipe:1"],
            capture_output=True, timeout=5, check=True)
        probe = stability.Probe(verify_media=True)
        self.assertTrue(probe._verify_sample(completed.stdout))
        self.assertFalse(probe._verify_sample(TS))


@contextlib.contextmanager
def working_directory(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class GeneratorTests(unittest.TestCase):
    source = '#EXTM3U\n#EXTINF:-1 group-title="Argentina",Telefe\nhttps://example.test/live/u/p/1.ts\n'

    def run_generator(self, body, result, directory):
        with working_directory(directory), patch.object(generator, "env_provider", side_effect=lambda i: (1, "https://example.test/get.php?username=u&password=p") if i == 1 else None), patch.object(generator, "fetch_m3u", return_value=body), patch.object(generator, "fetch_logo_manifest", return_value=[]), patch.object(generator, "probe_stream", return_value=result), contextlib.redirect_stdout(__import__('io').StringIO()), contextlib.redirect_stderr(__import__('io').StringIO()):
            return generator.main()

    def test_success_writes_candidate_and_safe_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            code = self.run_generator(self.source, {"state": "pass", "latency": .1}, directory)
            self.assertEqual(code, 0)
            generated = Path(directory, "dist/lista_clasica.m3u").read_text()
            self.assertIn("Telefe", generated)
            report = Path(directory, "dist/diagnostico_estabilidad.json").read_text()
            self.assertNotIn("https://", report)
            self.assertIn('"pass": 1', report)

    def test_total_probe_failure_does_not_overwrite_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory, "lista_clasica.m3u")
            baseline.write_text(self.source)
            code = self.run_generator(self.source, {"state": "fail", "reason": "transport"}, directory)
            self.assertEqual(code, 1)
            self.assertEqual(baseline.read_text(), self.source)
            self.assertFalse(Path(directory, "dist/lista_clasica.m3u").exists())

    def test_empty_provider_does_not_overwrite_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory, "lista_clasica.m3u")
            baseline.write_text(self.source)
            self.assertEqual(self.run_generator("#EXTM3U\n", {"state": "pass"}, directory), 1)
            self.assertEqual(baseline.read_text(), self.source)

    def test_linear_fiction_category_compatible_with_client(self):
        entry = generator.parse_entries('#EXTINF:-1 group-title="Argentina",HBO HD\nhttps://example.test/live/u/p/1.ts')[0]
        self.assertEqual(generator.category_for(entry), "TV · Ficción")
        self.assertNotRegex(generator.category_for(entry).lower(), "cine|series|movie|pelicula")

    def test_adult_vod_and_different_channels(self):
        for name, group, path in [("HBO", "Adultos", "live"), ("HBO", "Argentina", "movie"), ("Telefe S01E01", "Argentina", "live")]:
            entry = generator.parse_entries(f'#EXTINF:-1 group-title="{group}",{name}\nhttps://example.test/{path}/u/p/1.ts')[0]
            self.assertFalse(generator.keep_entry(entry))
        self.assertEqual(generator.catalog_match("Studio Universal HD")[1], "Studio Universal")
        self.assertEqual(generator.catalog_match("Universal TV HD")[1], "Universal TV")

    def test_names_and_attributes_with_commas_survive(self):
        entry = ['#EXTINF:-1 tvg-name="Name, original" group-title="Argentina",Canal, regional',
                 'https://example.test/live/1.ts']
        self.assertEqual(generator.metadata(entry)[0], "Canal, regional")
        changed = generator.set_group_title(entry, "Noticias")
        self.assertEqual(generator.metadata(changed)[0], "Canal, regional")
        self.assertEqual(generator.metadata(changed)[1]["tvg-name"], "Name, original")

    def test_mass_loss_keeps_old_playlist_and_explains_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = self.source + '#EXTINF:-1,HBO\nhttps://example.test/live/2.ts\n'
            path = Path(directory, "lista_clasica.m3u")
            path.write_text(baseline)
            self.assertEqual(self.run_generator(self.source, {"state": "pass", "latency": .1}, directory), 1)
            self.assertEqual(path.read_text(), baseline)
            report = json.loads(Path(directory, "dist/diagnostico_estabilidad.json").read_text())
            self.assertEqual(report["publicacion"], "massive_channel_loss")

    def test_same_channel_three_sources_one_visible_entry(self):
        body = self.source + self.source.split('\n', 1)[1].replace('/1.ts', '/2.ts') + self.source.split('\n', 1)[1].replace('/1.ts', '/3.ts')
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.run_generator(body, {"state": "pass"}, directory), 0)
            entries = generator.parse_entries(Path(directory, "dist/lista_clasica.m3u").read_text())
            self.assertEqual(len(entries), 1)
            self.assertEqual(sum(line.startswith(stability.BACKUP_TAG) for line in entries[0]), 2)
            report = json.loads(Path(directory, "dist/diagnostico_estabilidad.json").read_text())
            self.assertEqual(report["canales_con_dos_respaldos"], 1)

    def test_known_failed_primary_replaced_or_removed_even_if_incumbent(self):
        body = self.source + '#EXTINF:-1 group-title="Argentina",HBO\nhttps://example.test/live/2.ts\n'
        with tempfile.TemporaryDirectory() as directory, working_directory(directory), \
                patch.object(generator, "env_provider", side_effect=lambda i: (1, "https://example.test/get.php?username=u&password=p") if i == 1 else None), \
                patch.object(generator, "fetch_m3u", return_value=body), \
                patch.object(generator, "fetch_logo_manifest", return_value=[]), \
                patch.object(generator, "probe_stream", side_effect=lambda url, headers: {"state": "fail" if '/1.ts' in url else "pass"}), \
                contextlib.redirect_stdout(__import__('io').StringIO()):
            Path('lista_clasica.m3u').write_text(body)
            self.assertEqual(generator.main(), 0)
            entries = generator.parse_entries(Path('dist/lista_clasica.m3u').read_text())
            self.assertEqual([generator.metadata(e)[0] for e in entries], ['HBO'])

    def test_channel_identity_does_not_merge_local_or_numbered_feeds(self):
        for _, canonical, _ in generator.CHANNEL_CATALOG:
            self.assertIsNotNone(generator.catalog_match(canonical), canonical)
        self.assertEqual(generator.catalog_match('AR | Crónica HD')[1], 'Crónica TV')
        self.assertIsNone(generator.catalog_match('Telefe Tucumán'))
        self.assertIsNone(generator.catalog_match('Canal 12 Posadas'))
        self.assertIsNone(generator.catalog_match('HBO 9'))

    def test_same_account_never_probed_concurrently(self):
        active = 0
        maximum = 0
        lock = threading.Lock()
        def probe(*args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.01)
            with lock:
                active -= 1
            return {"state": "pass", "latency": .1}
        def fetch(i, url):
            name = "Telefe" if i == 1 else "HBO"
            return f'#EXTM3U\n#EXTINF:-1 group-title="Argentina",{name}\nhttps://example.test/live/u/p/{i}.ts\n'
        with tempfile.TemporaryDirectory() as directory, working_directory(directory), \
                patch.object(generator, "env_provider", side_effect=lambda i: (i, "https://example.test/get.php?username=u&password=p") if i <= 2 else None), \
                patch.object(generator, "fetch_m3u", side_effect=fetch), \
                patch.object(generator, "fetch_logo_manifest", return_value=[]), \
                patch.object(generator, "probe_stream", side_effect=probe), \
                contextlib.redirect_stdout(__import__('io').StringIO()):
            self.assertEqual(generator.main(), 0)
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
