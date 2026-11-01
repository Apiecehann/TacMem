"""Restricted generated-program surface, without a simulator or filesystem handle.

An experiment contract guard, not a hostile-code OS security sandbox. No source
or stdout is interpreted as a sensor log. Host errors contain types, not values.
"""
from __future__ import annotations

import ast
import builtins
import math
import re
from types import SimpleNamespace

import numpy as np


class ProgramViolation(ValueError):
    pass


NUMPY = set('array asarray zeros ones full empty arange linspace concatenate stack hstack vstack '
            'mean median percentile quantile sum min max std var abs sqrt square diff gradient '
            'clip where nonzero count_nonzero isfinite isnan all any logical_and logical_or '
            'maximum minimum float32 float64 int32 int64 uint8 bool_ pi inf nan reshape '
            'transpose squeeze expand_dims dot cross argmin argmax sign sin cos tan arctan2 '
            'degrees radians round rint ceil floor histogram bincount unique'.split())
CV2 = set('cvtColor GaussianBlur medianBlur Sobel Scharr Laplacian Canny threshold adaptiveThreshold '
          'absdiff subtract add multiply divide resize dilate erode morphologyEx getStructuringElement '
          'findContours contourArea moments boundingRect connectedComponents connectedComponentsWithStats '
          'calcOpticalFlowFarneback goodFeaturesToTrack calcOpticalFlowPyrLK normalize mean meanStdDev '
          'countNonZero bitwise_and bitwise_or bitwise_not matchTemplate minMaxLoc'.split())
METHODS = set('copy astype reshape flatten ravel transpose squeeze mean sum min max std var all any '
              'tolist item append extend pop get keys values items update setdefault count index sort '
              'reverse split strip lower upper is_integer'.split())
FORBIDDEN = set('env APIS obs get_tactile_data get_tactile_features get_estimated_force '
                'get_key_force get_tactile_force get_tactile_depth get_marker get_marker_positions '
                'check_success is_contacting is_slipping is_grasp_stable detect_contact detect_slip '
                'sample_grasp_pose read_force check_force_stability'.split())


def extract_program(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ProgramViolation('empty_program')
    if '```' in raw:
        match = re.fullmatch(r'\s*```(?:python)?\s*\n([\s\S]*?)\n```\s*', raw)
        if not match or '```' in match.group(1):
            raise ProgramViolation('one_complete_program_required')
        raw = match.group(1)
    # Syntax errors are terminal; no repair call.
    ast.parse(raw)
    return raw


def validate_program(code, mode):
    tree = ast.parse(code)
    allowed_modules = {'math', 'numpy', 'cv2'}  # same scene-vision tools in both groups
    allowed_attrs = NUMPY | CV2 | METHODS | set(dir(math)) | {'shape', 'dtype', 'ndim', 'size', 'T'}
    forbidden = FORBIDDEN | ({'read_tactile_rgb'} if mode == 'no-tactile' else set())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(n.name not in allowed_modules for n in node.names):
                raise ProgramViolation('import_not_allowed')
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in allowed_modules or any(n.name == '*' for n in node.names):
                raise ProgramViolation('import_not_allowed')
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await)):
            raise ProgramViolation('unsupported_program_construct')
        elif isinstance(node, ast.Name) and (node.id.startswith('__') or node.id in forbidden):
            raise ProgramViolation('forbidden_interface')
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith('_') or not (node.attr in allowed_attrs or
                  (node.attr.isupper() and node.attr.startswith(('COLOR_', 'THRESH_', 'MORPH_', 'RETR_', 'CHAIN_', 'CV_', 'NORM_', 'INTER_', 'TM_')))):
                raise ProgramViolation('attribute_not_allowed')
    return tree


def run_program(code, api, mode):
    validate_program(code, mode)
    modules = {'math': math, 'numpy': SimpleNamespace(**{k: getattr(np, k) for k in NUMPY})}
    import cv2
    constants = {k for k in dir(cv2) if k.isupper() and k.startswith(
        ('COLOR_', 'THRESH_', 'MORPH_', 'RETR_', 'CHAIN_', 'CV_', 'NORM_', 'INTER_', 'TM_'))}
    modules['cv2'] = SimpleNamespace(**{k: getattr(cv2, k) for k in CV2 | constants})

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ProgramViolation('import_not_allowed')
        return modules[name]

    names = 'abs all any bool dict enumerate float int len list map max min range reversed round set sorted str sum tuple zip isinstance Exception ValueError RuntimeError TimeoutError'.split()
    safe = {key: getattr(builtins, key) for key in names}
    safe.update(__import__=safe_import, print=lambda *a, **kw: None)
    scope = {'__builtins__': safe, **api, 'capx_stop': False}
    exec(compile(code, '<single-program>', 'exec'), scope, scope)
    if scope.get('capx_stop') is not True:
        raise ProgramViolation('missing_legal_END')
    return True
