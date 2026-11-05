#!/usr/bin/env python3
"""Compile an offline course index, individual chapters and prerequisite pages.

Arguments: input.md OUTPUT_FOLDER [--title TITLE].
See README for course working-directory and skill-path conventions.
Markdown may contain semantic HTML (sections, details, inline SVG/MathML).
LaTeX is converted at build time; no CDN, server or math script is needed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from html import escape, unescape
from html.parser import HTMLParser
import mimetypes
from pathlib import Path
import re
import posixpath
import unicodedata
from urllib.parse import quote, unquote, urlsplit
import xml.etree.ElementTree as ET


ASSETS = Path(__file__).resolve().parents[1] / 'assets'
INSTALL_HELP = '请在项目环境执行：python -m pip install -r requirements.txt'


class ChapterMapPlacement(HTMLParser):
    """Keep a map at a chapter's entrance, outside answers and classrooms."""

    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.ids = set()
        self.chapter = False
        self.after_heading = False
        self.maps = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get('id'):
            self.ids.add(values['id'])
        if tag == 'figure' and 'chapter-map' in values.get('class', '').split():
            if self.stack or not self.chapter or not self.after_heading or self.maps:
                raise ValueError('chapter-map 关系图每章最多一张，必须紧跟顶层 ## 标题，不能放进正文、折叠答案或小课堂')
            self.maps += 1
        if not self.stack:
            if tag == 'h2':
                self.chapter = True
                self.maps = 0
            self.after_heading = False
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack and self.stack[-1] == tag:
            self.stack.pop()
            if tag == 'h2' and not self.stack:
                self.after_heading = True

    def handle_data(self, data):
        if not self.stack and data.strip():
            self.after_heading = False


