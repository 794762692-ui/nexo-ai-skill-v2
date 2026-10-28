import base64
import csv
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import requests
import main


def image_bytes():
    buffer = BytesIO()
    Image.new('RGB', (16, 16), 'red').save(buffer, format='PNG')
    return buffer.getvalue()


def response(payload=None, status=200):
    result = Mock()
    result.status_code = status
    result.headers = {'x-request-id': 'req_test'}
    result.json.return_value = payload
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class FlowTests(unittest.TestCase):
    def generate(self, session):
        return main.generate_image(session, 'edit', image_bytes(), key='secret',
            model='test-edit', base_url='https://example.com/v1', size='1024x1024', timeout=30)

    def test_reference_uploaded_and_parameters(self):
        session = Mock()
        session.post.return_value = response({'data': [{'b64_json': base64.b64encode(image_bytes()).decode()}]})
        image, rid = self.generate(session)
        args = session.post.call_args.kwargs
        self.assertEqual(args['files']['image[]'][1], image_bytes())
        self.assertEqual(args['data']['n'], '1')
        self.assertNotIn('response_format', args['data'])
        self.assertEqual(image.size, (16, 16))
        self.assertEqual(rid, 'req_test')

    def test_invalid_responses(self):
        for payload in ({}, {'data': []}, {'data': [{'url': 'https://example.com'}]},
                        {'data': [{'b64_json': '%%%'}]}, {'data': [{'b64_json': 'eA=='}]}):
            with self.subTest(payload=payload):
                session = Mock()
                session.post.return_value = response(payload)
                with self.assertRaises(main.APIError):
                    self.generate(session)
        session.post.return_value = response(status=401)
        with self.assertRaisesRegex(main.APIError, '401'):
            self.generate(session)
        session.post.return_value = response()
        session.post.return_value.json.side_effect = ValueError('bad JSON')
        with self.assertRaises(main.APIError):
            self.generate(session)

    def test_timeout_no_retry(self):
        session = Mock()
        session.post.side_effect = requests.Timeout('secret')
        with self.assertRaises(main.APIError) as caught:
            self.generate(session)
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_batch_continues_and_unique_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ref.png').write_bytes(image_bytes())
            table = root / 'input.csv'
            table.write_text('id,prompt,ref_img_path\n001,edit,missing.png\n001,edit,ref.png\n../../x,edit,ref.png\n', encoding='utf-8-sig')
            good = Image.open(BytesIO(image_bytes())).copy()
            with patch('main.generate_image', side_effect=[main.APIError('failed'), (good, 'r'), (good, 'r'), (good, 'r')]):
                directory, failures = main.run_from_table(table, root / 'out', key='secret', model='test', count=2)
            with (directory / 'result.csv').open(encoding='utf-8-sig') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(failures, 2)
            self.assertEqual([r['status'] for r in rows], ['error', 'error', 'success', 'success', 'success'])
            self.assertEqual(rows[0]['id'], '001')
            self.assertEqual(len(list((directory / 'images').glob('*.png'))), 3)
            with patch('main.generate_image', return_value=(good, 'r')):
                again, _ = main.run_from_table(table, root / 'out', key='secret', model='test')
            self.assertNotEqual(directory, again)

    def test_similarity_and_saved_on_metric_failure(self):
        image = Image.open(BytesIO(image_bytes()))
        self.assertEqual(main.calc_similarity(image, image), 1.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ref.png').write_bytes(image_bytes())
            table = root / 'input.csv'
            table.write_text('id,prompt,ref_img_path\n001,edit,ref.png\n')
            with patch('main.generate_image', return_value=(image, 'r')), patch('main.calc_similarity', side_effect=ValueError('metric')):
                directory, failures = main.run_from_table(table, root, key='secret', model='test')
            with (directory / 'result.csv').open(encoding='utf-8-sig') as handle:
                result = next(csv.DictReader(handle))
            self.assertEqual(result['status'], 'saved_without_similarity')
            self.assertTrue(Path(result['save_path']).exists())
            self.assertEqual(failures, 1)

    def test_config_validation_before_request(self):
        with patch.dict(os.environ, {}, clear=True), patch('main.run_from_table') as run:
            with self.assertRaises(SystemExit) as caught:
                main.main(['--csv', 'none.csv'])
            self.assertEqual(caught.exception.code, 2)
            run.assert_not_called()

    def test_bad_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            table = Path(temporary) / 'bad.csv'
            for text in ('wrong\nvalue\n', 'id,prompt,ref_img_path\n'):
                table.write_text(text)
                with self.assertRaises(ValueError):
                    main.read_table(table)


if __name__ == '__main__':
    unittest.main()
