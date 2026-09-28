from __future__ import annotations
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]; suite=root.parent.parent; deps=suite/"dependencies"; model=deps/"models"/"models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"/"snapshots"/"0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
paths={"ffmpeg":deps/"ffmpeg"/"bin"/"ffmpeg.exe","ffprobe":deps/"ffmpeg"/"bin"/"ffprobe.exe","python":deps/"python"/"python.exe","python_modules":deps/"python"/"Lib"/"site-packages","model_root":deps/"models","model_bin":model/"model.bin"}
result={key:{"path":str(path),"exists":path.is_file() if path.suffix else path.is_dir()} for key,path in paths.items()}
result["decision"]="pass" if all(item["exists"] for item in result.values() if isinstance(item,dict)) else "reject"
print(json.dumps(result,ensure_ascii=False,indent=2)); raise SystemExit(0 if result["decision"]=="pass" else 2)
