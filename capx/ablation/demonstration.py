"""World-only expert video -> timestamped first-request images (no video API)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from .protocol import sha256, write_json


def frame_indices(count, fps, interval=.2, maximum=64):
    if count < 1 or not np.isfinite(fps) or fps <= 0 or maximum < 2:
        raise ValueError('Invalid video metadata')
    duration = (count - 1) / fps
    indices = np.unique(np.r_[np.rint(np.arange(0, duration, interval) * fps).astype(int), count-1])
    if len(indices) > maximum:
        indices = np.unique(np.rint(np.linspace(0, count-1, maximum)).astype(int))
    return indices.tolist()


def image_part(rgb, *, demonstration=False):
    stream = io.BytesIO()
    image = Image.fromarray(rgb)
    if demonstration:
        # 64 lossless frames exceeded the deployed gateway's request limit.
        # Keep original PNGs on disk; compress only the model input images.
        image = image.convert('RGB')
        image.thumbnail((768, 768), Image.Resampling.LANCZOS)
        quality = 80
        while True:
            stream.seek(0)
            stream.truncate()
            image.save(stream, format='JPEG', quality=quality)
            if stream.tell() <= 96 * 1024:
                break
            if quality > 40:
                quality -= 10
            else:
                image.thumbnail((max(1, image.width * 3 // 4),
                                 max(1, image.height * 3 // 4)), Image.Resampling.LANCZOS)
    else:
        image.save(stream, format='PNG')
    mime = 'image/jpeg' if demonstration else 'image/png'
    return {'type': 'image_url', 'image_url': {'url': 'data:' + mime + ';base64,' +
            base64.b64encode(stream.getvalue()).decode('ascii')}}


def expert_parts(demo, output):
    import cv2
    if sha256(demo['path']) != demo['sha256']:
        raise ValueError('Expert video changed after audit')
    cap = cv2.VideoCapture(demo['path'])
    try:
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        indices = frame_indices(count, fps)
        parts = [{'type': 'text', 'text': 'Expert demonstration from a DIFFERENT seed. '
                  'Imitate the procedure, not its object identity, answer or exact coordinates.'}]
        records = []
        folder = Path(output) / 'expert_frames'
        folder.mkdir(parents=True, exist_ok=True)
        for index in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, bgr = cap.read()
            if not ok:
                raise ValueError(f'Cannot decode expert frame {index}')
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            path = folder / f'{index:08d}.png'
            Image.fromarray(rgb).save(path)
            parts.extend([{'type': 'text', 'text': f'Demonstration t={index/fps:.3f}s'},
                          image_part(rgb, demonstration=True)])
            records.append({'frame': index, 'time_s': index/fps, 'path': str(path), 'sha256': sha256(path)})
        write_json(folder / 'index.json', {'video_sha256': demo['sha256'], 'frames': records,
                   'model_input_encoding': {'format': 'jpeg', 'max_quality': 80, 'max_edge': 768,
                                            'max_bytes_per_frame': 96 * 1024},
                   'stored_frame_encoding': 'png'})
        return parts
    finally:
        cap.release()
