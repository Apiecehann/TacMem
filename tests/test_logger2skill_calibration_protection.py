from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from capx.logger2skill import curators, storage
from capx.logger2skill.initial_library import (
    CalibrationProtectionError, INITIAL_METADATA, build_initial, check_source_identity,
    common_view, validate_calibration_bindings,
)

SOURCE = Path(__file__).resolve().parents[1] / "capx/tactile_skills/initial_libraries/original-21-3758efb"


@pytest.fixture
def source():
    if not SOURCE.exists():
        pytest.skip('Frozen upstream source not installed')
    return SOURCE


@pytest.mark.parametrize('mode', ['capx', 'skillopt_markdown'])
@pytest.mark.parametrize(('old', 'new'), [
    ('on the shear x\n   component.', 'using the 2D norm.'),
    ('around 0.2 px.', 'around 0.5 px.'),
    ('n=12 seeds', 'n=13 seeds'),
    ('within ~0.4 px', 'within ~1.4 px'),
    ('split 0.06.', 'split 0.12.'),
])
def test_frozen_formula_threshold_units_conditions_and_evidence(source, tmp_path, mode, old, new):
    root = tmp_path / 'library'
    build_initial(source, root, mode, ('grasp_classify',))
    validate_calibration_bindings(root)
    name = 'texture-slope-two-grips' if old == 'split 0.06.' else 'gel-shear-weight'
    path = root / ('skill.md' if mode == 'skillopt_markdown' else f'skills/sense/{name}.md')
    text = path.read_text(encoding='utf-8')
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    with pytest.raises(CalibrationProtectionError, match=name):
        validate_calibration_bindings(root)


def test_common_roughness_delivery_keeps_original_two_grip_calibration(source, tmp_path):
    views = []
    for mode in ('capx', 'skillopt_markdown'):
        root = tmp_path / mode
        build_initial(source, root, mode, ('grasp_classify',))
        views.append(common_view(root, 'grasp_classify', 12000))
        metadata = storage.read_json(root / INITIAL_METADATA)
        assert len(metadata['calibration_bindings']['entries']) == 13
    assert views[0] == views[1]
    assert all(number in views[0] for number in ('27.5', '20.5', '0.10', '0.03', '0.06'))


def test_rejected_calibration_update_does_not_replace_active(source, tmp_path):
    kwargs = {'memory_format': storage.MARKDOWN_FORMAT} if hasattr(storage, 'MARKDOWN_FORMAT') else {}
    store = storage.LibraryStore(tmp_path / 'store', **kwargs)
    before = store.initialize(source, ('grasp_classify',))
    candidate = tmp_path / 'candidate'
    shutil.copytree(store.path(before), candidate)
    path = candidate / ('skill.md' if kwargs else 'skills/sense/gel-shear-weight.md')
    path.write_text(path.read_text(encoding='utf-8').replace('around 0.2 px.', 'around 0.5 px.'), encoding='utf-8')
    with pytest.raises(CalibrationProtectionError):
        store.adopt(candidate, before, 'bad_calibration')
    assert store.active() == before
    shutil.rmtree(candidate)
    shutil.copytree(store.path(before), candidate)
    action = candidate / ('skill.md' if kwargs else 'skills/act/strap-grasp-and-ramp.md')
    text = action.read_text(encoding='utf-8')
    action.write_text(text + '\n\n## Action evidence\n\nCPU fixture, unverified.\n', encoding='utf-8')
    after = store.adopt(candidate, before, 'action_update')
    assert after != before
    validate_calibration_bindings(store.path(after))
    metadata = candidate / INITIAL_METADATA
    metadata.unlink()
    with pytest.raises(CalibrationProtectionError, match='frozen initial'):
        store.adopt(candidate, after, 'removed_bindings')
    assert store.active() == after


