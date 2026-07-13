import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database
from collections import Counter
conn = database.get_conn()
rows = conn.execute("SELECT display_name, google_advertiser_name FROM brands WHERE COALESCE(is_active,1)=1").fetchall()
withname = [r for r in rows if (r[1] or '').strip()]
print("active brands:", len(rows), " with google_advertiser_name:", len(withname))
c = Counter((r[1] or '').strip() for r in withname)
shared = {k: v for k, v in c.items() if v > 1}
print("shared legal entities:", len(shared))
for k, v in shared.items():
    print("  ", k, v)
conn.close()
