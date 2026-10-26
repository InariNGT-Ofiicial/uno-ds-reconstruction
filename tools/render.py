from __future__ import annotations
from pathlib import Path
from functools import lru_cache
import argparse
import os
import shutil
import bisect
import hashlib
import json
import math
import subprocess
import sys
import time

import numpy as np
from PIL import Image,ImageDraw,ImageFilter
from scipy.ndimage import label,find_objects,binary_fill_holes,binary_dilation,binary_erosion
from scipy.spatial import ConvexHull

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'project'
import av

VIDEO=ROOT/'inputs/source-video.mp4'
AUDIO=ROOT/'inputs/source-audio.m4a'
FFMPEG=Path(os.environ.get('FFMPEG_BINARY') or shutil.which('ffmpeg') or 'ffmpeg')
SIZE=(852,480)
HASHES={}

def read(path):return json.loads(path.read_text(encoding='utf-8'))
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def local_path(value):
    p=Path(value)
    if not p.is_absolute():p=PROJECT/p
    p=p.resolve()
    if not p.is_relative_to(ROOT.resolve()):raise ValueError(f'Outside workspace input:{p}')
    if not p.exists():raise FileNotFoundError(p)
    key=p.relative_to(ROOT).as_posix()
    if key not in HASHES:HASHES[key]=digest(p)
    return p

@lru_cache(maxsize=32)
def image(value):
    with Image.open(local_path(value)) as im:return im.convert('RGBA')

def premultiplied_resize(im,size):
    return im.convert('RGBa').resize(size,Image.Resampling.LANCZOS).convert('RGBA')

@lru_cache(maxsize=32)
def normalize(value):
    im=image(value)
    if im.size==SIZE:return im.copy()
    if im.height!=941 or im.width not in {1671,1672}:raise ValueError(f'Unexpected production canvas{im.size}:{value}')
    # Uniform common reference raster; one fixed right-edge crop only.
    # Fractional fixed crop keeps EXACTLY the same source/output scale in
    # both axes; the native reference has two repeated edge-pad pixels.
    return im.convert('RGBa').resize(SIZE,Image.Resampling.LANCZOS,
                                    box=(0,0,852*941/480,941)).convert('RGBA')

@lru_cache(maxsize=24)
def fixed_purple_slots(value):
    im=normalize(value)
    a=np.asarray(im).astype(np.int16)
    # R>G is essential here: generated DS blue hair must not be identified
    # as a speech panel. This extracts existing GRAPHICS, never character hair.
    m=(a[:,:,0]>a[:,:,1]+7)&(a[:,:,2]>a[:,:,0]+8)&(a[:,:,0]<140)&(a[:,:,1]<125)&(a[:,:,2]<180)
    m[400:]=False
    labels,n=label(m);counts=np.bincount(labels.ravel());objects=find_objects(labels)
    slots=[]
    for k,slices in enumerate(objects,1):
        if slices is None:continue
        ys,xs=slices;h=ys.stop-ys.start;w=xs.stop-xs.start
        if counts[k]<5000 or h<45 or w<45 or counts[k]/(h*w)<.4:continue
        full=np.zeros((480,852),bool);full[slices]=binary_fill_holes(labels[slices]==k)
        full[90:136,99:144]=False
        fill=np.median(a[m & (labels==k),:3],axis=0).astype('uint8')
        slots.append({'bbox':(xs.start,ys.start,xs.stop,ys.stop),'mask':Image.fromarray(full.astype('uint8')*255),
                      'fill':tuple(int(c) for c in fill)})
    return slots

