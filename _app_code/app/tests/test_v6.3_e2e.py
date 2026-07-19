"""Test v6.3 Error/Warning distinction with test.zip"""
import urllib.request
import json
import time
import uuid

test_zip = r'E:\北航项目\test.zip'
with open(test_zip, 'rb') as f:
    zip_data = f.read()

boundary = '----' + uuid.uuid4().hex
body = (
    f'--{boundary}\r\n'
    f'Content-Disposition: form-data; name="files"; filename="test.zip"\r\n'
    f'Content-Type: application/zip\r\n'
    f'\r\n'
).encode() + zip_data + f'\r\n--{boundary}--\r\n'.encode()

req = urllib.request.Request(
    'http://localhost:8003/analyze',
    data=body,
    headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
    method='POST'
)

print('Uploading test.zip...')
resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
rid = resp.get('request_id', '')
print(f'request_id: {rid}')

for i in range(60):
    time.sleep(2)
    sr = urllib.request.Request(f'http://localhost:8003/status/{rid}')
    try:
        sd = json.loads(urllib.request.urlopen(sr, timeout=10).read())
        status = sd.get('status', '')
        if status == 'completed':
            bugs = sd.get('payload', {}).get('report', {}).get('summary', {}).get('bugs', [])
            by_level = {}
            for b in bugs:
                by_level[b['level']] = by_level.get(b['level'], 0) + 1
            print(f'\n=== Results ({len(bugs)} bugs) ===')
            for lvl, cnt in sorted(by_level.items()):
                print(f'  {lvl}: {cnt}')
            print()
            for b in bugs[:8]:
                print(f'  [{b["level"]:7s}] {b["rule_id"]:20s} force={b.get("force","?")}')
            if len(bugs) > 8:
                print(f'  ... and {len(bugs)-8} more')
            
            if 'Error' in by_level and 'Warning' in by_level:
                print(f'\nPASS: Both Error({by_level["Error"]}) and Warning({by_level["Warning"]}) found!')
            elif 'Error' in by_level:
                print(f'\nWARN: Only Error found, no Warning')
            else:
                print(f'\nFAIL: Only Warning found, no Error')
            break
        elif status == 'failed':
            print(f'Failed: {sd.get("error", "?")}')
            break
        else:
            print(f'  status: {status}')
    except Exception as e:
        print(f'  poll error: {e}')
        break
