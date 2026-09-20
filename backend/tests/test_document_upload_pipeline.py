import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from service import docmind_service as module


@pytest.mark.parametrize('text', ['短文本', 'a' * 500, 'b' * 1200])
def test_chunking_terminates_and_covers_text(text):
    chunks = module.chunk_text(text)
    assert 1 <= len(chunks) <= 4
    assert chunks[-1].endswith(text[-20:])


@pytest.mark.parametrize('extension', ['.txt', '.md', '.TXT'])
def test_plain_text_bypasses_cloud_and_preserves_identity(tmp_path, monkeypatch, extension):
    path = tmp_path / ('document' + extension)
    path.write_text('现金流为负，需要进一步核查。', encoding='utf-8-sig')
    cloud = Mock(side_effect=AssertionError('plain text must not call DocMind'))
    storage = Mock()
    monkeypatch.setattr(module, 'DocMindService', cloud)
    monkeypatch.setattr(module, 'generate_embedding', lambda chunks: [[0.1] * 1024 for _ in chunks])
    monkeypatch.setattr(module, 'get_milvus_service', lambda: storage)
    result = module.process_document_with_docmind(str(path), path.name, 'kb_test', kb_id='kb1', document_id='doc1')
    assert result['success']
    row = storage.insert_documents.call_args.args[1][0]
    assert row['kb_id'] == 'kb1' and row['doc_id'] == 'doc1'
    assert row['content'] == '现金流为负，需要进一步核查。'
    cloud.assert_not_called()


def test_bad_encoding_fails_before_embedding(tmp_path, monkeypatch):
    path = tmp_path / 'bad.txt'
    path.write_bytes(b'\xff\xfe\x00')
    embedding = Mock()
    monkeypatch.setattr(module, 'generate_embedding', embedding)
    result = module.process_document_with_docmind(str(path), path.name, 'kb_test')
    assert not result['success'] and 'UTF-8' in result['message']
    embedding.assert_not_called()
