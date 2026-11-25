from pathlib import Path
import sqlite3,zipfile,tempfile
from datetime import datetime
root=Path(__file__).resolve().parent
out=root.parent/('radar-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
with tempfile.TemporaryDirectory() as tmp:
    db=root/'events.sqlite3';copy=Path(tmp)/'events.sqlite3'
    if db.exists():
        source=sqlite3.connect(str(db));target=sqlite3.connect(str(copy))
        source.backup(target);target.close();source.close()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for f in root.rglob('*'):
            if not f.is_file() or any(x in f.parts for x in ['.venv','__pycache__']):continue
            if f.name.startswith('events.sqlite3'):continue
            z.write(f,'radar_service/'+f.relative_to(root).as_posix())
        if copy.exists():z.write(copy,'radar_service/events.sqlite3')
print('Backup saved:',out)
