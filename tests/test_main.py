import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
import main


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        Image.new('RGB', (20, 20), 'red').save(self.root / 'ref.png')

    def table(self, text):
        path = self.root / 'input.csv'
        path.write_text(text, encoding='utf-8-sig')
        return path

    def test_prepare_relative_path_and_errors(self):
        table = self.table('id,prompt,ref_img_path\n001,"edit, please",ref.png\n002,edit,missing.png\n')
        directory, failures = main.prepare(table, self.root / 'out')
        rows = read(directory / 'tasks.csv')
        self.assertEqual(failures, 1)
        self.assertEqual(rows[0]['id'], '001')
        self.assertEqual(rows[0]['ref_img_path'], str(self.root / 'ref.png'))
        self.assertEqual(Path(rows[0]['prompt_file']).read_text(), 'edit, please')
        self.assertEqual(rows[1]['status'], 'error')

    def test_collect_continues_and_no_overwrite(self):
        table = self.table('id,prompt,ref_img_path,generated_img_path,seq\n001,edit,ref.png,missing.png,1\n../../x,edit,ref.png,ref.png,2\n001,edit,ref.png,ref.png,3\n')
        original = (self.root / 'ref.png').read_bytes()
        directory, failures = main.collect(table, self.root / 'out')
        rows = read(directory / 'result.csv')
        self.assertEqual(failures, 1)
        self.assertEqual([r['status'] for r in rows], ['error', 'success', 'success'])
        self.assertEqual(rows[1]['similarity'], '1.0')
        self.assertEqual(rows[1]['seq'], '2')
        self.assertEqual(len(list((directory / 'images').glob('*.png'))), 2)
        self.assertEqual((self.root / 'ref.png').read_bytes(), original)
        again, _ = main.collect(table, self.root / 'out')
        self.assertNotEqual(directory, again)

    def test_missing_reference_retains_archive(self):
        table = self.table('id,prompt,ref_img_path,generated_img_path\n001,edit,missing.png,ref.png\n')
        directory, failures = main.collect(table, self.root)
        row = read(directory / 'result.csv')[0]
        self.assertEqual(row['status'], 'saved_without_similarity')
        self.assertTrue(Path(row['save_path']).exists())
        self.assertEqual(failures, 1)

    def test_metric_failure_retains_archive(self):
        table = self.table('id,prompt,ref_img_path,generated_img_path\n001,edit,ref.png,ref.png\n')
        with patch('main.calc_similarity', side_effect=ValueError('metric error')):
            directory, _ = main.collect(table, self.root)
        self.assertEqual(read(directory / 'result.csv')[0]['status'], 'saved_without_similarity')

    def test_empty_path_and_corrupt_image(self):
        (self.root / 'bad.png').write_text('invalid')
        table = self.table('id,prompt,ref_img_path,generated_img_path\n001,edit,ref.png,\n002,edit,ref.png,bad.png\n')
        directory, failures = main.collect(table, self.root)
        self.assertEqual(failures, 2)
        self.assertTrue(all(r['status'] == 'error' for r in read(directory / 'result.csv')))

    def test_bad_table(self):
        for text in ('wrong\nvalue\n', 'id,prompt,ref_img_path\n'):
            with self.assertRaises(ValueError):
                main.prepare(self.table(text), self.root)

    def test_main_without_key_and_exit_codes(self):
        table = self.table('id,prompt,ref_img_path\n001,edit,ref.png\n')
        self.assertEqual(main.main(['prepare', '--csv', str(table), '--output-dir', str(self.root)]), 0)
        self.assertEqual(main.main(['collect', '--csv', str(table)]), 2)


if __name__ == '__main__':
    unittest.main()