def replace_panel_letters(stage,base_path,layers):
    if not layers:return
    slots=fixed_purple_slots(base_path)
    if not slots:return
    unused=list(layers)
    for slot in slots:
        stage.paste(slot['fill']+(255,),(0,0,852,480),slot['mask'])
        x0,y0,x1,y1=slot['bbox'];tx=(x0+x1)/2;ty=(y0+y1)/2
        if not unused:continue
        row=min(unused,key=lambda r:abs((r['bbox'][0]+r['bbox'][2])/2-tx))
        unused.remove(row)
        bubble=image(row['path']);bx0,by0,bx1,by1=row['bbox']
        arr=np.asarray(bubble.crop((bx0,by0,bx1,by1)))
        alpha=np.clip((arr[:,:,:3].min(axis=2).astype(float)-155)/100,0,1)*(arr[:,:,3]/255)
        glyph=np.full(arr.shape,255,dtype=np.uint8);glyph[:,:,3]=np.rint(alpha*255).astype('uint8')
        glyph=Image.fromarray(glyph,'RGBA')
        factor=min((x1-x0)/glyph.width,(y1-y0)/glyph.height)
        size=(max(1,round(glyph.width*factor)),max(1,round(glyph.height*factor)))
        glyph=premultiplied_resize(glyph,size)
        layer=Image.new('RGBA',SIZE)
        layer.alpha_composite(glyph,(round(tx-size[0]/2),round(ty-size[1]/2)))
        # Restrict typography to existing graphic shape; never cover the actor.
        layer.putalpha(Image.fromarray(np.minimum(np.asarray(layer.getchannel('A')),np.asarray(slot['mask']))))
        stage.alpha_composite(layer)

def frame_lists(rows,n=5245):
    out=[[] for _ in range(n)]
    for row in rows:
        a=max(0,int(row['start_frame']));b=min(n,int(row['end_frame_exclusive']))
        if b<a:raise ValueError('Inverted event interval')
        for i in range(a,b):out[i].append(row)
    return out

def single_event(rows,frame):
    matches=[r for r in rows if int(r['start_frame'])<=frame<int(r['end_frame_exclusive'])]
    return matches[-1] if matches else None

@lru_cache(maxsize=16)
def prop_raster(path,scale,blur):
    im=normalize(path)
    im=premultiplied_resize(im,(round(852*scale),round(480*scale)))
    if blur:
        im=im.convert('RGBa').filter(ImageFilter.GaussianBlur(blur)).convert('RGBA')
    return im

def add_prop(stage,event,frame,asset_id):
    if not event.get('path'):return None
    if asset_id=='game_prepare':return 'already baked in native game_prepare'
    if asset_id=='maid_close_paws':return 'source foreground fan ends at actual maid cut4263'
    d=event.get('default',{});scale=float(d.get('uniform_scale',.67));xy=d.get('translate_source_xy',[182,379])
    # Held whole-prop key positions, not a connecting-frame motion synthesis.
    for key in event.get('keyframes_proposal',[]):
        if int(key['frame'])<=frame:
            scale=float(key.get('uniform_scale',scale));xy=key.get('translate_source_xy',xy)
    if not math.isfinite(scale) or scale<=0:raise ValueError('Invalid uniform prop scale')
    im=prop_raster(event['path'],scale,float(d.get('soft_focus_sigma_source_pixels',1.25)))
    layer=Image.new('RGBA',SIZE);layer.alpha_composite(im,(round(xy[0]),round(xy[1])))
    stage.alpha_composite(layer)
    return {'event':event['event_id'],'uniform_scale':scale,'xy':xy,'operation':'whole independent prop; held key position'}

def mouth_sprite(stage,event):
    if event is None:raise ValueError('Blank-mouth pose has no source expression event')
    im=image(event['asset'])
    scale=float(event['width'])/im.width
    sprite=premultiplied_resize(im,(round(im.width*scale),round(im.height*scale)))
    xy=event['center_xy'];left=round(xy[0]-sprite.width/2);top=round(xy[1]-sprite.height/2)
    layer=Image.new('RGBA',SIZE);layer.alpha_composite(sprite,(left,top));stage.alpha_composite(layer)