def _chapter_map_svg(value: str, prefix: str) -> str:
    """Render the author's small, explicit graph without runtime dependencies."""
    def invalid(reason):
        raise ValueError('chapter-map 关系图：' + reason)

    def short_text(value, field, limit):
        if (not isinstance(value, str) or not value.strip() or len(value) > limit
                or any(unicodedata.category(char) in {'Cc', 'Cs'} for char in value)):
            invalid(f'{field} 须为 1–{limit} 字符的非空单行字符串')
        return value.strip()

    try:
        graph = json.loads(value)
    except (ValueError, RecursionError) as exc:
        invalid(f'JSON 无法解析，请检查引号和逗号（{exc}）')
    if not isinstance(graph, dict) or set(graph) != {'nodes', 'edges'}:
        invalid('JSON 必须只包含 nodes 和 edges')
    nodes, edges = graph['nodes'], graph['edges']
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 8:
        invalid('nodes 需要 1–8 个唯一节点；请简化关系图')
    if not isinstance(edges, list) or len(edges) > 12:
        invalid('edges 需要 0–12 条关系；请简化关系图')
    labels = {}
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {'id', 'label'}:
            invalid('每个节点必须只包含 id 和 label')
        identifier = short_text(node['id'], '节点 id', 48)
        label = short_text(node['label'], '节点 label', 24)
        if identifier in labels:
            invalid(f'节点 id 重复：{identifier}')
        labels[identifier] = label
    relations, pairs = [], set()
    for edge in edges:
        if not isinstance(edge, dict) or set(edge) != {'from', 'to', 'relation'}:
            invalid('每条关系必须只包含 from、to 和 relation')
        start = short_text(edge['from'], 'from', 48)
        end = short_text(edge['to'], 'to', 48)
        relation = short_text(edge['relation'], 'relation', 12)
        if start not in labels or end not in labels:
            invalid('关系的 from/to 必须引用已定义节点')
        if start == end:
            invalid('不支持自连线；请简化关系图')
        pair = frozenset((start, end))
        if pair in pairs:
            invalid('同一对节点只能声明一条关系；请合并重复或矛盾关系')
        pairs.add(pair)
        relations.append((start, end, relation))

    # Parallel concepts share a layer. Contract their groups before checking
    # directed relations, so parallel + prerequisite contradictions fail too.
    parents = {identifier: identifier for identifier in labels}

    def group(identifier):
        while parents[identifier] != identifier:
            identifier = parents[identifier]
        return identifier

    for start, end, relation in relations:
        if relation == '并列':
            parents[group(end)] = group(start)
    members = {}
    for identifier in labels:
        members.setdefault(group(identifier), []).append(identifier)
    outgoing = {identifier: set() for identifier in members}
    incoming = {identifier: 0 for identifier in members}
    for start, end, relation in relations:
        if relation == '并列':
            continue
        a, b = group(start), group(end)
        if a == b:
            invalid('并列节点之间不能同时声明有向关系；请简化关系图')
        if b not in outgoing[a]:
            outgoing[a].add(b)
            incoming[b] += 1
    queue = [identifier for identifier in members if incoming[identifier] == 0]
    ranks = {identifier: 0 for identifier in members}
    visited = 0
    for identifier in queue:
        visited += 1
        for child in members:
            if child not in outgoing[identifier]:
                continue
            ranks[child] = max(ranks[child], ranks[identifier] + 1)
            incoming[child] -= 1
            if incoming[child] == 0:
                queue.append(child)
    if visited != len(members):
        invalid('有向关系形成环；请简化为无环关系图')
    layers = [[] for _ in range(max(ranks.values()) + 1)]
    for identifier, children in members.items():
        layers[ranks[identifier]].extend(children)

    def units(text):
        return sum(2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1 for char in text)

    def lines(text):
        result, line = [], ''
        for char in text:
            if line and units(line + char) > 16:
                result.append(line)
                line = ''
            line += char
        return result + [line]

    wrapped = {identifier: lines(label) for identifier, label in labels.items()}
    node_width = 150
    node_height = max(56, max(len(value) for value in wrapped.values()) * 20 + 16)
    relation_width = max((units(relation) * 7 for _, _, relation in relations), default=0)
    horizontal_gap = max(64, relation_width + 20)
    horizontal = (len(labels) <= 3 and all(len(layer) == 1 for layer in layers)
                  and len(labels) * node_width + (len(labels) - 1) * horizontal_gap + 48 <= 704)
    positions = {}
    edge_tracks, peer_tracks, skip_lanes = {}, {}, {}
    if horizontal:
        width = len(layers) * node_width + (len(layers) - 1) * horizontal_gap + 48
        height = node_height + 64
        for rank, layer in enumerate(layers):
            positions[layer[0]] = (24 + rank * (node_width + horizontal_gap), 32)
    else:
        # Extra horizontal room between peers carries a readable parallel label.
        peer_gap = max(40, 2 * relation_width - 126)
        if any(r == '并列' for _, _, r in relations):
            peer_gap = max(peer_gap, relation_width + 20)
        width = max(len(layer) for layer in layers) * (node_width + peer_gap) - peer_gap + 48
        height = len(layers) * node_height + (len(layers) - 1) * 48 + 64
        for rank, layer in enumerate(layers):
            layer_width = len(layer) * (node_width + peer_gap) - peer_gap
            for column, identifier in enumerate(layer):
                positions[identifier] = ((width - layer_width) / 2 + column * (node_width + peer_gap), 32 + rank * (node_height + 48))
        # Share a label track only when its horizontal boxes do not overlap.
        # Reserve separate gutters for long edges, rather than stacking text.
        tracks, above_tracks = {}, {}

        def take_track(bucket, center, text_width):
            left, right = center - text_width / 2 - 4, center + text_width / 2 + 4
            for track, intervals in enumerate(bucket):
                if all(right <= a or b <= left for a, b in intervals):
                    intervals.append((left, right))
                    return track
            bucket.append([(left, right)])
            return len(bucket) - 1

        for index, (start, end, relation) in enumerate(relations):
            rank, end_rank = ranks[group(start)], ranks[group(end)]
            x1, y1 = positions[start]
            x2, y2 = positions[end]
            center = (x1 + x2 + node_width) / 2
            text_width = units(relation) * 7 + 10
            if end_rank - rank > 1:
                skip_lanes[index] = len(skip_lanes)
            elif end_rank == rank:
                if any(other_y == y1 and min(x1, x2) < other_x < max(x1, x2)
                       for other_x, other_y in positions.values()):
                    peer_tracks[index] = take_track(above_tracks.setdefault(rank, []), center, text_width)
            else:
                edge_tracks[index] = take_track(tracks.setdefault(rank, []), center, text_width)
        layer_y = [32 + max(0, len(above_tracks.get(0, [])) - 1) * 24]
        for rank in range(1, len(layers)):
            gap = max(48, (len(tracks.get(rank - 1, [])) + 1) * 24)
            layer_y.append(layer_y[-1] + node_height + gap + len(above_tracks.get(rank, [])) * 24)
        for identifier, (x, _) in positions.items():
            positions[identifier] = (x, layer_y[ranks[group(identifier)]])
        height = layer_y[-1] + node_height + 32
        base_width, lane_pitch = width, relation_width + 34
        if skip_lanes:
            width += 24 + len(skip_lanes) * lane_pitch

    paths, relation_labels, node_markup = [], [], []
    for index, (start, end, relation) in enumerate(relations):
        x1, y1 = positions[start]
        x2, y2 = positions[end]
        skip = ranks[group(end)] - ranks[group(start)] > 1
        if horizontal or y1 == y2:
            direction = 1 if x2 > x1 else -1
            a = x1 + (node_width if direction == 1 else 0)
            b = x2 + (0 if direction == 1 else node_width)
            y = y1 + node_height / 2
            obstructed = any(other_y == y1 and min(x1, x2) < other_x < max(x1, x2)
                             for other_x, other_y in positions.values())
            if (horizontal and skip) or obstructed:
                gap = horizontal_gap if horizontal else peer_gap
                left, right = a + direction * gap / 3, b - direction * gap / 3
                above = y1 - 20 - peer_tracks.get(index, 0) * 24
                path = f'M {a:g} {y:g} L {left:g} {y:g} L {left:g} {above:g} L {right:g} {above:g} L {right:g} {y:g} L {b:g} {y:g}'
                label_x, label_y = (a + b) / 2, above + 4
            else:
                path = f'M {a:g} {y:g} L {b:g} {y:g}'
                label_x, label_y = (a + b) / 2, y - 11
        else:
            a, b = x1 + node_width / 2, x2 + node_width / 2
            top, bottom = y1 + node_height, y2
            middle = (top + bottom) / 2
            if skip:
                lane = base_width + 12 + (skip_lanes[index] + 0.5) * lane_pitch
                path = f'M {a:g} {top:g} L {a:g} {top + 12:g} L {lane:g} {top + 12:g} L {lane:g} {bottom - 12:g} L {b:g} {bottom - 12:g} L {b:g} {bottom:g}'
                label_x, label_y = lane, middle + 4
            else:
                lane = top + 24 + edge_tracks[index] * 24
                label_x, label_y = (a + b) / 2, lane + 4
                path = f'M {a:g} {top:g} Q {a:g} {lane:g}, {label_x:g} {lane:g} Q {b:g} {lane:g}, {b:g} {bottom:g}'
        marker = f' marker-end="url(#{prefix}-arrow)"' if relation != '并列' else ''
        paths.append(f'<path class="chapter-map-edge" d="{path}"{marker}></path>')
        label_width = units(relation) * 7 + 10
        relation_labels.append(
            f'<rect class="chapter-map-relation-bg" x="{label_x - label_width / 2:g}" y="{label_y - 13:g}" width="{label_width}" height="19" rx="3"></rect>'
            f'<text class="chapter-map-relation" x="{label_x:g}" y="{label_y:g}" text-anchor="middle">{escape(relation)}</text>')
    for identifier, label_lines in wrapped.items():
        x, y = positions[identifier]
        center = x + node_width / 2
        baseline = y + node_height / 2 - (len(label_lines) - 1) * 10 + 6
        text = ''.join(f'<tspan x="{center:g}" y="{baseline + line_number * 20:g}">{escape(line)}</tspan>' for line_number, line in enumerate(label_lines))
        node_markup.append(
            f'<g class="chapter-map-node"><rect x="{x:g}" y="{y:g}" width="{node_width}" height="{node_height}" rx="8"></rect>'
            f'<text text-anchor="middle">{text}</text></g>')
    description = '节点：' + '、'.join(labels.values()) + '。关系：' + (
        '；'.join(f'{labels[start]} {relation} {labels[end]}' for start, end, relation in relations) if relations else '无连线') + '。'
    return (
        '<figure class="chapter-map"><figcaption>这一章的知识关系</figcaption>'
        '<div class="chapter-map-canvas" tabindex="0">'
        f'<svg role="img" aria-labelledby="{prefix}-title {prefix}-desc" viewBox="0 0 {width:g} {height:g}" width="{width:g}" height="{height:g}" style="--map-width: {width:g}px">'
        f'<title id="{prefix}-title">这一章的知识关系</title><desc id="{prefix}-desc">{escape(description)}</desc>'
        f'<defs><marker id="{prefix}-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path class="chapter-map-arrow" d="M 0 0 L 10 5 L 0 10 z"></path></marker></defs>'
        + ''.join(paths + relation_labels + node_markup) + '</svg></div></figure>'
    )


