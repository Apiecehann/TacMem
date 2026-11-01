"""Runs in the existing isolated TACTO Python. Emits native RGB-only IPC.

Adapted from our existing Shadow renderer, without depth/reference export or
force-estimator code. PhysX forces are renderer inputs, never policy outputs.
"""
import json
import sys
from pathlib import Path
from importlib import metadata, util


def main(root, config, width):
    import cv2
    import numpy as np
    import trimesh
    output = sys.stdout.buffer
    sys.stdout = sys.stderr
    distribution = metadata.distribution('tacto')
    spec = util.spec_from_file_location('digit_renderer', distribution.locate_file('tacto/renderer.py'))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    background = cv2.imread(str(root / 'assets/examples/conf/bg_digit_240_320.jpg'))
    if background is None:
        raise RuntimeError('Original DIGIT background missing')
    renderer = module.Renderer(width, width * 3 // 4, background, str(config))
    case = trimesh.load(root / 'assets/meshes/digit.STL')
    gel = renderer._generate_gel_trimesh()
    ready = dict(tacto_version=metadata.version('tacto'), config_path=str(config),
        gel_vertices=gel.vertices.tolist(), gel_faces=gel.faces.tolist(),
        case_vertices=case.vertices.tolist(), case_faces=case.faces.tolist(), case_size_m=case.extents.tolist())
    output.write(json.dumps(ready).encode() + b'\n')
    output.flush()
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if request['command'] == 'close':
                break
            if request['command'] == 'setup':
                for name, size in request['keys'].items():
                    renderer.add_object(trimesh.creation.box(extents=size), name)
                output.write(b'{"ready":true}\n')
            else:
                renderer.update_camera_pose(*request['sensor'])
                poses = {name: v[:2] for name, v in request['objects'].items()}
                forces = {name: v[2] for name, v in request['objects'].items() if v[2] > .01}
                colors, depths = renderer.render(object_poses=poses, normal_forces=forces)
                rgb = np.ascontiguousarray(colors[0], dtype=np.uint8)
                reference, depth = renderer.depth0[0], depths[0]
                valid = np.isfinite(depth) & np.isfinite(reference) & (depth > 0) & (reference > 0)
                indentation = float(np.maximum(0., reference[valid]-depth[valid]).max(initial=0.))
                output.write(json.dumps({'bytes': rgb.nbytes, 'indentation_m': indentation}).encode() + b'\n')
                output.write(rgb.tobytes())
            output.flush()
    finally:
        renderer.r.delete()


if __name__ == '__main__':
    main(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
