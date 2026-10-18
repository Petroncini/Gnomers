import os
import sys
import tempfile
from pathlib import Path

# precisa vir antes de qualquer import do app: o engine e o modo do MCP saem do ambiente
_tmp = tempfile.mkdtemp(prefix="gnomers-test-")
os.environ["GNOMERS_DB"] = f"sqlite:///{_tmp}/test.db"
os.environ["GNOMERS_MCP_MODE"] = "fake"
os.environ["GNOMERS_LLM"] = "off"
os.environ["GNOMERS_FIXTURES"] = f"{_tmp}/fixtures"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
