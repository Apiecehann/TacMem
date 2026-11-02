"""Encode the current scene for the single initial model request."""
import base64
import io

from PIL import Image


def image_part(rgb):
    stream = io.BytesIO()
    Image.fromarray(rgb).save(stream, format='PNG')
    return {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' +
            base64.b64encode(stream.getvalue()).decode('ascii')}}