def _render_chapter_maps(body: str) -> str:
    # Only the exact fenced-code output is eligible; escaped examples inside
    # other code blocks cannot accidentally become a diagram.
    pattern = re.compile(r'<pre><code class="language-chapter-map">([^<]*)</code></pre>')
    inspector = ChapterMapPlacement()
    inspector.feed(body)
    reserved = inspector.ids
    number = 0

    def replace(match):
        nonlocal number
        while True:
            number += 1
            prefix = f'chapter-map-{number}'
            identifiers = {prefix + suffix for suffix in ('-title', '-desc', '-arrow')}
            if not identifiers & reserved:
                reserved.update(identifiers)
                return _chapter_map_svg(unescape(match.group(1)), prefix)

    result = pattern.sub(replace, body)
    inspector = ChapterMapPlacement()
    inspector.feed(result)
    return result


def _mathml(latex: str, display: str) -> str:
    try:
        from latex2mathml.converter import convert
    except ImportError as exc:
        raise ValueError(f'缺少 latex2mathml，不能排版公式。{INSTALL_HELP}') from exc
    try:
        value = convert(latex.strip(), display=display)
        root = ET.fromstring(value)
        # Unknown macros may be emitted as literal text instead of raising.
        if re.search(r'\\[A-Za-z]+', ''.join(root.itertext())):
            raise ValueError('存在不支持的 LaTeX 命令')
    except Exception as exc:
        raise ValueError(f'公式无法转换，请核对或改用已验证的 MathML/原图：{latex[:90]} ({exc})') from exc
    return value.replace('<math ', f'<math aria-label="{escape(latex.strip(), quote=True)}" ', 1)


