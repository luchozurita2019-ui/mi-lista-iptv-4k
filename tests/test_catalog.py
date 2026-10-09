import collections
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import channel_catalog as catalog
import stream_stability as stability
import xtream_to_m3u as generator


def entry(name, group="Argentina", country="", language="", url="https://example.test/live/u/p/1.ts"):
    return [f'#EXTINF:-1 group-title="{group}" tvg-country="{country}" tvg-language="{language}",{name}', url]


class CatalogTests(unittest.TestCase):
    def test_complete_packs_have_distinct_identities(self):
        expected = {
            "HBO": {"HBO", "HBO 2", "HBO Plus", "HBO Family", "HBO Signature", "HBO Mundi", "HBO Pop", "HBO Xtreme"},
            "Universal+": {"Universal Premiere", "Universal Cinema", "Universal Comedy", "Universal Crime", "Universal Reality"},
            "Fútbol": {"ESPN Premium", "TNT Sports Argentina"},
        }
        for pack, names in expected.items():
            self.assertEqual({row["name"] for row in catalog.CHANNELS if row["pack"] == pack}, names)
            self.assertEqual(len({generator.entry_name(entry(name)) for name in names}), len(names))

    def test_provider_variants_merge_without_crossing_numbered_channels(self):
        cases = {
            "AR | ($ŠPN -3)": "ESPN 3", "AR | [$ŠPN) PREMIUM": "ESPN Premium",
            "AR | F0X $PORTS 2": "Fox Sports 2", "F⚽X $P0RTS 3 HD": "Fox Sports 3",
            "ARG I T&C SPORTS op2": "TyC Sports", "ARG | AMÃ©RICA TV": "América TV",
            "ARG| VOLVER FHD": "Volver", "TelefeHD": "Telefe",
            "Universal Crime 1080p HEVC": "Universal Crime", "HBO Pop LATINO HD": "HBO Pop",
            "ARG| CINEAR  HD": "CINE.AR", "ESPN7": "ESPN 7", "HBO+": "HBO Plus",
            "DSports+ HD": "DSports Plus", "DirecTV Sports 3": "DSports 3",
        }
        for variant, name in cases.items():
            with self.subTest(variant=variant):
                self.assertEqual(generator.catalog_match(variant, "Argentina")[1], name)
        self.assertNotEqual(generator.entry_name(entry("Fox Sports 2")), generator.entry_name(entry("Fox Sports 3")))
        self.assertNotEqual(generator.entry_name(entry("HBO")), generator.entry_name(entry("HBO Pop")))
        self.assertNotEqual(generator.entry_name(entry("DSports")), generator.entry_name(entry("DSports+")))

    def test_known_names_do_not_override_explicit_foreign_feeds(self):
        cases = [entry("HBO", country="US"), entry("ESPN", group="Brasil"),
                 entry("ESPN Brasil"), entry("MX | TNT"), entry("TyC Sports | UY"),
                 entry("TNT", language="eng"), entry("TNT", group="US | CINE"),
                 entry("Canal 7 Guatemala"), entry("GT: Canal 7"),
                 entry("Television Municipal de Cordoba | ES"),
                 entry("USA**:NM | SANTA FE | TELEMUNDO KASA")]
        for candidate in cases:
            with self.subTest(name=generator.metadata(candidate)[0]):
                self.assertFalse(generator.keep_entry(candidate))
        self.assertTrue(generator.keep_entry(entry("HBO", group="LATAM", language="spa")))
        self.assertTrue(generator.keep_entry(entry("USA Network", group="Argentina")))

    def test_generic_geography_events_radios_and_partial_names_are_not_channels(self):
        for name in ("Argentina vs Benin", "Canal 7", "Radio Argentina", "HBO Oferta", "HBO 20",
                     "HBO 2 EXTRA", "TNT Sports Chile", "Disney Movies 24/7", "Universal Cinema Trailer",
                     "Santa Fe Telemundo", "ESPN 15:30 Boca vs River"):
            with self.subTest(name=name):
                self.assertFalse(generator.keep_entry(entry(name)))
        self.assertFalse(generator.keep_entry(entry("Canal 9", group="TV")))
        self.assertTrue(generator.keep_entry(entry("Canal 9", group="Argentina")))

    def test_genres_use_catalog_not_misleading_provider_groups(self):
        for name, category in [("Telefe", "Argentina · Nacionales"), ("HBO Pop", "TV · Ficción"),
                               ("TNT Sports", "Argentina · Deportes"), ("Pakapaka", "Argentina · Infantiles"),
                               ("DreamWorks", "Argentina · Infantiles"), ("Canal Rural", "Argentina · Cultura"),
                               ("MTV Hits", "Argentina · Música"), ("El Doce", "Argentina · Regionales")]:
            self.assertEqual(generator.category_for(entry(name, group="Argentina | Noticias")), category)

    def test_collections_use_winners_and_preserve_playback_headers(self):
        entries = []
        details = {}
        for number, (name, state) in enumerate((('HBO Pop', 'pass'), ('Universal Crime', 'partial'),
                                                ('ESPN Premium', 'unknown'), ('DreamWorks', 'pass')), 1):
            original = entry(name, url=f"https://example.test/live/u/p/{number}.ts")
            original.insert(1, '#EXTVLCOPT:http-user-agent=Required-Agent')
            normalized = generator.set_display_name(generator.set_group_title(original, generator.category_for(original)))
            entries.append(normalized)
            details[name] = {"state": state, "options": 3}
        with tempfile.TemporaryDirectory() as directory:
            counts = generator.write_collections(entries, details, directory)
            self.assertEqual(counts['packs_premium'], 3)
            self.assertEqual(counts['estables'], 2)
            self.assertEqual(counts['hbo'], 1)
            self.assertEqual(counts['deportes'], 1)
            safe = json.loads(Path(directory, 'listas/disponibilidad.json').read_text())
            self.assertNotIn('https://', json.dumps(safe))
            self.assertEqual(safe['catalogo_objetivo'], len(catalog.CHANNELS))
            for path in Path(directory, 'listas').glob('*.m3u'):
                subset = generator.parse_entries(path.read_text())
                for candidate in subset:
                    self.assertIn(candidate, entries)
                    self.assertIn('#EXTVLCOPT:http-user-agent=Required-Agent', candidate)

    def test_scope_migration_does_not_disable_retention_for_valid_channels(self):
        old = [entry('HBO'), entry('HBO 2'), entry('Telefe'), entry('Canal 7 Guatemala'), entry('Radio Argentina')]
        previous = {generator.entry_name(candidate) for candidate in old if generator.keep_entry(candidate)}
        self.assertEqual(len(previous), 3)
        with self.assertRaises(ValueError):
            stability.validate_retention(previous, {generator.entry_name(entry('HBO'))})


