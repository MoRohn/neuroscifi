"""Fetch exact public study inputs into a new directory, verifying each Git blob identity."""
import hashlib,json,sys,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[1]
dest=Path(sys.argv[1]) if len(sys.argv)>1 else root/'inputs'
dest.mkdir(exist_ok=True,parents=True)
def git_hash(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
for entry in json.loads((root/'inputs.json').read_text()):
    target=dest/entry['filename']
    if target.exists() and git_hash(target.read_bytes())==entry['git_blob_sha1']:
        continue
    url='https://raw.githubusercontent.com/{repository}/{commit}/{path}'.format(**entry)
    with urllib.request.urlopen(url,timeout=90) as response: data=response.read()
    if git_hash(data)!=entry['git_blob_sha1']:
        raise ValueError('Input hash mismatch: '+entry['filename'])
    tmp=target.with_suffix(target.suffix+'.tmp');tmp.write_bytes(data);tmp.replace(target)
    print('Verified',target.name)
