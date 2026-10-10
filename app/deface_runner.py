"""Run deface in this interpreter, loading its ONNX model through Unicode-safe IO."""
from pathlib import Path


def main():
    import sys
    import numpy as np
    from deface import centerface
    from deface.deface import main as deface_main

    # OpenCV's Windows filename overload cannot open some Unicode paths.
    # Use the same model bytes and detector; never download or substitute a model.
    centerface.default_onnx_path = np.frombuffer(
        Path(centerface.default_onnx_path).read_bytes(), dtype=np.uint8)
    sys.argv += ['--backend', 'opencv']
    deface_main()


if __name__ == '__main__':
    main()