def _render_markdown(source: str):
    try:
        import markdown
        from markdown.extensions import Extension
        from markdown.extensions.toc import slugify_unicode
        from markdown.inlinepatterns import InlineProcessor
        from markdown.blockprocessors import BlockProcessor
        from markdown.util import AtomicString
    except ImportError as exc:
        raise ValueError(f'缺少 Python Markdown。{INSTALL_HELP}') from exc

    class DisplayMath(BlockProcessor):
        def test(self, parent, block):
            # Four-space indentation belongs to code. Fenced/raw HTML code has
            # already been stashed by Markdown before block processing begins.
            return re.match(r'^ {0,3}\$\$', block) is not None

        def run(self, parent, blocks):
            text = blocks.pop(0)
            pattern = re.compile(r'^ {0,3}\$\$(.*?)\$\$[ \t]*(?:\n|$)', re.S)
            match = pattern.match(text)
            while match is None and blocks:
                text += '\n\n' + blocks.pop(0)
                match = pattern.match(text)
            if match is None:
                raise ValueError('块公式缺少结束的 $$，请检查母稿')
            rendered = _mathml(match.group(1), 'block')
            value = '<div class="math-block" tabindex="0" aria-label="公式">' + rendered + '</div>'
            paragraph = ET.SubElement(parent, 'p')
            paragraph.text = AtomicString(self.parser.md.htmlStash.store(value))
            remaining = text[match.end():]
            if remaining.strip():
                blocks.insert(0, remaining)

    class InlineMath(InlineProcessor):
        def handleMatch(self, match, data):
            rendered = _mathml(match.group(1), 'inline')
            return self.md.htmlStash.store(rendered), match.start(0), match.end(0)

    class MathExtension(Extension):
        def extendMarkdown(self, md):
            md.parser.blockprocessors.register(DisplayMath(md.parser), 'course_display_math', 90)
            # Backticks (190) protect code; capture LaTeX before escapes (180)
            # consume matrix row separators (\\). The pattern excludes \$.
            md.inlinePatterns.register(InlineMath(r'(?<!\\)\$(?!\$)((?:\\[^\n]|[^\n$\\])+?)(?<!\\)\$(?!\$)', md), 'course_inline_math', 185)

    converter = markdown.Markdown(
        extensions=['extra', 'sane_lists', 'toc', MathExtension()],
        extension_configs={'toc': {'toc_depth': '2-3', 'slugify': slugify_unicode}},
        output_format='html',
    )
    body = converter.convert(source)
    # The reader owns one navigation area even if a manuscript contains [TOC].
    body = _render_chapter_maps(body.replace(converter.toc, ''))
    return body, converter.toc_tokens, len(converter.toc_tokens)


