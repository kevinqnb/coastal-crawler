from coastal_crawler.db.engine import get_session
from coastal_crawler.db import store
from sqlalchemy import func, select
from coastal_crawler.db.models import Extraction

with get_session() as session:
    counts = store.count_by_status(session)
    total = sum(counts.values())
    filtered = total - counts.get('discovered', 0) - counts.get('filtering', 0)
    relevant = sum(counts.get(s, 0) for s in ('relevant', 'processing', 'extracted', 'failed'))
    irrelevant_or_other = counts.get('irrelevant', 0) + counts.get('inaccessible', 0)
    extracted = counts.get('extracted', 0)
    failed = counts.get('failed', 0)
    data_points = session.execute(select(func.count()).select_from(Extraction)).scalar_one()

print(f'total={total} filtered={filtered} relevant={relevant} irrelevant_or_other={irrelevant_or_other} extracted={extracted} failed_not_retried={failed} data_points={data_points}')