@lru_cache(maxsize=64)
def clean_purple_bubble(value):
    """Rebuild an independent flat graphic; discard transitional RGB strips.

    Only the dominant purple speech polygon and its enclosed white glyphs
    survive. This never computes a character matte or changes actor art.
    """
    im=image(value);a=np.asarray(im)
    visible=a[:,:,3]>200
    if not visible.any():return im.copy()
    colors,counts=np.unique(a[visible,:3],axis=0,return_counts=True)
    color=colors[int(counts.argmax())]
    distance=np.max(np.abs(a[:,:,:3].astype(np.int16)-color.astype(np.int16)),axis=2)
    core=(distance<=6)&(a[:,:,3]>50)
    # Textured source bubbles contain disconnected flat-color islands;
    # join their nearby interiors before choosing the graphic component.
    lab,n=label(binary_dilation(core,iterations=3));sizes=np.bincount(lab.ravel());sizes[0]=0
    if not n or sizes.max()<200:return im.copy()
    polygon=binary_fill_holes(lab==int(sizes.argmax()))
    # A one-pixel edge restores the source antialias without retaining bars.
    polygon=binary_dilation(polygon,iterations=1)
    alpha=np.minimum(a[:,:,3],polygon.astype(np.uint8)*255)
    glyph=np.clip((a[:,:,:3].min(axis=2).astype(float)-155)/100,0,1)
    rgb=np.rint(color[None,None,:]*(1-glyph[:,:,None])+255*glyph[:,:,None]).astype(np.uint8)
    result=np.dstack([rgb,alpha])
    return Image.fromarray(result,'RGBA')

@lru_cache(maxsize=64)
def bubble_palette(value):
    old=np.asarray(image(value))
    colors,counts=np.unique(old[old[:,:,3]>200,:3],axis=0,return_counts=True)
    return tuple(map(int,colors[int(counts.argmax())])) if len(counts) else None

def source_purple_bubble(source,event,allow_fallback=True):
    """Source-timed graphic reconstruction from flat purple and white ink.

    The old extracted crop ended at y410 and could truncate low bubbles.
    No source character RGB is pasted: a polygon is filled with one purple
    color, and only enclosed white lettering is reconstructed on top.
    """
    palette=bubble_palette(event['path'])
    if palette is None:return clean_purple_bubble(event['path'])
    color=np.asarray(palette,np.uint8)
    original_bbox=event['bbox']
    x0,y0,x1,y1=event['bbox']
    x0=max(0,x0-24);x1=min(852,x1+24);y0=max(0,y0-12)
    y1=min(480,y1+(56 if y1>=400 else 12))
    rgb=np.asarray(source)[:,:,:3]
    roi=rgb[y0:y1,x0:x1]
    core=np.max(np.abs(roi.astype(np.int16)-color.astype(np.int16)),axis=2)<=(10 if allow_fallback else 6)
    lab,n=label(binary_dilation(core,iterations=3));sizes=np.bincount(lab.ravel());sizes[0]=0
    if not n or sizes.max()<200:return clean_purple_bubble(event['path'])
    main=lab==int(sizes.argmax());ys,xs=np.where(main)
    if allow_fallback and original_bbox[3]-original_bbox[1]>240 and ys.max()-ys.min()<.75*(original_bbox[3]-original_bbox[1]):
        # The source eye-insert wipe cuts this graphic, but that connecting
        # wipe was deliberately omitted. Hold its last complete speech key.
        return source_purple_bubble(image(event['path']),event,False)
    points=np.column_stack([xs,ys]);hull=ConvexHull(points)
    polygon=Image.new('L',(x1-x0,y1-y0))
    ImageDraw.Draw(polygon).polygon([tuple(map(int,v)) for v in points[hull.vertices]],fill=255)
    glyph_region=binary_erosion(binary_fill_holes(main),iterations=4)
    white=np.clip((roi.min(axis=2).astype(float)-155)/100,0,1)*glyph_region
    # Chinese captions are independent and start below the speech lettering.
    if y1>400:white[max(0,400-y0):]=0
    base=np.rint(color[None,None,:]*(1-white[:,:,None])+255*white[:,:,None]).astype(np.uint8)
    sprite=Image.fromarray(np.dstack([base,np.asarray(polygon)]),'RGBA')
    result=Image.new('RGBA',SIZE);result.alpha_composite(sprite,(x0,y0))
    return result

