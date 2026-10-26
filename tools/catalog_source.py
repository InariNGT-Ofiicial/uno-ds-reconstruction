from pathlib import Path
import sys, json, hashlib, math
ROOT=Path(__file__).resolve().parents[1]
import av
import numpy as np
from PIL import Image,ImageDraw,ImageFont

SOURCE=ROOT/'inputs/source-video.mp4'
OUT=ROOT/'project/source'
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'samples-2s').mkdir(exist_ok=True)
(OUT/'samples-quarter').mkdir(exist_ok=True)
try:
    font=ImageFont.truetype('DejaVuSans.ttf',16)
except OSError:
    font=ImageFont.load_default()

def sheet(items,path,cols=5,w=255,h=144):
    rows=math.ceil(len(items)/cols)
    im=Image.new('RGB',(cols*w,rows*(h+24)),(235,235,235))
    d=ImageDraw.Draw(im)
    for j,(idx,t,p) in enumerate(items):
        x,y=(j%cols)*w,(j//cols)*(h+24)
        q=Image.open(p).convert('RGB');q.thumbnail((w,h))
        im.paste(q,(x,y));d.text((x+2,y+h+2),f'{idx:04d}  {t:7.3f}s',font=font,fill=(0,0,0))
    im.save(path,quality=90)

def main():
    c=av.open(str(SOURCE));s=c.streams.video[0]
    metadata={'source':SOURCE.relative_to(ROOT).as_posix(),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'width':s.width,'height':s.height,'average_rate':str(s.average_rate),'stream_time_base':str(s.time_base),'stream_duration':s.duration,'container_duration_us':c.duration,'note':'Exact decoded hash and low-resolution metrics are analysis candidates, not semantic pose equivalence.'}
    rows=[];small=[];prev=None;sample2=[];quarter=[];next2=0.;nextq=0.
    for idx,f in enumerate(c.decode(s)):
        im=f.to_image().convert('RGB');arr=np.asarray(im)
        thumb=np.asarray(im.resize((142,80),Image.Resampling.BOX)).astype(np.int16)
        gray=np.asarray(im.convert('L').resize((32,18),Image.Resampling.BOX))
        ah=int(''.join('1' if v>gray.mean() else '0' for v in gray.ravel()),2)
        t=float(f.pts*f.time_base)
        mad=None if prev is None else float(np.abs(thumb-prev).mean())
        changed=None if prev is None else float((np.abs(thumb-prev).max(axis=2)>15).mean())
        rows.append({'frame':idx,'pts':f.pts,'time_base':str(f.time_base),'time':t,'sha256':hashlib.sha256(arr.tobytes()).hexdigest(),'small_mad_previous':mad,'changed_fraction_gt15':changed,'average_hash576':f'{ah:0144x}'})
        small.append(thumb.astype(np.uint8));prev=thumb
        if t+0.002>=next2:
            p=OUT/'samples-2s'/f'f{idx:05d}.jpg';im.save(p,quality=93);sample2.append((idx,t,p));next2+=2
        if t+0.002>=nextq:
            p=OUT/'samples-quarter'/f'f{idx:05d}.jpg';im.save(p,quality=91);quarter.append((idx,t,p));nextq+=.25
    metadata.update(decoded_frames=len(rows),first_pts=rows[0]['pts'],last_pts=rows[-1]['pts'],last_frame_time=rows[-1]['time'],nominal_duration=len(rows)/float(s.average_rate))
    (OUT/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf8')
    (OUT/'frame-analysis.json').write_text(json.dumps(rows,indent=2),encoding='utf8')
    np.savez_compressed(OUT/'source-small-rgb.npz',frames=np.stack(small))
    for pageno,start in enumerate(range(0,len(sample2),30)):
        sheet(sample2[start:start+30],OUT/f'storyboard-2s-{pageno+1:02d}.jpg')
    for pageno,start in enumerate(range(0,len(quarter),60)):
        sheet(quarter[start:start+60],OUT/f'storyboard-quarter-{pageno+1:02d}.jpg',cols=6,w=213,h=120)
    # Exact decoded pixel runs; compression can make source holds imperfect.
    runs=[];a=0
    for i in range(1,len(rows)+1):
        if i==len(rows) or rows[i]['sha256']!=rows[a]['sha256']:
            runs.append({'start':a,'end_exclusive':i,'length':i-a,'start_time':rows[a]['time'],'sha256':rows[a]['sha256']});a=i
    (OUT/'exact-pixel-runs.json').write_text(json.dumps(runs,indent=2),encoding='utf8')
    print(json.dumps(metadata,indent=2))
    print('2s sheets:',len(list(OUT.glob('storyboard-2s-*.jpg'))),'quarter sheets:',len(list(OUT.glob('storyboard-quarter-*.jpg'))),'exact runs:',len(runs))

if __name__=='__main__':main()
