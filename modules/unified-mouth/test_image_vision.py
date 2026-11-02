import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import image_vision
from image_vision import ImageVision, VisionEnricher, MARKER

PNG = b'\x89PNG\r\n\x1a\n' + b'demo'


def raw_message(mid='1', images=1):
    parts = [{'type': 'text', 'data': {'text': '看看这张'}}]
    for i in range(images):
        parts.append({'type': 'image', 'data': {'cached_path': ''}})
    return {'post_type': 'message', 'self_id': '100000002', 'user_id': '100000001',
            'message_id': mid, 'message_type': 'private',
            'sender': {'nickname': '测试者'}, 'message': parts}


class Fixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.incoming = self.root / 'incoming'
        self.incoming.mkdir()
        self.paths = []
        for i in range(3):
            p = self.incoming / f'pic{i}.png'
            p.write_bytes(PNG + bytes([i]))
            self.paths.append(p)
        (self.root / 'image-vision-config.json').write_text('{"enabled": true}', encoding='utf8')
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def vision(self, reply='一只橘猫趴在键盘上，看起来很不耐烦。'):
        def request(cfg, url):
            self.calls.append(url[:30])
            return {'choices': [{'message': {'content': reply}}]}
        return ImageVision(self.root, request)

    def enriched(self, inner=None):
        return VisionEnricher(self.root, inner=inner, vision=self.vision())


class Reads(unittest.TestCase):
    def test_data_url_carries_real_mime_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'x.png'
            p.write_bytes(PNG)
            url = image_vision.data_url(p, 1024)
            self.assertTrue(url.startswith('data:image/png;base64,'))
            self.assertIn('iVBORw0', url)

    def test_data_url_rejects_empty_and_oversized(self):
        import base64 as b64
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'x.jpg'
            p.write_bytes(b'')
            with self.assertRaises(ValueError):
                image_vision.data_url(p, 1024)
            p.write_bytes(b'a' * 2048)
            with self.assertRaises(ValueError):
                image_vision.data_url(p, 1024)

    def test_parse_requires_text(self):
        self.assertEqual(image_vision.parse({'choices': [{'message': {'content': ' 一个  杯子 '}}]}), '一个 杯子')
        for bad in ({}, {'choices': []}, {'choices': [{'message': {'content': '  '}}]}):
            with self.assertRaises(ValueError):
                image_vision.parse(bad)

    def test_api_key_prefers_config_and_falls_back_to_provider_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'cmd.json'
            source.write_text(json.dumps({'provider_sources': [{'key': ['sk-test-key']}]}), encoding='utf8')
            cfg = {'api_key': '', 'key_source': str(source)}
            self.assertEqual(image_vision.api_key(cfg), 'sk-test-key')
            self.assertEqual(image_vision.api_key({'api_key': 'sk-direct', 'key_source': str(source)}), 'sk-direct')
            with self.assertRaises(ValueError):
                image_vision.api_key({'api_key': '', 'key_source': str(root / 'missing.json')})


class Describes(Fixture, unittest.TestCase):
    def test_caption_is_cached_per_image_content(self):
        vision = self.vision()
        first = vision.describe(self.paths[0])
        second = vision.describe(self.paths[0])
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'ok')
        self.assertIn('橘猫', first['caption'])
        self.assertEqual(len(self.calls), 1)
        vision.describe(self.paths[1])
        self.assertEqual(len(self.calls), 2)

    def test_failure_is_not_cached_so_it_can_retry(self):
        calls = []

        def request(cfg, url):
            calls.append(url[:20])
            raise OSError('boom')
        vision = ImageVision(self.root, request)
        result = vision.describe(self.paths[0])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'OSError')
        self.assertEqual(vision.describe(self.paths[0]), result)
        self.assertEqual(len(calls), 2, 'a failed caption must be retried next time')


class Enriches(Fixture, unittest.TestCase):
    def raw(self, images=1, mid='1'):
        raw = raw_message(mid, images)
        for part, path in zip([p for p in raw['message'] if p['type'] == 'image'], self.paths):
            part['data']['cached_path'] = str(path)
        return raw

    def test_text_part_appears_beside_ocr_and_raw_is_untouched(self):
        raw = self.raw()
        before = copy.deepcopy(raw)
        prepared = self.enriched().enrich(raw)
        self.assertEqual(raw, before)
        texts = [p['data']['text'] for p in prepared['message'] if p['type'] == 'text']
        self.assertTrue(any(text.startswith(MARKER) and '橘猫' in text for text in texts))
        self.assertIn(MARKER, prepared['raw_message'])
        self.assertTrue(prepared['_image_vision'])

    def test_second_pass_is_a_no_op(self):
        enricher = self.enriched()
        once = enricher.enrich(self.raw())
        self.assertIs(enricher.enrich(once), once)

    def test_inner_ocr_runs_first(self):
        class Inner:
            def __init__(self):
                self.called = 0

            def enrich(self, raw):
                self.called += 1
                out = copy.deepcopy(raw)
                out['message'].append({'type': 'text', 'data': {'text': '【图片文字识别】A'}})
                return out
        inner = Inner()
        prepared = self.enriched(inner).enrich(self.raw())
        self.assertEqual(inner.called, 1)
        self.assertIn('【图片文字识别】A', prepared['raw_message'])
        self.assertIn(MARKER, prepared['raw_message'])

    def test_disabled_or_missing_paths_do_nothing(self):
        (self.root / 'image-vision-config.json').write_text('{"enabled": false}', encoding='utf8')
        raw = self.raw()
        self.assertIs(self.enriched().enrich(raw), raw)
        (self.root / 'image-vision-config.json').write_text('{"enabled": true}', encoding='utf8')
        blank = raw_message()
        self.assertIs(self.enriched().enrich(blank), blank)

    def test_image_limit_and_failure_text(self):
        (self.root / 'image-vision-config.json').write_text('{"enabled": true, "max_images": 1}', encoding='utf8')
        prepared = self.enriched().enrich(self.raw(images=2))
        self.assertEqual(sum(1 for p in prepared['message'] if p.get('type') == 'text' and p['data']['text'].startswith(MARKER)), 1)
        (self.root / 'image-vision-config.json').write_text('{"enabled": true}', encoding='utf8')
        broken = ImageVision(self.root, lambda cfg, url: (_ for _ in ()).throw(ValueError('x')))
        fresh = self.incoming / 'broken.png'
        fresh.write_bytes(PNG + b'broken')
        target = raw_message(mid='broken')
        target['message'][1]['data']['cached_path'] = str(fresh)
        prepared = VisionEnricher(self.root, vision=broken).enrich(target)
        self.assertIn(MARKER + '（未能识别图片内容）', prepared['raw_message'])


if __name__ == '__main__':
    unittest.main()
