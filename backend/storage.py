import json
import re
import uuid
from pathlib import Path

from .config import MAX_INPUT_BYTES
from .narration import decode_response


JOB_ID = re.compile(r'\d{8}_\d{6}_\d{6}_[0-9a-f]{8}')


def source_path(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    intended_base = root.resolve() / 'src'
    base = intended_base.resolve()
    if base != intended_base:
        raise ValueError('src 폴더를 다른 경로로 연결할 수 없습니다.')
    path = (base / relative_path).resolve()
    if relative_path.is_absolute() or not path.is_relative_to(base) or path.suffix.lower() not in {'.txt', '.html'}:
        raise ValueError('src 폴더 안의 .txt 또는 .html 파일만 선택할 수 있습니다.')
    return path


def read_source(root: Path, relative: str) -> str:
    path = source_path(root, relative)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('입력 파일은 5 MiB 이하만 지원합니다.')
    with path.open('rb') as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('입력 파일은 5 MiB 이하만 지원합니다.')
    try:
        return data.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('파일을 UTF-8 인코딩으로 저장한 뒤 다시 선택하세요.') from None


def list_sources(root: Path) -> list[dict]:
    folder = root / 'src'
    files = []
    if folder.exists():
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix.lower() in {'.txt', '.html'}:
                relative = path.relative_to(folder).as_posix()
                try:
                    safe = source_path(root, relative)
                    files.append({'path': relative, 'size': safe.stat().st_size})
                except (ValueError, OSError):
                    continue
    return files


class ResultStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def directory(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise ValueError('잘못된 작업 ID입니다.')
        intended_base = self.root / 'outputs'
        intended_path = intended_base / job_id
        path = intended_path.resolve()
        if intended_base.resolve() != intended_base or path != intended_path:
            raise ValueError('결과 폴더를 다른 경로로 연결할 수 없습니다.')
        return path

    def write_text(self, job_id: str, name: str, text: str):
        if not re.fullmatch(r'(?:serialized|compressed|(?:invalid|raw)_response_[1-9]\d*)\.txt|metadata\.json', name):
            raise ValueError('허용되지 않은 결과 파일입니다.')
        folder = self.directory(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / name
        if not destination.resolve().is_relative_to(folder):
            raise ValueError('결과 파일 경로가 실행 폴더를 벗어났습니다.')
        temp = folder / f'.{name}.{uuid.uuid4().hex}.tmp'
        try:
            temp.write_text(text, encoding='utf-8', newline='\n')
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)

    def metadata(self, job: dict):
        metadata = {k: v for k, v in job.items() if k not in {'serialized', 'compressed'}}
        self.write_text(job['id'], 'metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))

    def load(self, job_id: str) -> dict:
        folder = self.directory(job_id)
        metadata_path = folder / 'metadata.json'
        if not metadata_path.resolve().is_relative_to(folder):
            raise ValueError('결과 경로를 확인하세요.')
        job = json.loads(metadata_path.read_text(encoding='utf-8'))
        job.setdefault('requested_model', job.get('model'))
        job.setdefault('attempted_models', [])
        for stage in ('serialized', 'compressed'):
            path = folder / (stage + '.txt')
            if not path.resolve().is_relative_to(folder):
                raise ValueError('결과 경로를 확인하세요.')
            job[stage] = path.read_text(encoding='utf-8') if path.exists() else None
        if job['state'] != 'completed' and not job['compressed']:
            # Expose the latest saved draft from older runs without converting files.
            drafts = [path for path in folder.glob('invalid_response_*.txt')
                      if re.fullmatch(r'invalid_response_[1-9]\d*\.txt', path.name)]
            for path in sorted(drafts, key=lambda path: int(path.stem.rsplit('_', 1)[1]), reverse=True):
                if not path.resolve().is_relative_to(folder):
                    raise ValueError('결과 경로를 확인하세요.')
                if path.exists():
                    job['compressed'] = decode_response(path.read_text(encoding='utf-8'))[0]
                    job['body_char_count'] = len(job['compressed'].replace('\r\n', '\n').replace('\n', ''))
                    break
        if job['state'] not in ('completed', 'failed'):
            job['state'] = 'failed'
            job['error'] = '서버 종료로 작업이 중단되었습니다. 보관된 직렬화 결과로 다시 시도하세요.'
        return job
