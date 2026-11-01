"""Optional local-browser verification of a directory, chapters and lessons."""
from pathlib import Path
import json
import shutil
import tempfile
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
COURSE_FOLDER = ROOT / 'examples' / 'vector-course'
COURSE = COURSE_FOLDER / 'index.html'
PREVIEW = COURSE_FOLDER / '_preview'


def verify():
    results = {}
    PREVIEW.mkdir(exist_ok=True)
    with sync_playwright() as driver:
        browser = driver.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 1280, 'height': 960}, offline=True)
        requests, errors = [], []
        context.on('request', lambda request: requests.append(request.url))
        context.on('page', lambda tab: tab.on('pageerror', lambda error: errors.append(str(error))))
        page = context.new_page()
        page.goto(COURSE.as_uri())
        assert page.locator('.chapter-entry').count() == 3
        assert page.locator('pre, .exercise').count() == 0
        chapter_urls = [urljoin(page.url, link.get_attribute('href')) for link in page.locator('.chapter-entry').all()]
        assert len(set(chapter_urls)) == 3
        assert all('/chapters/' in url for url in chapter_urls)
        page.screenshot(path=str(PREVIEW / 'reader-index.png'), full_page=True)
        page.locator('.chapter-entry').first.click()
        page.wait_for_url(chapter_urls[0])
        assert len(context.pages) == 1
        assert page.locator('main h1').inner_text().startswith('第一章')
        assert page.locator('pre, .exercise, #term-rank').count() == 0
        results['index'] = 'directory, three separate files, same-tab chapter entry: pass'

        assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior') == 'smooth'
        page.emulate_media(reduced_motion='reduce')
        assert page.evaluate('getComputedStyle(document.documentElement).scrollBehavior') == 'auto'
        sidebar = page.locator('.reader-sidebar')
        assert '&#x' not in sidebar.inner_text()
        assert sidebar.bounding_box()['x'] + sidebar.bounding_box()['width'] <= page.locator('.reader-main').bounding_box()['x']
        assert page.locator('.chapter-link[aria-current=page]').count() == 1
        page.locator('a[rel=next]').click()
        page.wait_for_url(chapter_urls[1])
        assert page.locator('pre').count() == 1
        assert page.locator('.exercise').count() == 0
        assert page.locator('math mtable').count() > 0
        page.locator('a[rel=prev]').click()
        page.wait_for_url(chapter_urls[0])
        page.locator('.reader-sidebar .chapter-link').nth(1).click()
        page.wait_for_url(chapter_urls[1])
        topic = page.locator('.reader-sidebar .topic-link[href^="#"]').first
        fragment = topic.get_attribute('href')
        topic.click()
        page.wait_for_function('hash => location.hash === hash', arg=fragment)
        page.wait_for_function('hash => document.querySelector(".topic-link[aria-current=location]")?.getAttribute("href") === hash', arg=fragment)
        assert page.locator('.chapter-link[aria-current=page]').count() == 1
        assert len(context.pages) == 1
        page.goto(chapter_urls[1])
        page.screenshot(path=str(PREVIEW / 'reader-desktop.png'), full_page=True)
        results['navigation'] = 'sidebar, current chapter, local scroll, previous/next: pass'

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
            if mention == '#mention-rank':
                lesson.screenshot(path=str(PREVIEW / 'reader-lesson.png'), full_page=True)
            lesson.close()
        results['lessons'] = 'new tabs from different chapters, original position preserved: pass'

        assert page.locator('details.solution[open]').count() == 0
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
        results['reading'] = 'math, code, answers, print restore, return to index: pass'

        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto(COURSE.as_uri())
        assert page.locator('.toc-disclosure[open]').count() == 0
        page.locator('.toc-disclosure > summary').click()
        page.locator('.reader-sidebar .chapter-link').first.click()
        page.wait_for_url(chapter_urls[0])
        assert page.locator('.toc-disclosure[open]').count() == 0
        page.screenshot(path=str(PREVIEW / 'reader-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.evaluate("""() => {
            const block = document.createElement('div');
            block.className = 'math-block';
            block.innerHTML = '<math display="block"><mtext>' + 'x+'.repeat(160) + '</mtext></math>';
            document.querySelector('main').append(block);
        }""")
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        results['mobile'] = '390px index, chapter switching, collapsed menu, long equation: pass'

        no_js = browser.new_context(java_script_enabled=False, offline=True, reduced_motion='reduce')
        fallback = no_js.new_page()
        fallback.goto(COURSE.as_uri())
        fallback.locator('.chapter-entry').first.click()
        fallback.wait_for_url(chapter_urls[0])
        assert fallback.locator('math').count() > 10
        with no_js.expect_page() as opened:
            fallback.locator('#mention-rank').click()
        lesson = opened.value
        lesson.wait_for_load_state()
        assert lesson.locator('#term-rank').count() == 1
        assert fallback.url == chapter_urls[0]
        lesson.close()
        fallback.locator('a[rel=next]').click()
        fallback.wait_for_url(chapter_urls[1])
        assert fallback.locator('pre').count() == 1
        assert len(no_js.pages) == 1
        results['without_javascript'] = 'index, chapters, formulas and new-tab lessons: pass'

        # Exercise the actual delivered folder after relocation and renaming.
        with tempfile.TemporaryDirectory(prefix='moved-course-', dir=ROOT / 'tests') as temporary:
            temporary_path = Path(temporary).resolve()
            assert temporary_path.is_relative_to((ROOT / 'tests').resolve())
            moved = temporary_path / '移动后重命名的课程'
            shutil.copytree(COURSE_FOLDER, moved)
            relocated = browser.new_context(offline=True, reduced_motion='reduce')
            relocated.on('request', lambda request: requests.append(request.url))
            reader = relocated.new_page()
            reader.goto((moved / 'index.html').as_uri())
            reader.locator('.chapter-entry').first.click()
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
            relocated.close()
        results['moved_folder'] = 'renamed folder, index, chapters and new-tab lessons offline: pass'
        assert not errors, errors
        assert not any(url.startswith(('http:', 'https:')) for url in requests), requests
        results['network_requests'] = 0
        browser.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    verify()
