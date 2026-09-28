"""Focused live Investigator regression, not HTTP/UI or report-quality E2E.

Uses the original synthetic T01 corpus and Scout model. No database writes,
Writer/Critic calls, review approvals, or model/budget overrides are performed.
Optional --output-dir must name a new directory; old archives are never reused.
"""
import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
load_dotenv(ROOT / '.env')


def _checkout_metadata():
    revision = subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT.parent, capture_output=True,
        text=True, encoding='utf-8', timeout=10, check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ['git', 'status', '--porcelain'], cwd=ROOT.parent, capture_output=True,
        text=True, encoding='utf-8', timeout=10, check=True,
    ).stdout.strip()
    return {'git_revision': revision, 'checkout_dirty': bool(dirty)}


def _original_checkpoint(baseline):
    """One local DB target for both checkpoint lookup and production catalog.

    Rewriting only the psycopg URL is unsafe: material_catalog uses the separate
    core.database SessionLocal, which could otherwise enumerate another DB.
    Validate both before constructing Scout or making a model/embedding call.
    """
    from core.database_url import resolve_database_urls
    import psycopg

    urls = resolve_database_urls()
    url = urlsplit(urls.psycopg_conninfo)
    if (url.hostname not in ('localhost', '127.0.0.1') or url.query
            or url.path != '/industry_assistant_codex'):
        raise RuntimeError('Both checkpoint and catalog must use the configured local industry_assistant_codex database')

    from core.database import DATABASE_CONNECTION_URLS, SessionLocal, engine
    if (DATABASE_CONNECTION_URLS.psycopg_conninfo != urls.psycopg_conninfo
            or engine.url.database != 'industry_assistant_codex'
            or engine.url.host != url.hostname
            or (engine.url.port or 5432) != (url.port or 5432)
            or SessionLocal.kw.get('bind') is not engine):
        raise RuntimeError('Production catalog session does not match the verified local evaluation database')

    with psycopg.connect(urls.psycopg_conninfo, connect_timeout=5) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row = conn.execute('SELECT user_id,state_json FROM research_checkpoints WHERE session_id=%s',
                           (baseline['session_id'],)).fetchone()
        if not row or not row[0]:
            raise RuntimeError('Baseline checkpoint or its owner is unavailable')
        owner, original_state = row
        kb_owner = conn.execute('SELECT user_id FROM knowledge_bases WHERE id=%s',
                                (baseline['kb_id'],)).fetchone()
        if not kb_owner or kb_owner[0] != owner:
            raise RuntimeError('Baseline checkpoint owner does not own the selected synthetic knowledge base')
        original_scope = {str(entry.get('kb_id')) for entry in original_state.get('kb_scope', [])}
        if original_scope != {baseline['kb_id']}:
            raise RuntimeError('Baseline checkpoint scope differs from the approved synthetic knowledge base')
        documents = conn.execute('SELECT filename,user_id FROM documents WHERE knowledge_base_id=%s',
                                 (baseline['kb_id'],)).fetchall()
        if (len(documents) != 5 or any(doc_owner != owner for _, doc_owner in documents)
                or {filename for filename, _ in documents} != set(baseline['uploaded_files'])):
            raise RuntimeError('Catalog documents differ from the five owner-bound baseline inputs')
        return str(owner), original_state['field_checks']


