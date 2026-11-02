"""Return OCR text lines with pixel boxes. Runs under the project OCR venv."""
import json
import os
import sys

os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')

from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
from rapidocr_onnxruntime import RapidOCR  # noqa: E402


def main():
    path = sys.argv[1]
    minimum = float(sys.argv[2]) if len(sys.argv) > 2 else 0.4
    with Image.open(path) as image:
        image = image.convert('RGB')
        if image.width * image.height > 24000000:
            raise SystemExit('pixel limit')
        pixels = np.asarray(image)[:, :, ::-1].copy()
    rows, _ = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)(pixels)
    lines = []
    for box, text, score in rows or []:
        if float(score) < minimum or not str(text).strip():
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        lines.append({'text': str(text), 'confidence': round(float(score), 3),
                      'x1': int(min(xs)), 'y1': int(min(ys)),
                      'x2': int(max(xs)), 'y2': int(max(ys))})
    print(json.dumps({'lines': lines}, ensure_ascii=False))


if __name__ == '__main__':
    main()
