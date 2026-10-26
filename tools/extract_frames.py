"""Extract explicitly requested source keys with sequential PTS/index mapping."""
from pathlib import Path
import argparse,json
import av
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--video',type=Path,default=ROOT/'inputs/source-video.mp4')
    p.add_argument('--frames',required=True,help='Comma-separated zero-based indices chosen after semantic review')
    p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    wanted={int(v) for v in args.frames.split(',')};assert wanted and min(wanted)>=0
    args.out.mkdir(parents=True,exist_ok=True);records=[]
    with av.open(str(args.video)) as c:
        for i,f in enumerate(c.decode(video=0)):
            if i in wanted:
                file=args.out/f'f{i:05d}.png'
                if file.exists():raise FileExistsError(file)
                f.to_image().save(file);records.append({'frame':i,'pts':f.pts,'time_base':str(f.time_base),'seconds':float(f.time),'file':file.name})
            if i>=max(wanted):break
    if len(records)!=len(wanted):raise ValueError('Requested frame is outside decoded source')
    (args.out/'frames.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'extracted':len(records)}))
if __name__=='__main__':main()