async def main(output_dir=None):
    if output_dir is not None:
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=False)
    started_at = datetime.now(timezone.utc).isoformat()
    started = monotonic()
    checkout = _checkout_metadata()
    baseline = json.loads((ROOT / 'eval/agent_e2e_pack/runs/T01-agent-001/result.json').read_text(encoding='utf-8'))
    owner, field_checks = _original_checkpoint(baseline)

    source_dir = (ROOT / 'eval/agent_e2e_pack/uploads/base').resolve()
    expected, manifest = {}, []
    for path in sorted(source_dir.glob('*.txt')):
        if path.resolve().parent != source_dir:
            raise RuntimeError('Synthetic fixture resolves outside the approved input directory')
        body = path.read_text(encoding='utf-8-sig').strip()
        expected[path.name] = body
        manifest.append({
            'filename': path.name,
            'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'normalized_body_utf8_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
        })
    if len(expected) != 5 or set(expected) != set(baseline['uploaded_files']):
        raise RuntimeError('Only the original five synthetic T01 files may enter this experiment')

    from config.llm_config import get_config
    from service.deep_research_v2.agents.scout import DeepScout
    from service.deep_research_v2.investigation_tools import InvestigationTools
    from service.deep_research_v2.state import create_initial_state
    config = get_config()
    # Pin the same Scout model used in the recorded baseline.
    scout = DeepScout(config.api_key, config.base_url, '', model=baseline['models']['scout'])
    kb = baseline['kb_id']
    # Fail closed before any model/embedding request if this shared local
    # collection has changed since the synthetic fixture upload.
    from pymilvus import Collection
    collection = Collection('kb_' + kb.replace('-', ''))
    collection.load()
    rows = collection.query(expr='', output_fields=['filename', 'content', 'kb_id'], limit=10000)
    if (len(rows) != len(expected) or len({r['filename'] for r in rows}) != len(expected)
            or any(r['kb_id'] != kb or r['filename'] not in expected
                   or r['content'].strip() != expected[r['filename']] for r in rows)):
        raise RuntimeError('Corpus differs from the five approved synthetic fixtures; no model call made')
    print('SYNTHETIC_CORPUS_VERIFIED: 5 files', flush=True)
    state = create_initial_state(baseline['query'], 't01-focused-regression', search_web=False,
                                search_local=True, as_of=baseline['as_of'],
                                subject_name=baseline['subject_name'], due_diligence=True,
                                research_strategy='agent', kb_scope=[{
                                    'kb_id': kb, 'kb_name': 'T01 baseline corpus',
                                    'collection': 'kb_' + kb.replace('-', '')}])
    # Use the real owner for the same read-only catalog boundary as production.
    # No token is minted, no checkpoint is resumed or rewritten, and no owner ID
    # is model supplied. The original checklist remains otherwise unchanged.
    state['_user_id'] = owner
    state['field_checks'] = field_checks
    await InvestigationTools(scout, state).run()
    result = {
        'test_type': 'focused_live_investigator_not_e2e', 'model': baseline['models']['scout'],
        **checkout,
        'query': baseline['query'], 'as_of': baseline['as_of'], 'subject_name': baseline['subject_name'],
        'baseline_session_id': baseline['session_id'], 'kb_id': kb,
        'database': 'industry_assistant_codex', 'catalog_owner_verified': True,
        'source_manifest': manifest,
        'body_normalization': 'utf-8-sig decode, universal-newline conversion, strip; matches corpus comparison',
        'production_budget': {'max_steps': 24, 'max_seconds': 420, 'overridden': False},
        'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': round(monotonic() - started, 3),
        'full_pipeline_run': False, 'http_ui_exercised': False, 'writer_critic_exercised': False,
        'quality_verdict': 'not_scored', 'database_writes': False, 'approvals_submitted': False,
        'limitations': [
            'An Investigator-only run; no upload, Graph, Writer, Critic, report or approval acceptance.',
            'Exit code zero only means the harness finished; inspect actual investigation status and action receipts.',
            'Error recovery is not exercised unless an actual tool failure and subsequent repair occur.',
            'The historical T01-focused-002 used 12 steps/240 seconds; it is not an equal-budget comparison.',
        ],
        'agent_investigation': state['agent_investigation'], 'errors': state['errors'],
        'rag_evidence_summary': state.get('rag_evidence_summary'),
    }
    if output_dir is not None:
        (output_dir / 'result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
        )
    print('RESULT_JSON=' + json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=None,
                        help='Write result.json in a new directory; refuse an existing archive')
    args = parser.parse_args()
    if args.output_dir is not None and args.output_dir.exists():
        parser.error('--output-dir must not already exist')
    asyncio.run(main(args.output_dir))
