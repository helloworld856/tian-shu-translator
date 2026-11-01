"""Optional offline-browser verification of the delivered course folder."""
from pathlib import Path
import json
import shutil
import tempfile
from urllib.parse import unquote, urljoin, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
COURSE_FOLDER = ROOT / 'examples' / 'vector-course'
COURSE = COURSE_FOLDER / 'index.html'
PREVIEW = COURSE_FOLDER / '_preview'


def verify():
    results, requests, errors = {}, [], []
    PREVIEW.mkdir(exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel='msedge', headless=True)

        def new_context(**options):
            context = browser.new_context(offline=True, **options)
            context.on('request', lambda request: requests.append(request.url))
            context.on('page', lambda tab: tab.on('pageerror', lambda error: errors.append(str(error))))
            return context

        def open_chapter(context, index, number):
            entry = index.locator('.chapter-entry').nth(number)
            expected = urljoin(index.url, entry.get_attribute('href'))
            assert entry.get_attribute('target') == '_blank'
            assert {'noopener', 'noreferrer'} <= set(entry.get_attribute('rel').split())
            before_url, before_y = index.url, index.evaluate('window.scrollY')
            with context.expect_page() as opened:
                entry.click()
            chapter = opened.value
            chapter.wait_for_load_state()
            assert chapter.url == expected
            assert index.url == before_url
            assert abs(index.evaluate('window.scrollY') - before_y) < 2
            return chapter

        def assert_local_topics(chapter, expected_count):
            links = chapter.locator('.reader-sidebar a')
            assert links.count() == expected_count
            headings = chapter.locator('main h3').evaluate_all(
                'items => items.filter(item => !item.closest("details, .prerequisite")).map(item => item.id)')
            fragments = links.evaluate_all('items => items.map(item => item.getAttribute("href"))')
            assert all(fragment.startswith('#') for fragment in fragments), fragments
            assert [unquote(fragment[1:]) for fragment in fragments] == headings
            assert chapter.locator('.reader-sidebar .chapter-link').count() == 0

        def follow_local_topic(chapter):
            topic = chapter.locator('.reader-sidebar .topic-link').last
            fragment = topic.get_attribute('href')
            before_path = chapter.url.split('#', 1)[0]
            topic.click()
            chapter.wait_for_function('hash => location.hash === hash', arg=fragment)
            chapter.wait_for_function(
                'hash => document.querySelector(".topic-link[aria-current=location]")?.getAttribute("href") === hash',
                arg=fragment)
            assert chapter.url.split('#', 1)[0] == before_path
            assert chapter.evaluate('window.scrollY') > 0
            assert chapter.evaluate('''hash => {
                const heading = document.getElementById(decodeURIComponent(hash.slice(1)));
                return heading.getBoundingClientRect().top <= 180 ||
                    scrollY + innerHeight >= document.documentElement.scrollHeight - 2;
            }''', fragment)

        context = new_context(viewport={'width': 1280, 'height': 960})
        index = context.new_page()
        index.goto(COURSE.as_uri())
        assert index.locator('h1').count() == 1
        assert index.locator('.chapter-entry').count() == 3
        assert index.locator('.reader-sidebar, .toc, main h2, main h3, main p, main blockquote, pre, .exercise, .term').count() == 0
        assert '未提供原始资料' not in index.locator('main').inner_text()
        chapter_urls = [urljoin(index.url, link.get_attribute('href')) for link in index.locator('.chapter-entry').all()]
        assert len(set(chapter_urls)) == 3
        assert all('/chapters/' in url for url in chapter_urls)
        index.screenshot(path=str(PREVIEW / 'reader-index.png'), full_page=True)
        for number in range(3):
            opened = open_chapter(context, index, number)
            assert opened.locator('main h1').count() == 1
            assert opened.locator('main h1').inner_text().startswith(('第一章', '第二章', '第三章')[number])
            assert_local_topics(opened, (2, 2, 1)[number])
            if number == 0:
                page = opened
                assert page.locator('main blockquote').count() == 1
                assert '未提供原始资料' in page.locator('main').inner_text()
                assert page.locator('pre, .exercise, #term-rank').count() == 0
            else:
                opened.close()
        assert len(context.pages) == 2
        results['index'] = 'title and chapter entries only; all three open new tabs and preserve index: pass'

        assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior') == 'smooth'
        page.emulate_media(reduced_motion='reduce')
        assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior') == 'auto'
        for width in (1280, 1440):
            page.set_viewport_size({'width': width, 'height': 960})
            main = page.locator('.reader-main').bounding_box()
            sidebar = page.locator('.reader-sidebar').bounding_box()
            assert abs(main['x'] + main['width'] / 2 - width / 2) < 1, (width, main)
            assert sidebar['x'] >= 0
            assert sidebar['x'] + sidebar['width'] < main['x'], (width, sidebar, main)
            assert page.evaluate('''() => parseFloat(getComputedStyle(document.querySelector(".topic-link")).fontSize)
                < parseFloat(getComputedStyle(document.querySelector(".course-content p")).fontSize)''')
            assert page.locator('.toc-disclosure[open]').count() == 1
        page.set_viewport_size({'width': 1280, 'height': 960})
        page.locator('a[rel=next]').click()
        page.wait_for_url(chapter_urls[1])
        assert page.locator('pre').count() == 1
        assert page.locator('.exercise').count() == 0
        assert page.locator('math mtable').count() > 0
        assert_local_topics(page, 2)
        follow_local_topic(page)
        assert len(context.pages) == 2
        page.goto(chapter_urls[1])
        page.screenshot(path=str(PREVIEW / 'reader-desktop.png'), full_page=True)
        page.locator('a[rel=prev]').click()
        page.wait_for_url(chapter_urls[0])
        results['navigation'] = 'centered at 1280/1440px; small local sidebar, scroll/current topic, previous/next: pass'

        for chapter_url, mention in [(chapter_urls[0], '#mention-rank'), (chapter_urls[2], '#mention-rank-again')]:
            page.goto(chapter_url)
            page.locator(mention).scroll_into_view_if_needed()
            before_url, before_y = page.url, page.evaluate('window.scrollY')
            with context.expect_page() as opened:
                page.locator(mention).click()
            lesson = opened.value
            lesson.wait_for_load_state()
            assert '/lessons/' in lesson.url and lesson.url.endswith('#term-rank')
            assert lesson.locator('#term-rank').count() == 1
            assert lesson.locator('math').count() > 0
            assert lesson.locator('.reader-sidebar, .exercise').count() == 0
            assert page.url == before_url
            assert abs(page.evaluate('window.scrollY') - before_y) < 2
            assert index.url == COURSE.as_uri()
            if mention == '#mention-rank':
                lesson.screenshot(path=str(PREVIEW / 'reader-lesson.png'), full_page=True)
            lesson.close()
        results['lessons'] = 'new tabs from two chapters, chapter and index positions preserved: pass'

        assert page.locator('details.hint[open], details.solution[open]').count() == 0
        page.locator('details.hint > summary').click()
        page.locator('details.solution > summary').click()
        assert page.locator('details.solution[open]').count() == 1
        page.locator('details.solution > summary').click()
        page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
        assert page.locator('details:not([open])').count() == 0
        page.evaluate("window.dispatchEvent(new Event('afterprint'))")
        assert page.locator('details.hint[open]').count() == 1
        assert page.locator('details.solution[open]').count() == 0
        page.locator('a.index-link').click()
        page.wait_for_url(COURSE.as_uri())
        assert index.url == COURSE.as_uri()
        page.close()
        results['reading'] = 'math, code, folded answers, print restores state, return link: pass'

        for width, height in ((1024, 900), (390, 844)):
            index.set_viewport_size({'width': width, 'height': height})
            assert index.locator('.reader-sidebar').count() == 0
            assert index.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            chapter = open_chapter(context, index, 1)
            chapter.set_viewport_size({'width': width, 'height': height})
            chapter.emulate_media(reduced_motion='reduce')
            chapter.wait_for_function('!document.querySelector(".toc-disclosure").open')
            assert_local_topics(chapter, 2)
            chapter.locator('.toc-disclosure > summary').click()
            follow_local_topic(chapter)
            assert chapter.locator('.toc-disclosure[open]').count() == 0
            assert chapter.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            if width == 390:
                chapter.goto(chapter_urls[1])
                chapter.screenshot(path=str(PREVIEW / 'reader-mobile.png'), full_page=True)
                chapter.evaluate('''() => {
                    const block = document.createElement('div');
                    block.className = 'math-block';
                    block.innerHTML = '<math display="block"><mtext>' + 'x+'.repeat(160) + '</mtext></math>';
                    document.querySelector('main').append(block);
                }''')
                assert chapter.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            chapter.close()
        results['mobile'] = '1024/390px local menu collapses after navigation, no horizontal overflow: pass'

        no_js = new_context(java_script_enabled=False, reduced_motion='reduce')
        fallback_index = no_js.new_page()
        fallback_index.goto(COURSE.as_uri())
        fallback = open_chapter(no_js, fallback_index, 0)
        assert fallback.locator('math').count() > 10
        topic = fallback.locator('.topic-link').last
        fragment = topic.get_attribute('href')
        topic.click()
        fallback.wait_for_url(lambda url: unquote(urlsplit(url).fragment) == unquote(fragment[1:]))
        assert fallback.evaluate('window.scrollY') > 0
        before_url = fallback.url
        with no_js.expect_page() as opened:
            fallback.locator('#mention-rank').click()
        lesson = opened.value
        lesson.wait_for_load_state()
        assert lesson.locator('#term-rank').count() == 1
        assert fallback.url == before_url
        assert fallback_index.url == COURSE.as_uri()
        lesson.close()
        fallback.locator('a[rel=next]').click()
        fallback.wait_for_url(chapter_urls[1])
        assert fallback.locator('pre').count() == 1
        assert len(no_js.pages) == 2
        no_js.close()
        results['without_javascript'] = 'native new-tab entries, local anchors, formulas and new-tab lessons: pass'

        # Exercise the actual delivered folder after relocation and renaming.
        with tempfile.TemporaryDirectory(prefix='moved-course-', dir=ROOT / 'tests') as temporary:
            temporary_path = Path(temporary).resolve()
            assert temporary_path.is_relative_to((ROOT / 'tests').resolve())
            moved = temporary_path / '移动后重命名的课程'
            shutil.copytree(COURSE_FOLDER, moved, ignore=shutil.ignore_patterns('_preview'))
            relocated = new_context(reduced_motion='reduce')
            moved_index = relocated.new_page()
            moved_index.goto((moved / 'index.html').as_uri())
            reader = open_chapter(relocated, moved_index, 0)
            assert reader.url.startswith(moved.as_uri())
            assert reader.locator('math').count() > 10
            with relocated.expect_page() as opened:
                reader.locator('#mention-rank').click()
            separate = opened.value
            separate.wait_for_load_state()
            assert separate.url.startswith(moved.as_uri())
            assert separate.locator('#term-rank').count() == 1
            separate.close()
            reader.locator('a.index-link').click()
            reader.wait_for_url((moved / 'index.html').as_uri())
            assert reader.locator('.chapter-entry').count() == 3
            assert moved_index.url == (moved / 'index.html').as_uri()
            relocated.close()
        results['moved_folder'] = 'renamed folder retains index, chapters and new-tab lessons offline: pass'
        assert not errors, errors
        assert not any(url.startswith(('http:', 'https:')) for url in requests), requests
        results['network_requests'] = 0
        browser.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify()