class OfflineBody(HTMLParser):
    """Embed images and check the manuscript before writing an artifact.

    Manuscripts are authored course material, not arbitrary web applications.
    Reject resource loaders so the promised offline document has no hidden CDN.
    """

    def __init__(self, directory: Path):
        super().__init__(convert_charrefs=False)
        self.directory = directory
        self.output = []
        self.ids = set()
        self.links = []
        self.images = 0
        self.formulas = 0
        self.terms = 0
        self.exercises = 0
        self.term_targets = set()
        self.active_sections = []
        self.sections = []
        self.id_owners = {}
        self.details_depth = 0
        self.folded_headings = set()

    def _image(self, src: str) -> str:
        if src.startswith('data:image/'):
            return src
        parsed = urlsplit(src)
        if parsed.scheme or parsed.netloc:
            raise ValueError(f'离线图片必须先保存到本地：{src}')
        if parsed.query:
            raise ValueError(f'本地图片不应包含查询参数：{src}')
        path = (self.directory / unquote(parsed.path)).resolve()
        if not path.is_file():
            raise ValueError(f'找不到图片：{path}')
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'image/avif'}:
            raise ValueError(f'不支持的图片格式：{path.name}')
        data = path.read_bytes()
        if mime == 'image/svg+xml':
            svg = data.decode('utf-8-sig')
            if re.search(r'(?:href\s*=\s*[\'"]\s*(?!#|data:)|url\(\s*(?!#|data:)|<script\b)', svg, re.I):
                raise ValueError(f'SVG 图片含外部资源或脚本，先转为自包含图片：{path.name}')
        return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'link', 'style', 'iframe', 'object', 'embed', 'base', 'video', 'audio', 'source'}:
            raise ValueError(f'正文不支持 {tag} 资源；请使用本地图片、原生公式和内置阅读样式')
        values = dict(attrs)
        classes = values.get('class', '').split()
        identifier = values.get('id')
        if identifier is not None:
            if not identifier or identifier in self.ids:
                raise ValueError(f'空或重复的 ID：{identifier}')
            self.ids.add(identifier)
            owner = identifier if 'prerequisite' in classes else next(
                (section['id'] for section in reversed(self.active_sections) if 'prerequisite' in section['classes']), None)
            self.id_owners[identifier] = owner
            if tag == 'h3' and self.details_depth:
                self.folded_headings.add(identifier)
        for name, value in attrs:
            if name.startswith('on') or (name == 'style' and re.search(r'url\s*\(|@import', value or '', re.I)):
                raise ValueError(f'正文不支持脚本事件或外部样式资源：{name}')
            for reference in re.findall(r'url\(\s*[\'"]?([^\)\'\"]+)', value or '', re.I):
                reference = reference.strip()
                if not reference.startswith('#'):
                    raise ValueError(f'离线图形不能依赖外部资源：{reference}')
                self.links.append(unquote(reference[1:]))
            if name in {'srcset', 'poster'}:
                raise ValueError(f'请使用单一本地图片，不要设置 {name}')
            if name in {'href', 'xlink:href'} and value:
                parsed = urlsplit(value)
                if tag in {'image', 'feimage'}:
                    values[name] = self._image(value)
                    self.images += 1
                elif value.startswith('#'):
                    self.links.append(unquote(value[1:]))
                elif tag != 'a':
                    raise ValueError(f'离线图形仅支持本页资源引用：{value}')
                elif (parsed.scheme not in {'https', 'http', 'mailto'}
                      or (parsed.netloc == '' and parsed.scheme != 'mailto')):
                    raise ValueError(f'链接须为本页锚点或完整来源网址：{value}')
        if tag == 'img':
            if not values.get('alt', '').strip():
                raise ValueError('图片需要简短 alt 说明')
            values['src'] = self._image(values.get('src', ''))
            values.setdefault('loading', 'lazy')
            self.images += 1
        if tag == 'math':
            self.formulas += 1
        if 'term' in classes:
            if not identifier or not values.get('href', '').startswith('#'):
                raise ValueError('术语链接需要唯一 ID 和母稿中的小课堂目标')
            self.terms += 1
            self.term_targets.add(unquote(values['href'][1:]))
        if {'exercise', 'prerequisite'} & set(classes):
            if tag != 'section' or not identifier:
                raise ValueError('练习与术语说明需要使用带唯一 ID 的 section')
        if tag == 'section':
            if 'prerequisite' in classes and any('prerequisite' in item['classes'] for item in self.active_sections):
                raise ValueError('小课堂不能嵌套，请改为同级说明块并通过术语链接关联')
            record = {'id': identifier, 'classes': classes, 'solution': False, 'start': len(self.output)}
            self.sections.append(record)
            self.active_sections.append(record)
        if 'exercise' in classes:
            self.exercises += 1
        if tag == 'details' and {'hint', 'solution'} & set(classes):
            if 'open' in values:
                raise ValueError('提示和解析应默认折叠，请删除 open 属性')
            if 'solution' in classes:
                for section in self.active_sections:
                    if 'exercise' in section['classes']:
                        section['solution'] = True
        if tag == 'details':
            self.details_depth += 1
        svg_names = {'viewbox': 'viewBox', 'refx': 'refX', 'refy': 'refY',
                     'markerwidth': 'markerWidth', 'markerheight': 'markerHeight'} if tag in {'svg', 'marker'} else {}
        self.output.append('<' + tag + ''.join(
            f' {svg_names.get(name, name)}' if value is None else f' {svg_names.get(name, name)}="{escape(value, quote=True)}"'
            for name, value in values.items()
        ) + '>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'img', 'br', 'hr', 'input', 'meta', 'wbr'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'details':
            self.details_depth = max(0, self.details_depth - 1)
        if tag == 'section' and self.active_sections:
            section = self.active_sections.pop()
            section['end'] = len(self.output) + 1
        self.output.append(f'</{tag}>')

    def handle_data(self, data):
        self.output.append(data)

    def handle_entityref(self, name):
        self.output.append(f'&{name};')

    def handle_charref(self, name):
        self.output.append(f'&#{name};')

    def handle_comment(self, data):
        self.output.append(f'<!--{data}-->')

    def finish(self):
        missing = set(self.links) - self.ids
        if missing:
            raise ValueError('失效锚点链接：' + ', '.join(sorted(missing)))
        prerequisite_ids = {section['id'] for section in self.sections if 'prerequisite' in section['classes']}
        if self.term_targets - prerequisite_ids:
            raise ValueError('术语链接需要指向 prerequisite 讲解块：' + ', '.join(sorted(self.term_targets - prerequisite_ids)))
        for section in self.sections:
            if 'exercise' in section['classes'] and not section['solution']:
                raise ValueError(f'练习 {section["id"]} 缺少折叠的 solution 解析')
            if 'prerequisite' in section['classes'] and 'end' not in section:
                raise ValueError(f'小课堂 {section["id"]} 的 section 未闭合')
        return ''.join(self.output)


class PageLinks(HTMLParser):
    """Route original fragment links to their extracted offline page."""

    def __init__(self, current_page: str, locations: dict, lesson_pages=None, new_tab_pages=None):
        super().__init__(convert_charrefs=False)
        self.current_page = current_page
        self.locations = locations
        self.lesson_pages = set(lesson_pages or ())
        self.new_tab_pages = self.lesson_pages | set(new_tab_pages or ())
        self.output = []
        self.skip_return = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'a' and 'return-link' in values.get('class', '').split():
            self.skip_return = True  # Backward-compatible input; no in-page return now.
        if self.skip_return:
            return
        # SVG fragment resources cannot follow a classroom into another HTML
        # file. Fail before writing instead of quietly emitting a missing figure.
        if tag != 'a':
            references = [value[1:] for name, value in attrs
                          if name in {'href', 'xlink:href'} and value and value.startswith('#')]
            for _, value in attrs:
                references.extend(re.findall(r'url\(\s*[\'"]?#([^\)\'\"]+)', value or '', re.I))
            for identifier in references:
                destination = self.locations.get(unquote(identifier.strip()), self.current_page)
                if destination != self.current_page:
                    raise ValueError(f'图形资源 #{identifier} 跨页引用；请把定义和使用它的图放在同一页/同一小课堂内')
        link_attribute = 'href' if values.get('href') else 'xlink:href'
        if tag == 'a' and values.get(link_attribute, '').startswith('#'):
            identifier = unquote(values[link_attribute][1:])
            destination = self.locations.get(identifier, self.current_page)
            for name in ('href', 'xlink:href'):
                if not values.get(name, '').startswith('#'):
                    continue
                fragment_id = unquote(values[name][1:])
                fragment_page = self.locations.get(fragment_id, self.current_page)
                if fragment_page != self.current_page:
                    relative = posixpath.relpath(fragment_page, posixpath.dirname(self.current_page) or '.')
                    values[name] = quote(relative, safe='/') + '#' + quote(fragment_id, safe='')
            if destination in self.new_tab_pages:
                values['target'] = '_blank'
                values['rel'] = ' '.join(sorted(set(values.get('rel', '').split()) | {'noopener', 'noreferrer'}))
                values.setdefault('title', '在新标签页打开小课堂' if destination in self.lesson_pages else '在新标签页打开章节')
            else:
                values['target'] = '_self'
            self.output.append('<a' + ''.join(
                f' {name}' if value is None else f' {name}="{escape(value, quote=True)}"'
                for name, value in values.items()) + '>')
        else:
            self.output.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.skip_return:
            if tag == 'a':
                self.skip_return = False
            return
        self.output.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.skip_return:
            self.output.append(data)

    def handle_entityref(self, name):
        if not self.skip_return:
            self.output.append(f'&{name};')

    def handle_charref(self, name):
        if not self.skip_return:
            self.output.append(f'&#{name};')

    def handle_comment(self, data):
        if not self.skip_return:
            self.output.append(f'<!--{data}-->')

    def rewrite(self, value: str) -> str:
        self.feed(value)
        self.close()
        return ''.join(self.output)


class ChapterPages(HTMLParser):
    """Split only top-level chapter headings, preserving whole HTML blocks."""

    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, index_path: str, folder: str, reserved_ids=()):
        super().__init__(convert_charrefs=False)
        self.current = index_path
        self.folder = folder
        self.groups = {index_path: []}
        self.chapters = []
        self.id_pages = {}
        self.stack = []
        self.reserved_ids = set(reserved_ids)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'h2':
            if self.stack:
                raise ValueError('章节标题 ## 必须放在顶层，不能放进题目、details 或其它 HTML 容器；题内标题请用 ### 或更低层级')
            identifier = values.get('id')
            if not identifier:
                raise ValueError('章节标题需要唯一 ID；建议使用 Markdown 的 ## 标题')
            self.current = f'{self.folder}/chapter-{len(self.chapters) + 1:02d}.html'
            self.groups[self.current] = []
            self.chapters.append({'id': identifier, 'path': self.current})
        if values.get('id'):
            self.id_pages[values['id']] = self.current
        self.groups[self.current].append(self.get_starttag_text())
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError(f'正文 HTML 标签未正确闭合：{tag}')
        self.stack.pop()
        self.groups[self.current].append(f'</{tag}>')

    def handle_data(self, data):
        self.groups[self.current].append(data)

    def handle_entityref(self, name):
        self.groups[self.current].append(f'&{name};')

    def handle_charref(self, name):
        self.groups[self.current].append(f'&#{name};')

    def handle_comment(self, data):
        self.groups[self.current].append(f'<!--{data}-->')

    def split(self, value):
        self.feed(value)
        self.close()
        if self.stack:
            raise ValueError('正文 HTML 容器未闭合，不能拆成独立章节')
        if not self.chapters:
            identifier = 'body-chapter'
            while identifier in self.reserved_ids or identifier in self.id_pages:
                identifier += '-1'
            path = f'{self.folder}/chapter-01.html'
            self.groups[path] = [f'<h2 id="{identifier}">正文</h2>', *self.groups[self.current]]
            self.groups[self.current] = []
            self.id_pages = {key: path for key in self.id_pages}
            self.id_pages[identifier] = path
            self.chapters.append({'id': identifier, 'path': path})
        return {path: ''.join(tokens) for path, tokens in self.groups.items()}


