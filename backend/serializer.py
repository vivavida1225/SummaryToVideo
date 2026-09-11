"""Deterministic implementation of prompts/html to input text.txt."""

import argparse
import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment, NavigableString, Tag


BLOCK_TAGS = {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote',
              'article', 'section', 'header', 'footer', 'main', 'aside', 'nav',
              'pre', 'address', 'dl', 'dt', 'dd', 'ul', 'ol', 'figure', 'figcaption',
              'table', 'tr', 'td', 'th'}


class SerializationError(ValueError):
    pass


def _text(node: Tag) -> str:
    def walk(part):
        if isinstance(part, Comment):
            return ''
        if isinstance(part, NavigableString):
            return str(part)
        if part.name == 'br':
            return '\n'
        value = ''.join(walk(child) for child in part.children)
        return '\n' + value + '\n' if part.name in BLOCK_TAGS else value

    value = re.sub(r'[ \t]*[\r\n]+[ \t]*', ' ', walk(node)).strip()
    if not value:
        raise SerializationError(f'{node.name} 요소의 텍스트가 비어 있습니다.')
    return value


def _one(node: Tag, selector: str, location: str) -> Tag:
    matches = node.select(selector)
    if len(matches) != 1:
        raise SerializationError(f'{location}: {selector} 요소가 정확히 하나 필요합니다 (현재 {len(matches)}개).')
    return matches[0]


def serialize_html(html: str) -> str:
    if not html.strip():
        raise SerializationError('입력 HTML이 비어 있습니다.')
    soup = BeautifulSoup(html, 'html.parser')
    for element in list(soup.find_all(True)):
        if element.attrs is None:
            continue
        labels = ' '.join(element.get('class', []) + [element.get('id', '')]).lower()
        is_ad = re.search(r'(?:^|[\s_-])(?:ads?|advertisement|advertising|banner|youtube|ytplayer)(?:$|[\s_-])', labels)
        if element.name in {'script', 'style', 'iframe', 'noscript', 'template'} or is_ad:
            element.decompose()

    roots = []
    for header in soup.find_all('header'):
        root = header.find_parent(class_='embedded-content')
        if root is not None and all(root is not existing for existing in roots):
            roots.append(root)
    if not roots:
        raise SerializationError('header를 포함한 실제 뉴스 .embedded-content를 찾을 수 없습니다.')
    if len(roots) != 1:
        raise SerializationError('실제 뉴스 영역이 여러 개입니다. 한 개의 뉴스 HTML만 입력하세요.')
    root = roots[0]
    header = _one(root, 'header', '첫 장면')
    summary_box = _one(root, 'div.summary-box', '첫 장면')
    summaries = summary_box.select('p.summary')
    if not summaries:
        raise SerializationError('첫 장면: p.summary가 없습니다.')
    indices = _one(summary_box, '.indices', '첫 장면')
    cards = indices.select('.index-card')
    if not cards:
        raise SerializationError('첫 장면: .index-card가 없습니다.')
    index_lines = []
    for i, card in enumerate(cards, 1):
        for name in ('index-name', 'index-value', 'index-change'):
            index_lines.append(_text(_one(card, '.' + name, f'지수 {i}')))
    scenes = ['<1>' + _text(header) + '\n' + '\n\n'.join(_text(s) for s in summaries)
              + '\n\n' + '\n'.join(index_lines)]
    sections = root.find_all('section')
    if not sections:
        raise SerializationError('뉴스 section이 없습니다.')
    for scene_number, section in enumerate(sections, 2):
        for ancestor in section.parents:
            if ancestor is root:
                break
            if ancestor.name == 'section':
                raise SerializationError(f'장면 {scene_number}: 중첩된 section은 지원하지 않습니다.')
        lines = [f'<{scene_number}>' + _text(_one(section, 'h2', f'장면 {scene_number}'))]
        items = section.select('ul li')
        if not items:
            raise SerializationError(f'장면 {scene_number}: ul/li 항목이 없습니다.')
        for i, item in enumerate(items, 1):
            for name in ('key-info', 'key-detail'):
                lines.append(_text(_one(item, f'span.{name}', f'장면 {scene_number} 항목 {i}')))
        scenes.append('\n'.join(lines))
    return '\n===\n'.join(scenes)


def main():
    parser = argparse.ArgumentParser(description='HTML 뉴스 파일을 장면별 평문으로 직렬화합니다.')
    parser.add_argument('input', type=Path)
    parser.add_argument('-o', '--output', type=Path)
    parser.add_argument('--fenced', action='store_true')
    args = parser.parse_args()
    try:
        result = serialize_html(args.input.read_text(encoding='utf-8-sig'))
        if args.fenced:
            result = '```text\n' + result + '\n```'
        if args.output:
            args.output.write_text(result, encoding='utf-8', newline='\n')
        else:
            import sys
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stdout.write(result)
    except (OSError, UnicodeError, SerializationError) as exc:
        parser.exit(1, f'변환 실패: {exc}\n')


if __name__ == '__main__':
    main()