def source_white_paper(stage,source,side):
    # The source panel slides, while the native held drawing does not.
    # Fit ONLY its typography into the approved native paper's ink slot.
    box=(0,0,426,400) if side=='left' else (426,0,852,400)
    current=np.asarray(source.convert('RGB')).astype(np.float32)
    target=np.asarray(stage).astype(np.float32)
    x0,y0,x1,y1=box
    def paper_mask(arr):
        roi=arr[y0:y1,x0:x1,:3]
        mask=(roi.min(axis=2)>225)&((roi.max(axis=2)-roi.min(axis=2))<25)
        lab,n=label(mask);counts=np.bincount(lab.ravel());counts[0]=0
        if not n or counts.max()<6000:return None
        return binary_fill_holes(lab==int(counts.argmax()))
    sm=paper_mask(current);tm=paper_mask(target)
    if sm is None or tm is None:return False
    source_roi=current[y0:y1,x0:x1,:3]
    target_roi=target[y0:y1,x0:x1,:3]
    source_a=np.clip((195-source_roi.max(axis=2))/165,0,1)*sm
    target_a=np.clip((195-target_roi.max(axis=2))/165,0,1)*tm
    # Exclude the independently fixed black square from both text bounds.
    for alpha in [source_a,target_a]:
        if side=='left':alpha[90:136,99:144]=0
    def ink_bbox(alpha):
        ys,xs=np.where(alpha>.18)
        return None if not len(xs) else (int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1))
    sb=ink_bbox(source_a);tb=ink_bbox(target_a)
    if sb is None or tb is None:return False
    fill=Image.new('RGBA',SIZE,(255,255,255,255));mask=np.zeros((480,852),np.uint8);mask[y0:y1,x0:x1]=tm*255
    stage.paste(fill,(0,0),Image.fromarray(mask))
    ink_pixels=source_roi[(source_roi.max(axis=2)<150)&(source_a>.18)]
    ink=np.median(ink_pixels,axis=0).astype(np.uint8) if len(ink_pixels) else np.array([72,56,66],np.uint8)
    source_a=np.clip((255-source_roi.max(axis=2))/max(1,255-float(ink.max())),0,1)*sm
    source_a[source_roi.max(axis=2)>225]=0
    if side=='left':source_a[90:136,99:144]=0
    rgba=np.empty((400,x1-x0,4),np.uint8);rgba[:,:,:3]=ink;rgba[:,:,3]=np.rint(source_a*255).astype(np.uint8)
    glyph=Image.fromarray(rgba,'RGBA').crop(sb)
    factor=min((tb[2]-tb[0])/glyph.width,(tb[3]-tb[1])/glyph.height)*.96
    cx=x0+(tb[0]+tb[2])/2;cy=(tb[1]+tb[3])/2
    for attempt in range(12):
        fitted=premultiplied_resize(glyph,(max(1,round(glyph.width*factor)),max(1,round(glyph.height*factor))))
        overlay=Image.new('RGBA',SIZE);overlay.alpha_composite(fitted,(round(cx-fitted.width/2),round(cy-fitted.height/2)))
        oa=np.asarray(overlay.getchannel('A'))
        lost=float(oa[mask==0].sum())/max(1,float(oa.sum()))
        if lost<.001:break
        factor*=.92
    overlay.putalpha(Image.fromarray(np.minimum(np.asarray(overlay.getchannel('A')),mask)))
    stage.alpha_composite(overlay)
    return True

def add_rays(stage):
    # A held source face-graphic key. It does not rotate/warp the head.
    overlay=Image.new('RGBA',(1704,960));draw=ImageDraw.Draw(overlay)
    cx,cy=1266,278
    for j in range(12):
        angle=j*math.tau/12
        r0,r1=84,156
        p=(cx+math.cos(angle)*r0,cy+math.sin(angle)*r0)
        q=(cx+math.cos(angle)*r1,cy+math.sin(angle)*r1)
        draw.line([p,q],fill=(80,78,77,round(255*(j+2)/14)),width=13)
        for x,y in [p,q]:draw.ellipse((x-6,y-6,x+6,y+6),fill=(80,78,77,round(255*(j+2)/14)))
    stage.alpha_composite(premultiplied_resize(overlay,SIZE))

def scale_layer_alpha(im,amount):
    layer=im.copy()
    layer.putalpha(layer.getchannel('A').point(lambda p:round(p*min(1,max(0,amount)))))
    return layer

