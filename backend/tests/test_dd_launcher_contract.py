"""Static UI/request wiring guards; not a substitute for browser interaction."""
from pathlib import Path

PAGE = (Path(__file__).resolve().parents[2] / 'frontend/src/pages/due-diligence/index.tsx').read_text(encoding='utf-8')
STREAM = (Path(__file__).resolve().parents[2] / 'frontend/src/pages/due-diligence/useDDStream.ts').read_text(encoding='utf-8')


def test_subject_and_multiline_question_are_separate():
    assert 'id="dd-subject"' in PAGE
    assert '<Input.TextArea' in PAGE and 'id="dd-query"' in PAGE
    assert 'subjectName: selectedProfile?.name || subjectName.trim()' in PAGE
    assert 'start(query.trim(),' in PAGE
    assert 'subject_name: options?.subjectName' in STREAM


def test_profile_does_not_overwrite_question_and_locks_subject():
    assert 'if (selectedProfile) setSubjectName(selectedProfile.name)' in PAGE
    assert 'if (selectedProfile) setQuery' not in PAGE
    assert 'disabled={running || Boolean(companyProfileId)}' in PAGE


def test_required_inputs_and_explicit_submission():
    assert 'query.trim() && subjectName.trim()' in PAGE
    assert 'disabled={!canLaunch}' in PAGE
    assert 'onPressEnter' not in PAGE
    assert 'asOf: asOf.trim() || undefined' in PAGE
