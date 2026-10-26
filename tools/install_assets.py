"""Install the declared frozen artwork bundle without accepting extra paths."""
from pathlib import Path,PurePosixPath
import argparse,hashlib,json,urllib.request,zipfile
ROOT=Path(__file__).resolve().parents[1]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser();group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--archive',type=Path);group.add_argument('--download',action='store_true')
    args=p.parse_args();release=json.loads((ROOT/'project/release-assets.json').read_text(encoding='utf-8'))
    archive=args.archive
    if args.download:
        cache=ROOT/'downloads';cache.mkdir(exist_ok=True);archive=cache/release['name']
        if not archive.exists() or sha(archive)!=release['sha256']:
            part=archive.with_suffix('.part')
            with urllib.request.urlopen(release['download_url'],timeout=60) as r,part.open('wb') as out:
                while block:=r.read(1024*1024):out.write(block)
            if sha(part)!=release['sha256']:raise ValueError('Downloaded archive hash mismatch')
            part.replace(archive)
    if sha(archive)!=release['sha256']:raise ValueError('Asset archive hash mismatch')
    manifest=json.loads((ROOT/'project/assets-manifest.json').read_text(encoding='utf-8'))
    expected={r['path']:r for r in manifest['files']};written=0
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(set(names))!=len(names) or set(names)!=set(expected):raise ValueError('Unexpected or missing archive member')
        for name in names:
            rel=PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='project':raise ValueError('Unsafe archive member')
            target=(ROOT/Path(*rel.parts)).resolve()
            if not target.is_relative_to((ROOT/'project').resolve()):raise ValueError('Escaping archive member')
            row=expected[name]
            if target.exists():
                if sha(target)!=row['sha256']:raise ValueError('Existing asset differs; move it aside deliberately: '+name)
                continue
            content=z.read(name)
            if hashlib.sha256(content).hexdigest()!=row['sha256']:raise ValueError('Asset content hash mismatch: '+name)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content);written+=1
    print(json.dumps({'verified_assets':len(expected),'new_files':written}))
if __name__=='__main__':main()
