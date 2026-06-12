"""Quick API smoke test for /analyze endpoint."""
import urllib.request
import json
import os

# Create test C file
os.makedirs("workspaces/_test_upload", exist_ok=True)
code = b"""#include <stdio.h>
int main(void) {
    int x;
    printf("%%d", x);
    return 0;
}
"""
with open("workspaces/_test_upload/test_up.c", "wb") as f:
    f.write(code)

# Build multipart body
boundary = b"----BOUNDARY"
body = b""
body += b"--" + boundary + b"\r\n"
body += b'Content-Disposition: form-data; name="files"; filename="test.c"\r\n'
body += b"Content-Type: text/x-c\r\n\r\n"
body += code + b"\r\n"
body += b"--" + boundary + b"--\r\n"

# Send request
req = urllib.request.Request(
    "http://127.0.0.1:8000/analyze",
    data=body,
    headers={"Content-Type": "multipart/form-data; boundary=" + boundary.decode()},
)

try:
    resp = urllib.request.urlopen(req, timeout=120)
    data = json.loads(resp.read())
    summary = data.get("report", {}).get("summary", {})
    print(f"request_id: {data.get('request_id', '?')}")
    print(f"total_bugs: {summary.get('total_bugs', 0)}")
    print(f"by_level: {summary.get('by_level', {})}")
    print("Upload analysis: OK")
except Exception as e:
    print(f"Error: {e}")
