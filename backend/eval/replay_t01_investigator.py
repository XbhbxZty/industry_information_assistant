"""Focused live adapter regression, not an HTTP/UI E2E run. No DB writes."""
import asyncio
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
load_dotenv(ROOT / '.env')


async def main():
    from config.llm_config import get_config
    from service.deep_research_v2.agents.scout import DeepScout
    from service.deep_research_v2.investigation_tools import InvestigationTools
    from service.deep_research_v2.state import create_initial_state
    baseline = json.loads((ROOT / 'eval/agent_e2e_pack/runs/T01-agent-001/result.json').read_text(encoding='utf-8'))
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
    expected = {p.name: p.read_text(encoding='utf-8-sig').strip()
                for p in (ROOT / 'eval/agent_e2e_pack/uploads/base').glob('*.txt')}
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
    # Same checklist as the existing baseline, without modifying its checkpoint.
    from core.database_url import resolve_database_urls
    import psycopg
    from urllib.parse import urlsplit, urlunsplit
    url = urlsplit(resolve_database_urls().psycopg_conninfo)
    if url.hostname not in ('localhost', '127.0.0.1') or url.query:
        raise RuntimeError('Only plain local evaluation DB connections allowed')
    target = urlunsplit(url._replace(path='/industry_assistant_codex'))
    with psycopg.connect(target) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        row = conn.execute('SELECT state_json FROM research_checkpoints WHERE session_id=%s',
                           (baseline['session_id'],)).fetchone()
        if not row:
            raise RuntimeError('Baseline checkpoint unavailable')
        state['field_checks'] = row[0]['field_checks']
    await InvestigationTools(scout, state).run()
    print('RESULT_JSON=' + json.dumps({
        'test_type': 'focused_live_investigator_not_e2e', 'model': baseline['models']['scout'],
        'agent_investigation': state['agent_investigation'], 'errors': state['errors'],
        'rag_evidence_summary': state.get('rag_evidence_summary'),
    }, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
