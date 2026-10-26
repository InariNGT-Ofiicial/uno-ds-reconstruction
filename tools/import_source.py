from pathlib import Path
import argparse,hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[1]
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--video',type=Path,required=True);p.add_argument('--audio',type=Path,required=True)
    args=p.parse_args();spec=json.loads((ROOT/'project/source-inputs.json').read_text(encoding='utf-8'))
    # Validate both before copying either input.
    for kind,file in [('video',args.video),('audio',args.audio)]:
        if sha(file)!=spec[kind]['sha256']:raise ValueError(kind+' does not match the frozen source; realign another encode instead of bypassing this check')
    for kind,file in [('video',args.video),('audio',args.audio)]:
        dest=ROOT/spec[kind]['path'];dest.parent.mkdir(exist_ok=True)
        if dest.resolve()!=file.resolve():shutil.copyfile(file,dest)
    print(json.dumps({'source_video':'verified','source_audio':'verified'}))
if __name__=='__main__':main()
