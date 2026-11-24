import asyncio, traceback
from app.core.db import tenant_session
from sqlalchemy import text

ORG = '01a20de1-10a6-78fe-8cea-eb03ab82e504'
SQL = (
    'INSERT INTO audit_log '
    '(id, org_id, actor_id, actor_type, action, resource_type, resource_id, '
    'outcome, reason, request_id, ip, meta) '
    'VALUES (:id, :o, :a, :at, :ac, :rt, :ri, :ou, :re, :rq, :ip, :m)'
)
PARAMS = {
    'id': 'diag-id-001', 'o': ORG, 'a': 'u1', 'at': 'user',
    'ac': 'read', 'rt': 'agent', 'ri': None,
    'ou': 'allowed', 're': 'diag', 'rq': None, 'ip': None, 'm': '{}',
}

async def main():
    try:
        async with tenant_session(ORG) as s:
            await s.execute(text(SQL), PARAMS)
        print('INSERT OK')
    except Exception:
        traceback.print_exc()

asyncio.run(main())
