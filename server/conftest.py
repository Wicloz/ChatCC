import pathlib
import sys

# Make the server modules and shared test utils importable regardless of CWD.
HERE = pathlib.Path(__file__).parent
for p in (HERE, HERE / "tests"):
    sys.path.insert(0, str(p))