def test_legacy_initial_library_is_not_silently_relabelled_protected(source, tmp_path):
    root = tmp_path / 'legacy'
    build_initial(source, root, 'capx', ('grasp_classify',))
    path = root / INITIAL_METADATA
    meta = storage.read_json(path)
    del meta['calibration_bindings']
    storage.write_json(path, meta)
    before = storage.digest(root)
    with pytest.raises(CalibrationProtectionError, match='new experiment'):
        check_source_identity(root, source)
    assert storage.digest(root) == before


def test_markdown_cannot_delete_or_duplicate_protected_entry(source, tmp_path):
    root = tmp_path / 'library'
    build_initial(source, root, 'skillopt_markdown', ('grasp_classify',))
    path = root / 'skill.md'
    text = path.read_text(encoding='utf-8')
    entry = (source / 'skills/sense/gel-shear-weight.md').read_text(encoding='utf-8')
    for changed in (text.replace(entry, ''), text + '\n\n' + entry):
        path.write_text(changed, encoding='utf-8')
        with pytest.raises(CalibrationProtectionError, match='gel-shear-weight'):
            validate_calibration_bindings(root)


@pytest.mark.parametrize('mode', ['capx', 'skillopt_markdown'])
def test_new_name_cannot_add_a_perception_calibration(source, tmp_path, mode):
    root = tmp_path / 'library'
    build_initial(source, root, mode, ('grasp_classify',))
    entry = (source / 'skills/sense/gel-shear-weight.md').read_text(encoding='utf-8')
    entry = entry.replace('id: S2\n', 'id: S999\n').replace('name: gel-shear-weight\n', 'name: alternate-weight-rule\n')
    entry = entry.replace('around 0.2 px.', 'around 0.5 px.')
    if mode == 'capx':
        (root / 'skills/sense/alternate-weight-rule.md').write_text(entry, encoding='utf-8')
    else:
        path = root / 'skill.md'
        path.write_text(path.read_text(encoding='utf-8') + '\n\n' + entry, encoding='utf-8')
    with pytest.raises(CalibrationProtectionError, match='new perception.*alternate-weight-rule'):
        validate_calibration_bindings(root)


def test_cc_calibration_violation_is_not_a_format_retry(source, tmp_path):
    if not hasattr(curators, 'finish_cc_proposal'):
        pytest.skip('CC local editing exists only in the CC branch')
    candidate, inputs, edited = tmp_path / 'candidate', tmp_path / 'inputs', tmp_path / 'edited'
    build_initial(source, candidate, 'capx', ('grasp_classify',))
    shutil.copytree(candidate, inputs / 'library')
    shutil.copytree(candidate, edited)
    path = edited / 'skills/sense/gel-shear-weight.md'
    path.write_text(path.read_text(encoding='utf-8').replace('around 0.2 px.', 'around 0.5 px.'), encoding='utf-8')
    session = SimpleNamespace(work=tmp_path, ask=lambda *a, **k: pytest.fail('Must not request format repair'))
    before = storage.digest(candidate)
    with pytest.raises(CalibrationProtectionError):
        curators.finish_cc_proposal(session, '', candidate, inputs, {}, edited)
    assert storage.digest(candidate) == before


def test_skillopt_native_patch_rejects_calibration_change_atomically(source, tmp_path):
    if not hasattr(storage, 'MARKDOWN_FORMAT'):
        pytest.skip('Native Markdown SkillOpt exists only in the SkillOpt branch')
    root = tmp_path / 'library'
    build_initial(source, root, 'skillopt_markdown', ('grasp_classify',))
    before = storage.digest(root)
    response = json.dumps({'patch': {'edits': [
        {'op': 'replace', 'target': 'around 0.2 px.', 'content': 'around 0.5 px.'}
    ]}})
    with pytest.raises(CalibrationProtectionError):
        curators.apply_skillopt_response(response, root, Path('/mnt/data/ljz/opentacbench/references/SkillOpt'))
    assert storage.digest(root) == before