def _compile_pages(source: Path, target: Path, title: str | None,
                   chapter_folder: str, lesson_folder: str,
                   output_root: Path | None = None) -> dict:
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError('输出路径不能与源稿相同，避免覆盖源文件')
    if target.suffix.lower() != '.html':
        raise ValueError('正式成品只支持 .html 输出')
    if not source.is_file():
        raise ValueError(f'找不到母稿：{source}')
    text = source.read_text(encoding='utf-8-sig')
    if not text.strip():
        raise ValueError('母稿为空，不能生成完整教程')
    body, toc_tokens, heading_count = _render_markdown(text)
    first_heading = re.search(r'<h1(?:\s[^>]*)?>(.*?)</h1>', body, re.S)
    heading_id = ''
    if first_heading:
        # Preserve structured mathematics in the visible heading; a plain
        # browser-tab title can use its accessible LaTeX label instead.
        heading_markup = first_heading.group(1)
        plain_heading = re.sub(r'<math\b[^>]*aria-label="([^"]*)"[^>]*>.*?</math>', lambda m: m.group(1), heading_markup, flags=re.S)
        inferred = unescape(re.sub(r'<[^>]+>', '', plain_heading))
        id_match = re.search(r'\bid="([^"]+)"', first_heading.group(0))
        heading_id = f' id="{id_match.group(1)}"' if id_match else ''
        body = body[:first_heading.start()] + body[first_heading.end():]
    else:
        inferred = source.stem
        heading_markup = escape(inferred)
    if title is not None:
        heading_markup = escape(title)
    title = title if title is not None else inferred
    if not title.strip():
        raise ValueError('教程标题不能为空')
    content = (
        f'<header class="site-header"><p class="subtitle">天书翻译官 · 自学读物</p><h1{heading_id}>{heading_markup}</h1></header>'
        f'<main id="content" class="course-content">{body}</main>'
    )
    parser = OfflineBody(source.parent)
    parser.feed(content)
    parser.close()
    content = parser.finish()
    css = (ASSETS / 'reader.css').read_text(encoding='utf-8')
    js = (ASSETS / 'reader.js').read_text(encoding='utf-8')

    def document(page_title, page_content, body_class=''):
        return (
            '<!doctype html>\n<html lang="zh-CN">\n<head>\n'
            '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{escape(page_title)}</title>\n<style>\n{css}\n</style>\n</head>\n<body class="{body_class}">\n'
            f'{page_content}\n<script>\n{js}\n</script>\n</body>\n</html>\n'
        )

    lessons = [section for section in parser.sections if 'prerequisite' in section['classes']]
    folder = lesson_folder
    lesson_paths = {section['id']: folder + '/lesson-' + hashlib.sha256(section['id'].encode('utf-8')).hexdigest()[:16] + '.html'
                    for section in lessons}
    main_tokens, last = [], 0
    for section in lessons:
        main_tokens.extend(parser.output[last:section['start']])
        last = section['end']
    main_tokens.extend(parser.output[last:])
    course_html = ''.join(main_tokens)
    index_header = re.search(r'<header\b.*?</header>', course_html, re.S).group(0)
    course_body = re.search(r'<main\b[^>]*>(.*)</main>', course_html, re.S).group(1)
    splitter = ChapterPages(target.name, chapter_folder, parser.ids)
    groups = splitter.split(course_body)
    # The entry page is only a chapter directory. Keep any manuscript preface
    # after the first chapter's map (when present), including its anchor targets.
    first_path = splitter.chapters[0]['path']
    introduction = groups[target.name]
    if introduction.strip():
        heading = re.search(r'<h2(?:\s[^>]*)?>.*?</h2>', groups[first_path], re.S)
        insertion = heading.end()
        entry_map = re.match(r'(?:\s|<!--.*?-->)*<figure class="chapter-map">.*?</figure>', groups[first_path][insertion:], re.S)
        if entry_map:
            insertion += entry_map.end()
        groups[first_path] = groups[first_path][:insertion] + introduction + groups[first_path][insertion:]
        for identifier, path in splitter.id_pages.items():
            if path == target.name:
                splitter.id_pages[identifier] = first_path
    groups[target.name] = ''
    locations = {identifier: lesson_paths[owner] if owner else splitter.id_pages.get(identifier, target.name)
                 for identifier, owner in parser.id_owners.items()}
    lesson_pages = set(lesson_paths.values())
    documents = {}
    for section in lessons:
        fragment = ''.join(parser.output[section['start']:section['end']])
        lesson_path = lesson_paths[section['id']]
        fragment = PageLinks(lesson_path, locations, lesson_pages).rewrite(fragment)
        heading = re.search(r'<h([1-6])(?:\s[^>]*)?>(.*?)</h\1>', fragment, re.S)
        lesson_title = unescape(re.sub(r'<[^>]+>', '', heading.group(2))) if heading else section['id']
        fragment = re.sub(r'<h([1-6])(\s[^>]*)?>(.*?)</h\1>', lambda m: f'<h1{m.group(2) or ""}>{m.group(3)}</h1>', fragment, count=1, flags=re.S)
        lesson_content = (
            '<a class="skip-link" href="#content">跳到正文</a>'
            '<header class="site-header"><p class="subtitle">先修小课堂</p></header>'
            f'<main id="content" class="course-content">{fragment}'
            '<p class="lesson-note">读完后，切回原来的教程标签页即可继续阅读。</p></main>'
        )
        documents[target.parent / lesson_path] = document(lesson_title, lesson_content, 'lesson-page')

    def flatten(tokens):
        for token in tokens:
            yield token
            yield from flatten(token.get('children', []))

    course_topics = [token for token in flatten(toc_tokens)
                     if token['level'] == 3 and parser.id_owners.get(token['id']) is None
                     and token['id'] not in parser.folded_headings]

    def page_href(destination, current_page, identifier=None):
        suffix = '#' + quote(identifier, safe='') if identifier else ''
        if destination == current_page:
            return suffix or '#content'
        relative = posixpath.relpath(destination, posixpath.dirname(current_page) or '.')
        return quote(relative, safe='/') + suffix

    def chapter_index(current_page):
        items = []
        for token in course_topics:
            if locations[token['id']] == current_page:
                href = page_href(current_page, current_page, token['id'])
                items.append(f'<li><a class="topic-link" href="{href}">{escape(unescape(token["name"]))}</a></li>')
        return '<ul>' + ''.join(items) + '</ul>' if items else ''

    def layout(current_page, page_content):
        toc = chapter_index(current_page) if current_page != target.name else ''
        sidebar = (
            '<aside class="reader-sidebar" aria-label="本章小节索引">'
            '<nav class="toc" aria-label="本章小节"><details class="toc-disclosure" open>'
            f'<summary>本章小节</summary>{toc}</details></nav></aside>'
        ) if toc else ''
        layout_class = 'reader-layout' if toc else 'reader-layout without-index'
        return ('<a class="skip-link" href="#content">跳到正文</a>'
                f'<div class="{layout_class}">{sidebar}<div class="reader-main">{page_content}</div></div>')

    chapter_list = []
    for position, chapter in enumerate(splitter.chapters):
        path = chapter['path']
        fragment = groups[path]
        heading = re.search(r'<h2(?:\s[^>]*)?>(.*?)</h2>', fragment, re.S)
        plain = re.sub(r'<math\b[^>]*aria-label="([^"]*)"[^>]*>.*?</math>', lambda m: m.group(1), heading.group(1), flags=re.S)
        chapter_title = unescape(re.sub(r'<[^>]+>', '', plain))
        fragment = re.sub(r'<h2(\s[^>]*)?>(.*?)</h2>', lambda m: f'<h1{m.group(1) or ""}>{m.group(2)}</h1>', fragment, count=1, flags=re.S)
        fragment = PageLinks(path, locations, lesson_pages).rewrite(fragment)
        controls = []
        if position:
            previous = splitter.chapters[position - 1]
            controls.append(f'<a rel="prev" href="{page_href(previous["path"], path, previous["id"])}">上一章</a>')
        controls.append(f'<a class="index-link" href="{page_href(target.name, path)}">课程目录</a>')
        if position + 1 < len(splitter.chapters):
            following = splitter.chapters[position + 1]
            controls.append(f'<a rel="next" href="{page_href(following["path"], path, following["id"])}">下一章</a>')
        page_content = (
            '<header class="site-header"><p class="subtitle">'
            f'<a href="{page_href(target.name, path)}">{escape(title)}</a></p></header>'
            f'<main id="content" class="course-content">{fragment}'
            f'<nav class="chapter-pagination" aria-label="章节翻页">{"".join(controls)}</nav></main>'
        )
        documents[target.parent / path] = document(chapter_title + ' · ' + title, layout(path, page_content), 'chapter-page')
        chapter_list.append(f'<li><a class="chapter-entry" href="{page_href(path, target.name, chapter["id"])}" target="_blank" rel="noopener noreferrer">{escape(chapter_title)}</a></li>')
    directory = '<nav class="course-directory" aria-label="课程章节"><ol class="chapter-list">' + ''.join(chapter_list) + '</ol></nav>'
    index_header = re.sub(r'<p class="subtitle">.*?</p>', '', index_header, count=1, flags=re.S)
    chapter_pages = {chapter['path'] for chapter in splitter.chapters}
    index_header = PageLinks(target.name, locations, lesson_pages, chapter_pages).rewrite(index_header)
    index_content = index_header + f'<main id="content" class="course-content">{directory}</main>'
    documents[target] = document(title, layout(target.name, index_content), 'index-page')
    if source in {path.resolve() for path in documents}:
        raise ValueError('输出页面不能覆盖源稿')
    if output_root is not None:
        for path in documents:
            if not path.resolve().is_relative_to(output_root):
                raise ValueError(f'页面必须保留在课程文件夹内：{path.name}')
    # All pages are rendered and validated before touching existing outputs.
    target.parent.mkdir(parents=True, exist_ok=True)
    for path, value in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
    return {'title': title, 'headings': heading_count, 'images': parser.images,
            'formulas': parser.formulas, 'term_links': parser.terms, 'exercises': parser.exercises,
            'lessons': len(lessons), 'chapters': len(splitter.chapters)}