def endcard_overlay(stage,frame,tail):
    measurements=tail['measurements']
    frames=[m['frame'] for m in measurements]
    bg=float(np.interp(frame,frames,[m['purple_background_fade_estimate'] for m in measurements]))
    jp=float(np.interp(frame,frames,[m['jp_visibility_estimate'] for m in measurements]))
    stage=Image.blend(stage,Image.new('RGBA',SIZE,tuple(tail['purple_background_color'])+(255,)),min(1,max(0,bg)))
    stage.alpha_composite(scale_layer_alpha(image(tail['jp_layer']['path']),jp))
    # Actual source4987 has no CN;4988 has complete CN, not the slow JP fade.
    if frame>=4988:
        stage.alpha_composite(image(tail['cn_bar_layer']['path']))
        stage.alpha_composite(image(tail['cn_layer']['path']))
    return stage

def add_clock(stage,event):
    path=event.get('path') or event.get('asset')
    if not path:return
    registration=event.get('registration',event.get('placement',event.get('default',{})))
    scale=float(registration.get('uniform_scale',event.get('uniform_scale',1.04659695)))
    xy=registration.get('translate_source_xy',event.get('translate_source_xy',[-24.31076,-33.64143]))
    sprite=prop_raster(path,scale,0)
    layer=Image.new('RGBA',SIZE);layer.alpha_composite(sprite,(round(xy[0]),round(xy[1])))
    # The BACKGROUND prop must sit behind the CURRENT approved DS foreground.
    # This is z-order occlusion from rendered DS artwork, never source Miku
    # hair replacement/inpainting, and never changes any character pixels.
    a=np.asarray(stage).astype(np.int16)
    blue=(a[:,:,2]-a[:,:,0]>22)&(a[:,:,2]-a[:,:,1]>5)
    pink_background=(a[:,:,0]-a[:,:,1]>8)&(a[:,:,0]-a[:,:,2]>0)
    foreground_candidates=~pink_background
    labels,n=label(foreground_candidates)
    counts=np.bincount(labels[blue].ravel(),minlength=n+1)
    chosen=counts>30;chosen[0]=False
    foreground=chosen[labels]
    foreground=binary_dilation(binary_fill_holes(foreground),iterations=1)
    alpha=np.asarray(layer.getchannel('A')).copy();alpha[foreground]=0
    layer.putalpha(Image.fromarray(alpha))
    stage.alpha_composite(layer)
    return Image.fromarray(foreground.astype('uint8')*255)

