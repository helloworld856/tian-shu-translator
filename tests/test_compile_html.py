"""Contract tests for the actual offline artifact, not source text snapshots."""
from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'compile_html.py'


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.code = []
        self.text = []
        self.list_depth = 0
        self.link_depths = []
        self.in_code = False
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == 'ul':
            self.list_depth += 1
        if tag == 'a' and 'href' in dict(attrs):
            self.link_depths.append((dict(attrs)['href'], self.list_depth))
        if tag == 'code':
            self.in_code = True

    def handle_endtag(self, tag):
        if tag == 'ul':
            self.list_depth -= 1
        if tag == 'code':
            self.in_code = False

    def handle_data(self, data):
        self.text.append(data)
        if self.in_code:
            self.code.append(data)


class CompileHTMLTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), '缺少离线 HTML 编译器')
        spec = importlib.util.spec_from_file_location('course_compiler', SCRIPT)
        self.compiler = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.compiler)
        # Keep fixtures inside the writable workspace, not the host TEMP directory.
        self.tmp = tempfile.TemporaryDirectory(prefix='html-test-', dir=ROOT / 'tests')
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.source = self.directory / '中文母稿.md'
        self.target = self.directory / 'output' / '课程.html'

    def build(self, markdown, **kwargs):
        self.source.write_text(markdown, encoding='utf-8')
        stats = self.compiler.compile_course(self.source, self.target, **kwargs)
        text = self.target.read_text(encoding='utf-8')
        return stats, text, Page(text)

    def chapter(self, index, number=0):
        links = [attrs for tag, attrs in index.tags if tag == 'a' and 'chapter-entry' in attrs.get('class', '').split()]
        path = self.target.parent / unquote(urlsplit(links[number]['href']).path)
        text = path.read_text(encoding='utf-8')
        return path, text, Page(text)

    def assert_invalid(self, source, message):
        self.source.write_text(source, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, message):
            self.compiler.compile_course(self.source, self.target)
        self.assertFalse(self.target.exists(), '校验失败不应留下成品')

    def test_code_keeps_indentation_blank_lines_and_literal_dollars(self):
        _, _, page = self.build('# 一起看向量\n\n```python\ndef f():\n    price = "$5"\n\n    return price\n```\n\n行内代码 `$x$`。')
        code = ''.join(page.code)
        self.assertIn('    price = "$5"\n\n    return price', code)
        self.assertIn('$x$', code)
        self.assertFalse(any(tag == 'math' for tag, _ in page.tags))

    def test_inline_and_display_math_are_rendered_without_network(self):
        _, text, page = self.build('# 公式\n\n把 $x_1 + x_2$ 放在一起。\n\n$$\n\\frac{1}{2} + \\sqrt{x}\n$$')
        math = [attrs for tag, attrs in page.tags if tag == 'math']
        self.assertEqual(len(math), 2)
        self.assertTrue(any(attrs.get('display') == 'block' for attrs in math))
        self.assertIn('<mfrac>', text)
        self.assertIn('<msqrt>', text)
        self.assertFalse(any(attrs.get('src', '').startswith('http') for _, attrs in page.tags))

    def test_dollars_inside_escaped_text_and_code_do_not_become_math(self):
        _, _, page = self.build('# 说明\n\n价格为 \\$5。\n\n```text\n$$\nnot math\n$$\n```')
        self.assertIn('$$\nnot math\n$$', ''.join(page.code))
        self.assertFalse(any(tag == 'math' for tag, _ in page.tags))

    def test_inline_matrix_preserves_latex_row_separators(self):
        _, text, _ = self.build('# 矩阵\n\n矩阵 $\\begin{pmatrix}1&2\\\\3&4\\end{pmatrix}$ 有两行。')
        self.assertEqual(text.count('<mtr>'), 2)
        self.assertNotIn('klzzwxh', text)

    def test_display_delimiters_in_indented_and_raw_code_remain_literal(self):
        _, _, page = self.build('# 代码\n\n    $$\n    x + y\n    $$\n\n<pre><code>$$\nx + y\n$$</code></pre>')
        self.assertEqual(''.join(page.code).count('$$'), 4)
        self.assertFalse(any(tag == 'math' for tag, _ in page.tags))

    def test_inline_svg_cannot_load_remote_image_or_symbol(self):
        for tag, attr in [('image', 'href'), ('use', 'href'), ('image', 'xlink:href')]:
            with self.subTest(tag=tag, attr=attr):
                self.assert_invalid(f'# 图\n\n<svg><{tag} {attr}="https://example.com/image.svg"></{tag}></svg>', '离线|资源|本地')

    def test_svg_paint_cannot_depend_on_a_remote_file(self):
        self.assert_invalid('# 图\n\n<svg><rect fill="url(https://example.com/paint.svg#gradient)"></rect></svg>', '离线|资源|本地')

    def test_escaped_dollar_inside_math_does_not_capture_surrounding_text(self):
        _, text, page = self.build(r'# Prices' + '\n\n' + r'Price $p=\$5$ and $q=2$.')
        self.assertEqual(sum(tag == 'math' for tag, _ in page.tags), 2)
        self.assertIn('</math> and <math', text)
        self.assertNotIn('klzzwxh', text)

    def test_mathematics_in_course_title_keeps_its_structure(self):
        _, text, page = self.build(r'# The fraction $\frac{a}{b}$' + '\n\n正文。')
        self.assertTrue(any(tag == 'mfrac' for tag, _ in page.tags))
        self.assertIn('<mfrac>', text.split('</h1>', 1)[0])

    def test_toc_marker_does_not_create_duplicate_navigation(self):
        _, text, _ = self.build('# Course\n\n[TOC]\n\n## Lesson\n\nText.')
        sidebar = Page(text.split('<aside', 1)[1].split('</aside>', 1)[0])
        lesson_links = [attrs for tag, attrs in sidebar.tags if tag == 'a' and urlsplit(attrs.get('href', '')).fragment == 'lesson']
        self.assertEqual(len(lesson_links), 1)

    def test_exercise_without_a_solution_is_not_a_complete_artifact(self):
        self.assert_invalid('# 练习\n\n<section class="exercise" id="q1">题干</section>', '解析|答案|solution')

    def test_solution_cannot_be_open_before_the_student_tries(self):
        self.assert_invalid('# 练习\n\n<section class="exercise" id="q1"><details class="solution" open><summary>解答</summary>结果</details></section>', '折叠|收起|open')

    def test_prerequisite_opens_in_a_new_tab_without_a_return_anchor(self):
        stats, text, page = self.build('# 术语\n\n正文保持原处。\n\n<a class="term" id="mention" href="#rank">秩</a>\n\n<section class="prerequisite" id="rank" markdown="1">\n## 先理解秩\n\n只属于小课堂的解释 $x^2$。\n</section>')
        link = next(attrs for tag, attrs in page.tags if tag == 'a' and attrs.get('class') == 'term')
        self.assertEqual(link.get('target'), '_blank')
        self.assertIn('noopener', link.get('rel', '').split())
        self.assertEqual(stats['lessons'], 1)
        self.assertNotIn('只属于小课堂的解释', text)
        self.assertFalse(any(attrs.get('id') == 'rank' for _, attrs in page.tags))
        lesson = self.target.parent / unquote(urlsplit(link['href']).path)
        self.assertTrue(lesson.is_file())
        lesson_text = lesson.read_text(encoding='utf-8')
        self.assertIn('只属于小课堂的解释', lesson_text)
        self.assertIn('<msup>', lesson_text)
        self.assertNotIn('正文保持原处', lesson_text)

    def test_multiple_mentions_share_a_lesson_and_cross_lesson_links_are_offline(self):
        _, _, page = self.build('''# 主文

<a class="term" id="one" href="#rank">秩</a>
<a class="term" id="two" href="#rank">秩</a>

<section class="prerequisite" id="rank" markdown="1">
## 秩

可以再看 <a class="term" id="three" href="#vector">向量</a>。
</section>

<section class="prerequisite" id="vector" markdown="1">
## 向量

向量的解释。
</section>
''')
        mentions = [attrs for tag, attrs in page.tags if tag == 'a' and attrs.get('class') == 'term']
        self.assertEqual(len(mentions), 2)
        self.assertEqual(mentions[0]['href'], mentions[1]['href'])
        lesson = self.target.parent / unquote(urlsplit(mentions[0]['href']).path)
        subpage = Page(lesson.read_text(encoding='utf-8'))
        onward = next(attrs for tag, attrs in subpage.tags if attrs.get('id') == 'three')
        self.assertEqual(onward['target'], '_blank')
        self.assertTrue((lesson.parent / unquote(urlsplit(onward['href']).path)).is_file())

    def test_nested_prerequisites_are_rejected_before_writing(self):
        self.assert_invalid('# 课程\n\n<section class="prerequisite" id="a"><section class="prerequisite" id="b">说明</section></section>', '嵌套|同级')

    def test_sidebar_indexes_course_chapters_but_not_separate_lessons(self):
        _, text, page = self.build('''# 课程

## 第一章：起步 {#chapter-one}

<a class="term" id="ref" href="#rank">秩</a>

### 一点例子 {#example}

正文。

## 第二章：继续 {#chapter-two}

正文。

<section class="prerequisite" id="rank" markdown="1">
## 小课堂 {#lesson-title}

独立说明。
</section>
''')
        self.assertTrue(any(tag == 'aside' and attrs.get('class') == 'reader-sidebar' for tag, attrs in page.tags))
        sidebar = Page(text.split('<aside', 1)[1].split('</aside>', 1)[0])
        destinations = ['#' + urlsplit(attrs['href']).fragment for tag, attrs in sidebar.tags if tag == 'a']
        self.assertEqual(destinations, ['#chapter-one', '#example', '#chapter-two'])
        self.assertTrue(any(tag == 'details' and attrs.get('class') == 'toc-disclosure' for tag, attrs in sidebar.tags))

    def test_chapter_index_decodes_math_entities_for_readable_labels(self):
        _, text, _ = self.build('# 课程\n\n## 到达 $(3,2)$\n\n正文。')
        sidebar = Page(text.split('<aside', 1)[1].split('</aside>', 1)[0])
        self.assertIn('(3,2)', ''.join(sidebar.text))

    def test_index_keeps_subtopics_under_their_course_chapter_after_extraction(self):
        _, text, _ = self.build('''# 课程

## 第一章 {#one}

正文。

<section class="prerequisite" id="rank" markdown="1">
## 小课堂

说明。
</section>

### 第一章的后续话题 {#subtopic}

正文。

## 第二章 {#two}

正文。
''')
        sidebar = Page(text.split('<aside', 1)[1].split('</aside>', 1)[0])
        self.assertEqual([('#' + urlsplit(href).fragment, depth) for href, depth in sidebar.link_depths], [('#one', 1), ('#subtopic', 2), ('#two', 1)])

    def test_chapters_are_separate_pages_and_index_has_no_chapter_body(self):
        stats, text, index = self.build('''# 课程

这是一段导言。

## 第一章 {#first}

第一章独有的教学正文。

### 一个小节 {#sub}

本章细节。

## 第二章 {#second}

第二章独有的教学正文。[回看第一章](#sub)
''')
        self.assertEqual(stats['chapters'], 2)
        self.assertIn('这是一段导言', text)
        self.assertNotIn('第一章独有的教学正文', text)
        self.assertNotIn('第二章独有的教学正文', text)
        first, first_text, first_page = self.chapter(index, 0)
        second, second_text, second_page = self.chapter(index, 1)
        self.assertNotEqual(first, second)
        self.assertIn('第一章独有的教学正文', first_text)
        self.assertNotIn('第二章独有的教学正文', first_text)
        self.assertIn('第二章独有的教学正文', second_text)
        self.assertNotIn('第一章独有的教学正文', second_text)
        cross = next(attrs for tag, attrs in second_page.tags if tag == 'a' and urlsplit(attrs.get('href', '')).fragment == 'sub')
        self.assertNotEqual(cross.get('target'), '_blank')
        self.assertEqual((second.parent / unquote(urlsplit(cross['href']).path)).resolve(), first.resolve())
        self.assertTrue(any(tag == 'a' and attrs.get('rel') == 'next' for tag, attrs in first_page.tags))
        self.assertTrue(any(tag == 'a' and attrs.get('rel') == 'prev' for tag, attrs in second_page.tags))
        self.assertTrue(any(tag == 'a' and attrs.get('class') == 'index-link' for tag, attrs in second_page.tags))

    def test_chapter_links_do_not_change_new_tab_behavior_of_prerequisites(self):
        _, _, index = self.build('''# 课程

## 第一章 {#first}

<a class="term" id="mention" href="#rank">秩</a>

## 第二章 {#second}

别的内容。

<section class="prerequisite" id="rank" markdown="1">
## 秩说明

只在小课堂里的内容。
</section>
''')
        chapter_path, _, chapter = self.chapter(index)
        link = next(attrs for tag, attrs in chapter.tags if tag == 'a' and attrs.get('class') == 'term')
        self.assertEqual(link['target'], '_blank')
        lesson = chapter_path.parent / unquote(urlsplit(link['href']).path)
        self.assertTrue(lesson.is_file())
        self.assertIn('只在小课堂里的内容', lesson.read_text(encoding='utf-8'))

    def test_chapter_heading_inside_exercise_is_rejected_without_broken_pages(self):
        self.assert_invalid('# 课程\n\n<section class="exercise" id="q" markdown="1">\n## 章节标题\n\n<details class="solution"><summary>解析</summary>答案</details>\n</section>', '顶层|章节')

    def test_index_title_link_is_routed_to_the_actual_chapter(self):
        _, text, index = self.build('# [课程](#first)\n\n## 第一章 {#first}\n\n正文。')
        header = Page(text.split('<header', 1)[1].split('</header>', 1)[0])
        link = next(attrs for tag, attrs in header.tags if tag == 'a')
        chapter_path, _, _ = self.chapter(index)
        self.assertEqual((self.target.parent / unquote(urlsplit(link['href']).path)).resolve(), chapter_path.resolve())
        self.assertNotEqual(link.get('target'), '_blank')

    def test_svg_hyperlink_to_another_chapter_uses_the_real_file(self):
        _, _, index = self.build('# 课程\n\n## 第一章 {#first}\n\n<svg><a xlink:href="#second"><text>继续</text></a></svg>\n\n## 第二章 {#second}\n\n正文。')
        first, _, page = self.chapter(index)
        second, _, _ = self.chapter(index, 1)
        link = next(attrs for tag, attrs in page.tags if tag == 'a' and 'xlink:href' in attrs)
        self.assertEqual((first.parent / unquote(urlsplit(link['xlink:href']).path)).resolve(), second.resolve())
        self.assertNotEqual(link.get('target'), '_blank')

    def test_local_course_anchor_does_not_open_a_duplicate_chapter_tab(self):
        _, _, index = self.build('# 课程\n\n## 第一章\n\n<a id="local-link" href="#sub" target="_blank">本章小节</a>\n\n### 小节 {#sub}\n\n正文。')
        _, _, page = self.chapter(index)
        link = next(attrs for tag, attrs in page.tags if attrs.get('id') == 'local-link')
        self.assertEqual(link['target'], '_self')

    def test_svg_resource_cannot_silently_reference_a_different_output_page(self):
        self.assert_invalid('''# 课程

<svg><defs><g id="arrow"><path d="M0 0L5 5"/></g></defs></svg>

<section class="prerequisite" id="rank" markdown="1">
## 说明

<svg><use href="#arrow"/></svg>
</section>
''', '资源|同页|跨页')

    def test_term_target_must_be_a_prerequisite(self):
        self.assert_invalid('# 术语\n\n<a class="term" id="mention" href="#rank">秩</a>\n\n<p id="rank">任意段落</p>', '术语|prerequisite')

    def test_local_image_is_embedded_so_output_can_move(self):
        (self.directory / '示意图.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><circle r="2"/></svg>', encoding='utf-8')
        _, _, page = self.build('# 图片\n\n![方向示意](示意图.svg)')
        image = next(attrs for tag, attrs in page.tags if tag == 'img')
        self.assertTrue(image['src'].startswith('data:image/svg+xml;base64,'))
        self.assertEqual(image['alt'], '方向示意')

    def test_terms_answers_tables_and_chinese_navigation_survive(self):
        _, _, index = self.build('''# 从这里开始

## 一起看个例子

<a id="mention-rank" class="term" href="#term-rank">矩阵的秩</a>

| x | y |
| --- | --- |
| 1 | 2 |

<section class="exercise" id="exercise-1" markdown="1">
题目：求和。

<details class="solution" markdown="1">
<summary>看看完整思路</summary>

答案是 **3**。
</details>
</section>

<section id="term-rank" class="prerequisite" markdown="1">
## 矩阵的秩

先看两个独立方向。

<a class="return-link" href="#mention-rank">回到刚才的位置</a>
</section>
''')
        _, text, page = self.chapter(index)
        ids = {attrs['id'] for _, attrs in page.tags if 'id' in attrs}
        self.assertNotIn('term-rank', ids)
        self.assertIn('mention-rank', ids)
        self.assertTrue(any(tag == 'table' for tag, _ in page.tags))
        solutions = [attrs for tag, attrs in page.tags if tag == 'details' and 'solution' in attrs.get('class', '')]
        self.assertEqual(len(solutions), 1)
        self.assertNotIn('open', solutions[0])
        self.assertIn('<strong>3</strong>', text)
        self.assertNotIn('markdown="1"', text)

    def test_missing_image_is_a_build_error(self):
        self.assert_invalid('# 图片\n\n![原图](missing.png)', '图片|image')

    def test_remote_images_do_not_silently_break_offline_reading(self):
        self.assert_invalid('# 图片\n\n![远程图](https://example.com/figure.png)', '离线|本地|remote')

    def test_broken_term_link_is_a_build_error(self):
        self.assert_invalid('# 术语\n\n<a class="term" id="ref" href="#missing">秩</a>', 'missing|锚点|链接')

    def test_duplicate_ids_are_a_build_error(self):
        self.assert_invalid('# 内容\n\n<p id="same">一</p>\n<p id="same">二</p>', 'same|重复')

    def test_script_dependencies_in_the_manuscript_are_rejected(self):
        self.assert_invalid('# 内容\n\n<script src="https://example.com/math.js"></script>', 'script|脚本|资源')

    def test_title_is_escaped_without_creating_markup(self):
        stats, text, _ = self.build('# 内容\n\n正文。', title='向量 <入门> & 例题')
        self.assertEqual(stats['title'], '向量 <入门> & 例题')
        self.assertIn('<title>向量 &lt;入门&gt; &amp; 例题</title>', text)
        self.assertNotIn('<入门>', text)

    def test_input_cannot_be_overwritten(self):
        original = '# 不能覆盖\n\n正文。'
        self.source.write_text(original, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '覆盖|相同|源'):
            self.compiler.compile_course(self.source, self.source)
        self.assertEqual(self.source.read_text(encoding='utf-8'), original)

    def test_invalid_build_keeps_previous_output_intact(self):
        self.target.parent.mkdir()
        self.target.write_text('previous artifact', encoding='utf-8')
        self.source.write_text('[术语](#missing)', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.compiler.compile_course(self.source, self.target)
        self.assertEqual(self.target.read_text(encoding='utf-8'), 'previous artifact')

    def test_course_folder_contains_fixed_entry_chapters_and_lessons(self):
        self.source.write_text('''# 课程

## 第一章 {#first}

<a class="term" id="mention" href="#rank">秩</a>

## 第二章 {#second}

正文。

<section class="prerequisite" id="rank" markdown="1">
### 秩

解释。
</section>
''', encoding='utf-8')
        folder = self.directory / '课程文件夹'
        stats = self.compiler.build_course(self.source, folder)
        self.assertEqual(stats['chapters'], 2)
        self.assertTrue((folder / 'index.html').is_file())
        self.assertTrue((folder / 'chapters' / 'chapter-01.html').is_file())
        self.assertTrue((folder / 'chapters' / 'chapter-02.html').is_file())
        self.assertEqual(len(list((folder / 'lessons').glob('*.html'))), 1)
        self.assertEqual({p.name for p in folder.iterdir()}, {'index.html', 'chapters', 'lessons'})
        self.assertEqual({p.name for p in self.directory.iterdir()}, {self.source.name, folder.name})

    def test_course_folder_can_move_without_its_input_files(self):
        image = self.directory / 'diagram.svg'
        image.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle r="3"/></svg>', encoding='utf-8')
        self.source.write_text('''# 课程

## 第一章 {#first}

![示意图](diagram.svg)

<a class="term" id="mention" href="#rank">秩</a>

## 第二章 {#second}

[回看](#first)

<section class="prerequisite" id="rank" markdown="1">
### 秩

公式 $x^2$。
</section>
''', encoding='utf-8')
        original = self.directory / '原课程'
        moved = self.directory / '移到这里的新课程名'
        self.compiler.build_course(self.source, original)
        self.assertTrue(original.resolve().is_relative_to(self.directory.resolve()))
        self.assertTrue(moved.resolve().is_relative_to(self.directory.resolve()))
        original.rename(moved)
        self.source.unlink()
        image.unlink()
        for html in moved.rglob('*.html'):
            text = html.read_text(encoding='utf-8')
            self.assertNotIn(str(original), text)
            for tag, attrs in Page(text).tags:
                if tag == 'img':
                    self.assertTrue(attrs['src'].startswith('data:image/'))
                if tag != 'a' or not attrs.get('href'):
                    continue
                link = urlsplit(attrs['href'])
                if link.scheme or link.netloc:
                    continue
                destination = (html.parent / unquote(link.path)).resolve() if link.path else html.resolve()
                self.assertTrue(destination.is_relative_to(moved.resolve()))
                self.assertTrue(destination.is_file(), attrs['href'])
                if link.fragment:
                    ids = {values.get('id') for _, values in Page(destination.read_text(encoding='utf-8')).tags}
                    self.assertIn(unquote(link.fragment), ids)

    def test_folder_can_hold_its_own_work_manuscript(self):
        folder = self.directory / '课程'
        work = folder / '_work'
        work.mkdir(parents=True)
        source = work / '母稿.md'
        source.write_text('# 课程\n\n## 第一章\n\n正文。', encoding='utf-8')
        self.compiler.build_course(source, folder)
        self.assertTrue(source.is_file())
        self.assertTrue((folder / 'index.html').is_file())

    def test_cli_accepts_one_output_folder(self):
        self.source.write_text('# 课程\n\n## 第一章\n\n正文。', encoding='utf-8')
        folder = self.directory / '交付课程'
        result = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), str(self.source), str(folder)], capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(folder), result.stdout)
        self.assertTrue((folder / 'index.html').is_file())
        self.assertFalse(self.target.exists())

    def test_folder_api_rejects_an_html_filename_or_an_existing_file(self):
        self.source.write_text('# 课程\n\n正文。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '文件夹|目录'):
            self.compiler.build_course(self.source, self.directory / 'course.html')
        with self.assertRaisesRegex(ValueError, '文件夹|目录'):
            self.compiler.build_course(self.source, self.source)
        self.assertEqual(self.source.read_text(encoding='utf-8'), '# 课程\n\n正文。')

    def test_resolved_child_page_cannot_escape_the_course_folder(self):
        self.source.write_text('# 课程\n\n## 第一章\n\n正文。', encoding='utf-8')
        folder = self.directory / '课程'
        child = folder / 'chapters' / 'chapter-01.html'
        outside = self.directory / '外部文件.html'
        outside.write_text('保持原样', encoding='utf-8')
        resolve = Path.resolve

        def resolved_destination(path, *args, **kwargs):
            # Windows forbids creating symlinks here. Model only the OS path
            # resolution boundary; actual compilation and writes remain real.
            return outside if path == child else resolve(path, *args, **kwargs)

        with patch.object(Path, 'resolve', resolved_destination):
            with self.assertRaisesRegex(ValueError, '文件夹|目录'):
                self.compiler.build_course(self.source, folder)
        self.assertFalse((folder / 'index.html').exists())
        self.assertFalse(child.exists())
        self.assertEqual(outside.read_text(encoding='utf-8'), '保持原样')


if __name__ == '__main__':
    unittest.main()
