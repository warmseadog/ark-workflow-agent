"""Explicit local/server release commands. Never starts SSH implicitly."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from app.release_operations import main

if __name__=='__main__':
    raise SystemExit(main())
