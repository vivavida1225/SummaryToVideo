import pytest

from backend.config import Settings
from backend.storage import ResultStore, read_source
from backend.clipboard import decode_cf_html, choose_clipboard_content


def test_key_loading_skips_empty_and_sorts_numbers_without_exposing_values(tmp_path):
    (tmp_path / '.env').write_text('GEMINI_API_KEY_3=three\nGEMINI_API_KEY_1=one\nGEMINI_API_KEY_2=\n', encoding='utf-8')
    settings = Settings(tmp_path)
    assert settings.keys() == [(1, 'one'), (3, 'three')]
    assert 'one' not in repr(settings)


def test_input_path_traversal_rejected_and_utf8_bom_supported(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/한글.txt').write_text('HTML', encoding='utf-8-sig')
    assert read_source(tmp_path, '한글.txt') == 'HTML'
    with pytest.raises(ValueError):
        read_source(tmp_path, '../.env')
    with pytest.raises(ValueError):
        read_source(tmp_path, str(tmp_path / '.env'))


def test_non_utf8_rejected_without_replacement_characters(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/bad.txt').write_bytes(b'\xff\xfe\x00')
    with pytest.raises(ValueError, match='UTF-8'):
        read_source(tmp_path, 'bad.txt')


def test_cf_html_byte_offsets_with_korean():
    html = '<div class="embedded-content">한글</div>'.encode('utf-8')
    header = 'Version:1.0\r\nStartHTML:0000000000\r\nEndHTML:0000000000\r\n'
    start = len(header.encode())
    header = f'Version:1.0\r\nStartHTML:{start:010d}\r\nEndHTML:{start + len(html):010d}\r\n'
    assert decode_cf_html(header.encode() + html) == html.decode('utf-8')


def test_plain_html_priority_over_formatted_code_copy():
    raw = '<div class="embedded-content"><header>시장</header></div>'
    assert choose_clipboard_content(raw, '<pre>escaped code</pre>') == {'text': raw, 'format': 'text'}
    assert choose_clipboard_content('시장', raw) == {'text': raw, 'format': 'html'}


def test_output_run_junction_cannot_redirect_to_another_run(tmp_path):
    import subprocess
    import sys
    job_id = '20260911_123000_000000_1234abcd'
    other = tmp_path / 'other-run'
    other.mkdir()
    (tmp_path / 'outputs').mkdir()
    link = tmp_path / 'outputs' / job_id
    if sys.platform == 'win32':
        # PowerShell creates a directory junction without administrator privileges.
        script = 'New-Item -ItemType Junction -Path $args[0] -Target $args[1] | Out-Null'
        subprocess.run(['powershell', '-NoProfile', '-Command', '& { ' + script + ' }', str(link), str(other)], check=True, capture_output=True)
    else:
        link.symlink_to(other, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            ResultStore(tmp_path).write_text(job_id, 'serialized.txt', 'must not write')
        assert not (other / 'serialized.txt').exists()
    finally:
        # Remove this known test junction itself, never its target tree.
        if sys.platform == 'win32':
            link.rmdir()
        else:
            link.unlink()


def test_legacy_archived_result_loads_without_narration_revalidation(tmp_path):
    store = ResultStore(tmp_path)
    job_id = '20260911_123000_000000_1234abcd'
    legacy = '<1>기존 장면\n설명\n===\n<2>기존 다음 장면'
    store.metadata({'id': job_id, 'state': 'completed', 'body_char_count': 25})
    store.write_text(job_id, 'serialized.txt', '기존 입력')
    store.write_text(job_id, 'compressed.txt', legacy)
    job = store.load(job_id)
    assert job['compressed'] == legacy
    assert job['body_char_count'] == 25


def test_multi_digit_attempt_storage_and_legacy_model_recovery(tmp_path):
    store = ResultStore(tmp_path)
    job_id = '20260911_123000_000000_1234abcd'
    store.metadata({'id': job_id, 'state': 'failed', 'model': 'gemini-3.7-flash', 'error': 'API failed'})
    store.write_text(job_id, 'invalid_response_9.txt', 'older')
    store.write_text(job_id, 'invalid_response_10.txt', 'latest')
    store.write_text(job_id, 'raw_response_10.txt', 'latest')
    job = store.load(job_id)
    assert job['compressed'] == 'latest'
    assert job['requested_model'] == 'gemini-3.7-flash'
    for name in ['raw_response_0.txt', 'raw_response_-1.txt', '../raw_response_10.txt']:
        with pytest.raises(ValueError):
            store.write_text(job_id, name, 'invalid')
