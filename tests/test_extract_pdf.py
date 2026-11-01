"""Exercise PDF extraction and optional rendering with real PDF fixtures."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'extract_pdf.py'


class ExtractPDFTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('pdf_extractor', SCRIPT)
        self.extractor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.extractor)
        self.tmp = tempfile.TemporaryDirectory(prefix='pdf-test-', dir=ROOT / 'tests')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.source = self.directory / '中文 源文件.pdf'
        self.output = self.directory / '交付 包' / '提取结果.json'
        self.renders = self.output.parent / '页面 图片'
        self.make_pdf(self.source)

    def make_pdf(self, path):
        with pymupdf.open() as doc:
            page = doc.new_page(width=240, height=160)
            page.insert_textbox(
                pymupdf.Rect(10, 10, 230, 150),
                'A readable page with ordinary text and no images. ' * 4,
                fontsize=10,
            )
            page = doc.new_page(width=240, height=160)
            page.insert_font(fontname='fixture', fontbuffer=pymupdf.Font('cjk').buffer)
            page.insert_text((10, 30), '标题页 □', fontname='fixture', fontsize=12)
            page = doc.new_page(width=240, height=160)
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
            image.clear_with(128)
            page.insert_image(pymupdf.Rect(10, 10, 140, 140), pixmap=image)
            doc.new_page(width=240, height=160)
            page = doc.new_page(width=240, height=160)
            page.insert_font(fontname='fixture', fontbuffer=pymupdf.Font('cjk').buffer)
            page.insert_text((10, 30), '替代字符 �', fontname='fixture', fontsize=12)
            doc.save(path)

    def extract(self, **kwargs):
        extract = getattr(self.extractor, 'extract_pdf', None)
        self.assertTrue(callable(extract), '需要可直接调用的 extract_pdf 接口')
        return extract(self.source, self.output, **kwargs)

    def cli(self, *args):
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run(
            [sys.executable, '-B', str(SCRIPT), *map(str, args)],
            cwd=self.directory, env=env, capture_output=True, text=True, encoding='utf-8',
        )

    def image_path(self, metadata, output=None):
        relative = metadata['rendered_image']
        self.assertFalse(PurePosixPath(relative).is_absolute())
        self.assertNotIn('\\', relative)
        return (output or self.output).parent / relative

    def test_default_extracts_json_without_rendering(self):
        result = self.extract()
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), result)
        self.assertEqual(result['source_file'], '中文 源文件.pdf')
        self.assertEqual(result['page_count'], 5)
        self.assertEqual([page['page'] for page in result['pages']], [1, 2, 3, 4, 5])
        self.assertIn('A readable page', result['pages'][0]['text'])
        self.assertGreaterEqual(len(result['pages'][0]['text'].strip()), 100)
        self.assertEqual(result['pages'][0]['warnings'], [])
        self.assertEqual(result['pages'][2]['image_count'], 1)
        self.assertEqual(result['pages'][3]['text'], '')
        self.assertNotIn('render_dpi', result)
        self.assertTrue(all('rendered_image' not in page for page in result['pages']))
        self.assertEqual(list(self.directory.rglob('*.png')), [])

    def test_short_title_and_blank_pages_suggest_review_without_scan_diagnosis(self):
        result = self.extract()
        for index in [1, 3]:
            with self.subTest(page=index + 1):
                warnings = ' '.join(result['pages'][index]['warnings'])
                self.assertIn('核', warnings)
                self.assertTrue('标题' in warnings or '分隔' in warnings)
                self.assertNotIn('扫描', warnings)

    def test_only_replacement_character_triggers_garbled_text_warning(self):
        result = self.extract()
        self.assertIn('□', result['pages'][1]['text'])
        self.assertFalse(any('乱码' in warning for warning in result['pages'][1]['warnings']))
        self.assertIn('�', result['pages'][4]['text'])
        self.assertTrue(any('乱码' in warning for warning in result['pages'][4]['warnings']))

    def test_image_only_page_keeps_image_review_warning(self):
        page = self.extract()['pages'][2]
        self.assertEqual(page['text'], '')
        self.assertTrue(any('图片' in warning for warning in page['warnings']))

    def test_default_rendering_writes_every_page_at_double_size(self):
        result = self.extract(render_dir=self.renders)
        self.assertEqual(result['render_dpi'], 144)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8')), result)
        self.assertEqual(sorted(path.name for path in self.renders.glob('*.png')),
                         [f'page-{number:04d}.png' for number in range(1, 6)])
        for page in result['pages']:
            with self.subTest(page=page['page']):
                self.assertEqual(page['rendered_image'], f"页面 图片/page-{page['page']:04d}.png")
                rendered = pymupdf.Pixmap(str(self.image_path(page)))
                self.assertEqual((rendered.width, rendered.height), (480, 320))
                self.assertNotIn('reviewed', page)
                self.assertNotIn('review_status', page)
        self.assertNotIn('reviewed', result)
        self.assertEqual(result['pages'][0]['warnings'], [])

    def test_configured_dpi_controls_pixel_dimensions(self):
        for dpi, dimensions in [(72, (240, 160)), (90, (300, 200))]:
            with self.subTest(dpi=dpi):
                result = self.extract(render_dir=self.renders, dpi=dpi)
                self.assertEqual(result['render_dpi'], dpi)
                rendered = pymupdf.Pixmap(str(self.image_path(result['pages'][0])))
                self.assertEqual((rendered.width, rendered.height), dimensions)

    def test_relative_inputs_and_sibling_render_directory_use_json_location(self):
        original = Path.cwd()
        try:
            os.chdir(self.directory)
            self.source = Path('中文 源文件.pdf')
            self.output = Path('交付 包') / '提取结果.json'
            result = self.extract(render_dir=Path('源页 图片'), dpi=72)
            self.assertEqual(result['pages'][0]['rendered_image'], '../源页 图片/page-0001.png')
            self.assertTrue(self.image_path(result['pages'][0]).is_file())
        finally:
            os.chdir(original)

    def test_render_paths_survive_moving_the_output_bundle(self):
        result = self.extract(render_dir=self.renders)
        moved = self.directory / '移动 后的交付包'
        shutil.move(str(self.output.parent), str(moved))
        moved_output = moved / self.output.name
        moved_result = json.loads(moved_output.read_text(encoding='utf-8'))
        self.assertEqual(moved_result, result)
        self.assertTrue(all(self.image_path(page, moved_output).is_file()
                            for page in moved_result['pages']))

    def test_invalid_dpi_is_rejected_before_creating_outputs(self):
        for dpi in [0, -1, 1.5, '72', True, None]:
            with self.subTest(dpi=dpi):
                with self.assertRaisesRegex(ValueError, 'DPI|dpi|正整数'):
                    self.extract(render_dir=self.renders, dpi=dpi)
                self.assertFalse(self.output.parent.exists())
        with self.assertRaises(ValueError):
            self.extract(dpi=0)
        self.assertFalse(self.output.parent.exists())

    def test_json_cannot_overwrite_the_source_pdf(self):
        original = self.source.read_bytes()
        self.output = self.source
        with self.assertRaisesRegex(ValueError, 'PDF|覆盖|源'):
            self.extract()
        self.assertEqual(self.source.read_bytes(), original)

    def test_render_cannot_overwrite_a_source_named_like_a_page(self):
        self.source.rename(self.directory / 'page-0001.png')
        self.source = self.directory / 'page-0001.png'
        original = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'PDF|覆盖|源'):
            self.extract(render_dir=self.directory)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(self.output.parent.exists())

    def test_json_cannot_overwrite_a_generated_png(self):
        self.output = self.renders / 'page-0002.png'
        with self.assertRaisesRegex(ValueError, 'JSON|PNG|冲突|覆盖'):
            self.extract(render_dir=self.renders)
        self.assertFalse(self.output.parent.exists())

    def test_missing_pdf_does_not_create_outputs(self):
        self.source = self.directory / 'missing.pdf'
        with self.assertRaises(FileNotFoundError):
            self.extract(render_dir=self.renders)
        self.assertFalse(self.output.parent.exists())

    def test_cli_keeps_json_only_default(self):
        completed = self.cli(self.source, '--out', self.output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertNotIn('render_dpi', result)
        self.assertEqual(list(self.directory.rglob('*.png')), [])

    def test_cli_render_and_dpi_options(self):
        for dpi in [None, 72, 90]:
            with self.subTest(dpi=dpi):
                args = [self.source, '--out', self.output, '--render', self.renders]
                if dpi is not None:
                    args.extend(['--dpi', dpi])
                completed = self.cli(*args)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                result = json.loads(self.output.read_text(encoding='utf-8'))
                self.assertEqual(result['render_dpi'], dpi or 144)
                self.assertTrue(all(self.image_path(page).is_file() for page in result['pages']))

    def test_cli_rejects_invalid_dpi_before_writing(self):
        for value in ['0', '-72', '1.5', 'invalid']:
            with self.subTest(dpi=value):
                completed = self.cli(self.source, '--out', self.output,
                                     '--render', self.renders, '--dpi', value)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn('正整数', completed.stderr)
                self.assertFalse(self.output.parent.exists())


if __name__ == '__main__':
    unittest.main()