class SelectionCoverageTests(unittest.TestCase):
    def candidate(self, key, provider, name='channel'):
        return {"key": key, "provider": provider, "name": name, "has_logo": False}

    def test_cold_start_distributes_first_probes_and_covers_every_channel(self):
        groups = {f'channel{i}': [self.candidate(f'{i}a', 1, f'channel{i}'), self.candidate(f'{i}b', 2, f'channel{i}')]
                  for i in range(10)}
        order = stability.probe_order(groups, {"version": 1, "streams": {}}, {})
        self.assertEqual(collections.Counter(row['provider'] for row in order[:10]), {1: 5, 2: 5})
        self.assertEqual(len({row['name'] for row in order[:10]}), 10)

    def test_current_incumbent_first_unless_last_measurement_failed(self):
        old, new = self.candidate('old', 1), self.candidate('new', 2)
        history = {"version": 1, "streams": {}}
        stability.observe(history, 'old', {"state": "pass"})
        self.assertEqual(stability.probe_order({'channel': [old, new]}, history, {'channel': 'old'})[0], old)
        stability.observe(history, 'old', {"state": "fail"})
        self.assertEqual(stability.probe_order({'channel': [old, new]}, history, {'channel': 'old'})[0], new)

    def test_recent_success_beats_inconclusive_new_sample_but_expires(self):
        history = {"version": 1, "streams": {}}
        stability.observe(history, 'old', {"state": "pass"})
        options = [self.candidate('old', 1), self.candidate('new', 2)]
        results = {'new': {'state': 'partial', 'latency': .1}}
        self.assertEqual(stability.choose(options, results, history)['key'], 'old')
        history['streams']['old']['updated'] = time.time() - 13 * 3600
        self.assertEqual(stability.choose(options, results, history)['key'], 'new')

    def test_large_startup_improvement_can_replace_equally_reliable_source(self):
        options = [self.candidate('old', 1), self.candidate('new', 2)]
        results = {'old': {'state': 'pass', 'latency': 4}, 'new': {'state': 'pass', 'latency': .1}}
        self.assertEqual(stability.choose(options, results, {"version": 1, "streams": {}}, 'old')['key'], 'new')

    def test_failed_channel_promotes_backup_before_unrelated_queued_channels(self):
        events = []
        def body(number):
            names = ['HBO'] if number == 1 else ['HBO', 'Golden', 'TNT', 'Warner Channel']
            return '#EXTM3U\n' + ''.join('\n'.join(entry(name, url=f'https://example.test/live/u{number}/p/{name.replace(" ", "_")}.ts')) + '\n' for name in names)
        def probe(url, headers):
            events.append(url.split('/')[-1])
            if '/u1/' in url:
                time.sleep(.005)
                return {'state': 'fail', 'reason': 'transport'}
            time.sleep(.03)
            return {'state': 'pass', 'latency': .1}
        with tempfile.TemporaryDirectory() as directory:
            previous_directory = os.getcwd()
            os.chdir(directory)
            try:
                # Pin the existing primaries to their accounts. Provider 2 starts
                # on Golden; failed provider-1 HBO must move backup HBO forward.
                baseline = body(1) + body(2).replace('#EXTM3U\n', '').replace('\n'.join(entry('HBO', url='https://example.test/live/u2/p/HBO.ts')) + '\n', '')
                Path('lista_clasica.m3u').write_text(baseline)
                with patch.object(generator, 'env_provider', side_effect=lambda i: (i, f'https://example.test/get.php?username=u{i}&password=p') if i <= 2 else None), \
                        patch.object(generator, 'fetch_m3u', side_effect=lambda i, url: body(i)), \
                        patch.object(generator, 'probe_stream', side_effect=probe), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(generator.main(), 0)
                self.assertLess(events.index('HBO.ts', 1), events.index('TNT.ts'))
                report = json.loads(Path('dist/diagnostico_estabilidad.json').read_text())
                self.assertNotIn('fail', report['ganadores_por_evidencia'])
            finally:
                os.chdir(previous_directory)


if __name__ == '__main__':
    unittest.main()