def compile_course(source: Path, target: Path, title: str | None = None) -> dict:
    """Deprecated: use build_course(source, output_folder, title) for folder delivery.

    Retained only for existing callers that supply an explicit index HTML path.
    """
    target = Path(target)
    return _compile_pages(source, target, title, target.stem + '.chapters', target.stem + '.lessons')


def build_course(source: Path, output_folder: Path, title: str | None = None) -> dict:
    """Build one movable course folder with a fixed index.html entry point."""
    folder = Path(output_folder).resolve()
    if folder.is_file() or folder.suffix.lower() in {'.html', '.htm'}:
        raise ValueError('输出位置必须是课程文件夹，不是 HTML 文件；入口 index.html 会自动生成')
    # Existing junctions/symlinks must not scatter generated files outside the
    # folder that the reader will move. Work manuscripts inside _work are fine.
    for name in ('index.html', 'chapters', 'lessons'):
        destination = folder / name
        if not destination.resolve().is_relative_to(folder):
            raise ValueError(f'输出路径必须保留在课程文件夹内：{name}')
        if name != 'index.html' and destination.is_file():
            raise ValueError(f'课程文件夹内的 {name} 必须是目录')
        if name == 'index.html' and destination.is_dir():
            raise ValueError('课程入口 index.html 已被目录占用')
    stats = _compile_pages(source, folder / 'index.html', title, 'chapters', 'lessons', folder)
    return dict(stats, output_folder=str(folder), index=str(folder / 'index.html'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('markdown', type=Path, help='Markdown/语义 HTML 母稿')
    parser.add_argument('output_folder', type=Path, help='完整课程文件夹（自动生成 index.html、chapters/ 和可选的 lessons/）')
    parser.add_argument('--title', help='覆盖母稿标题')
    args = parser.parse_args()
    try:
        stats = build_course(args.markdown, args.output_folder, args.title)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'无法生成教程：{exc}\n')
    print(f'已生成课程文件夹：{stats["output_folder"]}')
    print('打开文件夹中的 index.html；移动或分享时直接带上整个课程文件夹。')
    print(f'独立章节 {stats["chapters"]} 个；小课堂 {stats["lessons"]} 个')
    print(f'公式 {stats["formulas"]}；图片 {stats["images"]}；术语入口 {stats["term_links"]}；练习 {stats["exercises"]}')
    print('结构检查通过；来源、题解准确性与课件覆盖仍需按清单核对。')


if __name__ == '__main__':
    main()