def output_raster(im):
    raster=im.convert('RGB').resize((1278,720),Image.Resampling.LANCZOS)
    result=Image.new('RGB',(1280,720));result.paste(raster,(1,0));return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--label',required=True)
    parser.add_argument('--encode',action='store_true')
    parser.add_argument('--checkpoints-only',action='store_true')
    parser.add_argument('--start',type=int,default=0)
    parser.add_argument('--end',type=int,default=5245)
    parser.add_argument('--checkpoint-frames',default='')
    args=parser.parse_args()
    if '/' in args.label or '\\' in args.label or args.label in {'.','..'}:raise ValueError('Invalid output label')
    plan=read(PROJECT/'edit-plan.json')
    relevant_runs=[r for r in plan['runs'] if r['start_frame']<args.end and r['end_frame_exclusive']>args.start]
    relevant_missing=[r for r in relevant_runs if r['spec']['mode']=='missing' or r['spec'].get('native',{}).get('mode')=='missing']
    if relevant_missing:raise ValueError('Missing independently approved keys in requested render range')
    if plan['unresolved_baked_caption_clears']:raise ValueError('Baked subtitle clears unresolved')
    adoption=read(PROJECT/'audit/adopted-assets.json')
    approved={r['asset_id']:r for r in adoption['approved_assets']}
    for row in approved.values():
        if digest(local_path(row['path']))!=row['sha256']:raise ValueError('Changed approved native asset')
    graphics=read(PROJECT/'source/source-graphics-manifest.json')
    tail=read(PROJECT/'source/endcard/endcard-overlay-events.json')
    branding_path=PROJECT/'source/revision-v2/branding-events.json'
    branding=read(branding_path)['events'] if branding_path.exists() else []
    thinking_path=PROJECT/'source/revision-v2/thinking-effects/events.json'
    thinking_doc=read(thinking_path) if thinking_path.exists() else {}
    thinking=thinking_doc.get('events',[])
    revision_overlay_path=PROJECT/'source/revision-v2/overlay-events.json'
    revision_overlays_doc=read(revision_overlay_path) if revision_overlay_path.exists() else {}
    revision_overlays=revision_overlays_doc.get('events',[])
    for event in branding+thinking+revision_overlays:
        if event.get('sha256') and digest(local_path(event['path']))!=event['sha256']:
            raise ValueError('Changed revision graphic layer')
    HASHES[VIDEO.relative_to(ROOT).as_posix()]=digest(VIDEO)
    HASHES[AUDIO.relative_to(ROOT).as_posix()]=digest(AUDIO)
    mouth_path=PROJECT/'production/pair-a/mouth-events.json'
    mouths=read(mouth_path)['events'] if mouth_path.exists() else []
    if not mouths and any(r['spec'].get('blank_mouth') for r in relevant_runs):raise ValueError('Missing source taunt mouth event table')
    captions=frame_lists(graphics['caption_layers'])
    bubbles=frame_lists([r for r in graphics['graphic_layers'] if r.get('path') and r['kind']=='purple_bubble'])
    fan=frame_lists(graphics['foreground_fan_events'])
    timeline=[None]*5245
    for run in plan['runs']:
        for i in range(run['start_frame'],run['end_frame_exclusive']):timeline[i]=run['spec']
    if any(r is None for r in timeline):raise ValueError('Uncovered timeline')
    out=PROJECT/'renders'/args.label
    if out.exists():raise FileExistsError(out)
    (out/'checkpoints').mkdir(parents=True)
    (out/'logs').mkdir()
    # Full source is decoded sequentially to preserve the exact PTS/index map.
    wanted={1668,408,5016}
    wanted.update(int(r['spec']['source_reference_frame']) for r in plan['runs'] if r['spec']['mode'] in {'verified_nonhair_source_hold'})
    source_keys={}
    with av.open(str(VIDEO)) as container:
        for i,f in enumerate(container.decode(video=0)):
            if i in wanted:source_keys[i]=f.to_image().convert('RGBA')
            if i>=max(wanted):break
    checkpoints={r['start_frame'] for r in plan['runs']}
    checkpoints.update(int(v) for v in args.checkpoint_frames.split(',') if v.strip())
    for e in mouths:
        checkpoints.add(e['start_frame'])
        if e['start_frame']>0:checkpoints.add(e['start_frame']-1)
    for e in branding+thinking+revision_overlays:
        checkpoints.add(e['start_frame'])
        if e['start_frame']>0:checkpoints.add(e['start_frame']-1)
    checkpoints.update([31,145,173,210,211,423,426,1389,1390,1394,1395,1396,1403,
                        1500,1530,1818,2251,2252,2274,2275,2370,3026,3060,3120,
                        3558,3690,3846,3924,3960,4002,4104,4266,4290,4344,4428,
                        4470,4578,4638,4752,4770,4794,4818,4925,4926,4944,4974,4998,5016,5211,5212,5244])
    process=None;log=None
    target=out/'uno-ds-full-keyframes.mp4'
    if args.encode:
        log=(out/'logs/ffmpeg.txt').open('wb')
        command=[str(FFMPEG),'-hide_banner','-y','-f','rawvideo','-pix_fmt','rgb24','-s','1280x720','-r','24','-i','pipe:0',
                 '-ss',f'{args.start/24:.9f}','-i',str(AUDIO),'-map','0:v:0','-map','1:a:0','-t',f'{(args.end-args.start)/24:.9f}',
                 '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-r','24',
                 '-c:a','aac','-b:a','192k','-movflags','+faststart','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709',str(target)]
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
    ledger=[];last_signature=None;last_raster=None;started=time.time();written=0
    try:
      with av.open(str(VIDEO)) as container:
       for i,decoded in enumerate(container.decode(video=0)):
        if i<args.start:continue
        if i>=args.end:break
        if args.checkpoints_only and i not in checkpoints:continue
        row=timeline[i];mode=row['mode'];source=decoded.to_image().convert('RGBA')
        effect_id=row.get('effect_behavior_id',row.get('asset_id'))
        caption=captions[i][-1] if captions[i] else None
        mouth=single_event(mouths,i) if row.get('blank_mouth') else None
        if mouth and row.get('source_side')!=mouth['side']:raise ValueError(f'Wrong mouth side at source frame{i}')
        fan_events=[r for r in fan[i] if r.get('path')]
        clock_events=[r for r in graphics.get('clock_events',[]) if r['start_frame']<=i<r['end_frame_exclusive']]
        extra_events=[r for r in graphics.get('additional_graphic_layers',[]) if r['start_frame']<=i<r['end_frame_exclusive'] and r.get('path')]
        branding_events=[r for r in branding if r['start_frame']<=i<r['end_frame_exclusive'] and (not r.get('asset_ids') or row.get('asset_id') in r['asset_ids'])]
        thinking_events=[r for r in thinking if r['start_frame']<=i<r['end_frame_exclusive']]
        revision_overlay_events=[r for r in revision_overlays if r['start_frame']<=i<r['end_frame_exclusive']]
        # Prop can change at held key positions. Include exact keys in signature.
        fan_sig=tuple((r['event_id'],json.dumps([k for k in r.get('keyframes_proposal',[]) if k['frame']<=i],sort_keys=True)) for r in fan_events)
        signature=(json.dumps(row,sort_keys=True),caption['event_id'] if caption else None,
                   tuple(r['path'] for r in bubbles[i]),json.dumps(mouth,sort_keys=True),fan_sig,
                   'rays' if 4634<=i<4747 else None,
                   tuple(r.get('event_id',str(r.get('path'))) for r in clock_events+extra_events+branding_events+thinking_events+revision_overlay_events))
        dynamic=mode in {'safe_source_stream','native_to_verified_endcard_dissolve'} or effect_id in {'fold_arm_right','fold_arm_left'} or row.get('blank_mouth') or 4932<=i<5016
        if signature!=last_signature or dynamic:
            if mode=='solid_black':stage=Image.new('RGBA',SIZE,(0,0,0,255))
            elif mode=='native_key':stage=normalize(row['path']).copy()
            elif mode=='verified_plate_hold':stage=normalize(row['path']).copy()
            elif mode=='safe_source_stream':
                a,b=row['safe_range']
                if not a<=i<b:raise ValueError('Unsafe source passthrough')
                stage=source.copy()
            elif mode=='verified_nonhair_source_hold':stage=source_keys[row['source_reference_frame']].copy()
            elif mode=='native_to_verified_endcard_dissolve':
                stage=normalize(row['native']['path']).copy()
            else:raise ValueError(mode)
            if mode=='native_key':
                if row.get('blank_mouth'):
                    mouth_sprite(stage,mouth)
                    for bubble in bubbles[i]:stage.alpha_composite(source_purple_bubble(source,bubble))
                elif effect_id in {'fold_arm_right','fold_arm_left'}:
                    source_white_paper(stage,source,'right' if effect_id=='fold_arm_right' else 'left')
                elif effect_id in {'intro_palm_open','taunt_left_close','taunt_right','taunt_left_table','taunt_right_close',
                                             'tease_forehead_A','tease_forehead_B','tease_flip','cover_eyes','creep_palms',
                                             'explain_palms_A','explain_palms_B','game_set','raise_card'}:
                    replace_panel_letters(stage,row['path'],bubbles[i])
                if not thinking and 4634<=i<4747 and effect_id=='side_blank_eyes':add_rays(stage)
                for event in thinking_events:stage.alpha_composite(normalize(event['path']))
                for event in fan_events:add_prop(stage,event,i,effect_id)
                for event in clock_events:
                    occlusion=add_clock(stage,event)
                    if i in checkpoints and occlusion is not None:occlusion.save(out/'checkpoints'/f'f{i:05d}-current-DS-occlusion.png')
                # Additional independently extracted safe clock/graphic sprites.
                for event in extra_events:stage.alpha_composite(normalize(event['path']))
            # Character-free original streams already carry the correct text.
            if mode in {'native_key','verified_nonhair_source_hold'} and not (i>=4926 and row.get('asset_id')=='final_card_chaos_textless'):
                old=row.get('baked_caption_bbox')
                new=caption.get('bbox') if caption and caption.get('path') else None
                if old or new:
                    boxes=[b for b in [old,new] if b]
                    box=(min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes))
                    # Opaque neutral rebuilt bar hides previous baked letters;
                    # source actor/background RGB is never pasted into this layer.
                    if row.get('native_overlay_clean') and not old:
                        bar=Image.new('RGBA',SIZE)
                        ImageDraw.Draw(bar).rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=(65,69,71,155))
                        stage.alpha_composite(bar)
                    else:
                        ImageDraw.Draw(stage).rectangle((box[0],box[1],box[2]-1,box[3]-1),fill=(82,83,85,255))
                if caption and caption.get('path'):stage.alpha_composite(image(caption['path']))
            if 4932<=i<5016:stage=endcard_overlay(stage,i,tail)
            for event in revision_overlay_events:stage.alpha_composite(normalize(event['path']))
            for event in branding_events:stage.alpha_composite(normalize(event['path']))
            if mode not in {'solid_black'}:
                ImageDraw.Draw(stage).rectangle((99,90,142,134),fill=(0,0,0,255))
            last_raster=output_raster(stage);last_signature=signature
        if i in checkpoints:
            last_raster.save(out/'checkpoints'/f'f{i:05d}.png')
            comparison=Image.new('RGB',(1280,360));comparison.paste(source.convert('RGB').resize((639,360),Image.Resampling.LANCZOS),(0,0))
            comparison.paste(last_raster.resize((640,360),Image.Resampling.LANCZOS),(640,0))
            comparison.save(out/'checkpoints'/f'f{i:05d}-source-left.jpg',quality=92)
        record={'frame':i,'source_pts':decoded.pts,'source_time_base':str(decoded.time_base),'shot_id':row['shot_id'],
                'body_mode':mode,'asset_id':row.get('asset_id'),'caption':caption['event_id'] if caption else None,
                'bubble_sources':[r['source_frame'] for r in bubbles[i]],'mouth_state':mouth.get('state') if mouth else None,
                'fan_events':[r['event_id'] for r in fan_events],
                'branding_events':[r['event_id'] for r in branding_events],
                'thinking_events':[r['event_id'] for r in thinking_events],
                'revision_graphics':[r['event_id'] for r in revision_overlay_events],
                'pose_scale':1,'pose_deformation':False}
        ledger.append(record)
        if process:process.stdin.write(last_raster.tobytes())
        written+=1
        if written%480==0:print(json.dumps({'written_frames':written,'source_frame':i,'elapsed_seconds':round(time.time()-started,1)}),flush=True)
    finally:
        if process:
            process.stdin.close();code=process.wait()
            log.close()
            if code:raise RuntimeError(f'ffmpeg failed:{code}; see log')
    (out/'frame-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'input-hashes.json').write_text(json.dumps(HASHES,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'edit-plan-used.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'source-graphics-used.json').write_text(json.dumps(graphics,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'mouth-events-used.json').write_text(json.dumps(mouths,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'branding-events-used.json').write_text(json.dumps(branding,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'thinking-events-used.json').write_text(json.dumps(thinking_doc,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'revision-overlay-events-used.json').write_text(json.dumps(revision_overlays_doc,ensure_ascii=False,indent=2),encoding='utf-8')
    result={'render_label':args.label,'written_frames':written,'source_start':args.start,'source_end_exclusive':args.end,
            'duration_seconds':(args.end-args.start)/24,'render_seconds':round(time.time()-started,2),
            'video':str(target) if args.encode else None,'video_sha256':digest(target) if args.encode else None,
            'renderer_sha256':digest(Path(__file__)),'checks_scope':'actual sequential decode/render; semantic review remains separate'}
    (out/'render-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
