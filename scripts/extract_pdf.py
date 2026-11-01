#!/usr/bin/env python3
"""Extract page-wise PDF text, with optional page images for visual inspection.

Arguments: PDF --out JSON [--render DIR] [--dpi DPI].
See README for course working-directory and skill-path conventions.
This does NOT verify embedded diagrams/formulas; those need visual inspection.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path


def _same_file(first: Path, second: Path) -> bool:
    """Recognize existing aliases as well as paths that do not exist yet."""
    return first.resolve() == second.resolve() or (
        first.exists() and second.exists() and first.samefile(second)
    )


def extract_pdf(pdf: Path, out: Path, render_dir: Path | None = None, dpi: int = 144) -> dict:
    """Write extraction JSON and optionally render every source page.

    Image paths in the JSON are relative to its directory, so a bundle containing
    both the JSON and its images can be moved together. Rendering does not record
    a completed visual review.
    """
    if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
        raise ValueError('DPI 必须为正整数')
    if not pdf.is_file():
        raise FileNotFoundError(f'找不到 PDF：{pdf}')
    if _same_file(pdf, out):
        raise ValueError('输出 JSON 不能覆盖源 PDF')
    try:
        import pymupdf
    except ImportError as exc:
        raise SystemExit('需要 PyMuPDF：python -m pip install pymupdf') from exc
    pages = []
    # Keep replacement characters visible instead of replacing them with font CIDs.
    text_flags = pymupdf.TEXTFLAGS_TEXT & ~getattr(pymupdf, 'TEXT_USE_CID_FOR_UNKNOWN_UNICODE', 0)
    with pymupdf.open(str(pdf)) as doc:
        render_paths = []
        relative_images = []
        if render_dir is not None:
            render_paths = [render_dir / f'page-{number:04d}.png'
                            for number in range(1, len(doc) + 1)]
            # Check every target before creating directories or writing any file.
            for path in render_paths:
                if _same_file(pdf, path):
                    raise ValueError('输出 PNG 不能覆盖源 PDF')
                if _same_file(out, path):
                    raise ValueError('输出 JSON 与 PNG 路径冲突')
                relative_images.append(Path(os.path.relpath(path.resolve(), out.parent.resolve())).as_posix())
            render_dir.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(doc):
            text = page.get_text('text', sort=True, flags=text_flags) or ''
            n_images = len(page.get_images(full=True))
            warnings = []
            if len(text.strip()) < 100:
                warnings.append('文字信息较少：可能是标题页或分隔页，建议核对源页')
            if n_images:
                warnings.append('包含图片：必须确认图中的内容和公式未被文本提取遗漏')
            if '�' in text:
                warnings.append('存在疑似提取乱码：核实字符/数学公式')
            record = {'page': i + 1, 'text': text, 'image_count': n_images, 'warnings': warnings}
            if render_dir is not None:
                page.get_pixmap(dpi=dpi).save(str(render_paths[i]))
                record['rendered_image'] = relative_images[i]
            pages.append(record)
    result = {'source_file': pdf.name, 'page_count': len(pages), 'pages': pages}
    if render_dir is not None:
        result['render_dpi'] = dpi
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def _positive_dpi(value: str) -> int:
    try:
        dpi = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('DPI 必须为正整数') from exc
    if dpi <= 0:
        raise argparse.ArgumentTypeError('DPI 必须为正整数')
    return dpi


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('pdf', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--render', type=Path, metavar='DIR', help='逐页 PNG 的输出目录（仍需视觉核对）')
    p.add_argument('--dpi', type=_positive_dpi, default=144, help='渲染 DPI，默认 144，必须为正整数')
    args = p.parse_args()
    try:
        result = extract_pdf(args.pdf, args.out, render_dir=args.render, dpi=args.dpi)
    except (OSError, ValueError) as exc:
        p.error(str(exc))
    pages = result['pages']
    flagged = [v['page'] for v in pages if v['warnings']]
    print(f'已提取 {len(pages)} 页 → {args.out}')
    print(f'需要视觉核对的页：{flagged if flagged else "未检测到明显问题，仍建议核对公式与图表"}')


if __name__ == '__main__':
    main()
