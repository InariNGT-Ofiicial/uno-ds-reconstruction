from __future__ import annotations
from pathlib import Path
import json
import hashlib
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'project'
N=5245

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

ALIASES={
    'intro_palm_up':'intro_palm_open',
    'taunt_close_B':'taunt_close_A','taunt_close_C':'taunt_close_A',
    'taunt_flank_B':'taunt_close_A','taunt_left_A':'taunt_close_A',
    'taunt_right_A':'taunt_flank_A','clap_pose_B':'clap_pose_A',
    'plead_B':'plead_A','wink_reprise':'wink_hand',
    'side_rays':'side_blank_eyes','mouth_strip_A':'mouth_horizontal',
    'mouth_strip_B':'mouth_vertical',
}
CLEAN={
    'chorus_tilt_left':'chorus_left_textless',
    'chorus_tilt_right':'chorus_right_textless',
    'taunt_close_A':'taunt_neutral_textless_blankmouth',
    'taunt_flank_A':'taunt_flank_textless_blankmouth',
    'center_dark_card':'center_dark_card_textless',
    'final_card_chaos':'final_card_chaos_textless',
}

def canonical(key):
    seen=set()
    while key in ALIASES:
        if key in seen:raise ValueError('Alias cycle')
        seen.add(key);key=ALIASES[key]
    return key

def main():
    adoption=read(PROJECT/'audit/adopted-assets.json')
    approved={r['asset_id']:r for r in adoption['approved_assets']}
    for row in approved.values():
        path=PROJECT/row['path']
        if digest(path)!=row['sha256']:raise ValueError(f'Approved bytes changed: {path}')
    shots=read(PROJECT/'source/shots.json')
    keys=read(PROJECT/'source/action-keys.json')
    original_keys={r['id']:r for r in keys['poses']}
    text=read(PROJECT/'source/text-event-keys.json')
    captions=text['caption_events']
    small=np.load(PROJECT/'source/source-small-rgb.npz')['frames']
    safe=read(PROJECT/'audit/source-passthrough-ranges.json')
    plates={r['shot']:r for r in safe['wipe_replacement_plates']}
    starts=[]
    timing=[]
    for shot in shots:
        proposal=int(shot['start_frame_proposal'])
        candidate=int(shot.get('closest_large_jump_frame_candidate',proposal))
        strength=float(shot.get('closest_jump_mad',0))
        refined=candidate if abs(candidate-proposal)<=6 and strength>=15 else proposal
        starts.append(refined)
        timing.append({'shot':shot['shot_id'],'navigation_proposal':proposal,'source_pixel_change_candidate':candidate,
                       'source_change_mean_absolute_difference':strength,'chosen_start':refined,
                       'basis':'read-only full-rate source appearance discontinuity; original key drawings remain held'})
    exact_starts={'S000_black':0,'S001_intro':31,'S002_wipe':145,'S003_card_hand':173,
                  'S004_comic':211,'S005_wipe':248,'S006_center':265,'S008_score':396,
                  'S016_wipe':797,'S017_dark_center':800,'S050_wipe':2065,'S051_fanart_card':2069,
                  'S045_dynamite_eye':1657,'S100_dynamite_eye_reprise':3851,
                  'S055_opponent_fan':2252,'S056_center_short':2275,'S072_wipe_partial':2984,
                  'S109_center_taunt_open':4333,'S113_side_rays':4634,
                  'S117_wipe':4813,'S118_final_card_chaos':4814,'S119_end_card':4998,'S120_end_black':5212}
    # Closed-eye to open-eye source event: exact first sustained source
    # eye-area jump4333. Original template4290 has7..17 dark pixels in
    # the diagnostic small ROI; open4333 has100. No actor pixels are pasted.
    for j,shot in enumerate(shots):
        if shot['shot_id'] in exact_starts:starts[j]=exact_starts[shot['shot_id']]
        timing[j]['chosen_start']=starts[j]
    if any(b<=a for a,b in zip(starts,starts[1:])):raise ValueError('Refined cuts not monotonic')
    timeline=[None]*N
    missing=set()
    plan_shots=[]
    # Exact character-free evidence refines the corresponding coarse cuts.
    patches={shot['shot_id']:(starts[j],starts[j+1] if j+1<len(starts) else N) for j,shot in enumerate(shots)}

    def asset_for(key):
        key=canonical(key)
        target=CLEAN.get(key,key)
        if target not in approved:
            missing.add(target)
            return target,None
        return target,approved[target]

    def caption_at(frame):
        return next((e for e in reversed(captions) if e['source_frame']<=frame),None)

    def base_record(key):
        asset_id,asset=asset_for(key)
        if asset is None:return {'mode':'missing','asset_id':asset_id}
        clean=asset_id in set(CLEAN.values()) or bool(asset.get('native_overlay_clean',False))
        caption=None if clean else caption_at(int(asset.get('source_frame',0)))
        return {'mode':'native_key','asset_id':asset_id,'path':asset['path'],
                'effect_behavior_id':asset.get('effect_behavior_id',asset_id),
                'sha256':asset['sha256'],'source_reference_frame':asset.get('source_frame'),
                'native_to_source':'one shared uniform height scale480/941; fixed right crop to852',
                'pose_scale':1.0,'pose_local_transforms':False,
                'blank_mouth':bool(asset.get('blank_mouth',asset_id.startswith('taunt_') and 'blankmouth' in asset_id)),
                'native_overlay_clean':clean,
                'baked_caption_bbox':None if clean else asset.get('baked_caption_bbox',caption.get('bbox_including_gray_bar') if caption and caption['kind']=='caption' else None),
                'baked_caption_reference_text':None if clean else (caption.get('text') if caption and caption['kind']=='caption' else None)}

    def set_range(a,b,record):
        for i in range(a,b):
            if timeline[i] is not None:raise ValueError(f'Overlapping film coverage at{i}')
            timeline[i]=dict(record)

    for shot in shots:
        sid=shot['shot_id'];a,b=patches.get(sid,(shot['start_frame_proposal'],shot['end_frame_exclusive_proposal']))
        posed=[canonical(k) for k in shot['core_pose_candidates']]
        posed=list(dict.fromkeys(posed))
        if sid=='S004_comic':posed=['comic_panel']
        if sid=='S051_fanart_card':posed=['fanart_insert']
        if sid in {'S000_black','S120_end_black'}:
            set_range(a,b,{'mode':'solid_black','reason':'verified original black interval'})
        elif ('wipe' in sid and not posed) or sid=='S072_wipe_partial':
            prefix=sid.split('_')[0]
            p=plates.get(prefix,plates['S002'])
            if digest(PROJECT/p['path'])!=p['sha256']:raise ValueError('Plate bytes changed')
            set_range(a,b,{'mode':'verified_plate_hold','path':p['path'],'sha256':p['sha256'],
                           'source_reference_frame':p['source_plate_frame'],'reason':'fully covering graphic key; source partial-character fades omitted'})
        elif sid=='S003_card_hand':
            set_range(a,b,{'mode':'safe_source_stream','safe_range':[173,211]})
        elif sid=='S008_score':
            set_range(a,b,{'mode':'verified_nonhair_source_hold','source_reference_frame':408,
                           'classification':'source safe interval[396,423), full score-text/empty-table key; connecting reveal omitted'})
        elif sid=='S055_opponent_fan':
            set_range(a,b,{'mode':'safe_source_stream','safe_range':[2252,2275]})
        elif sid in {'S045_dynamite_eye','S100_dynamite_eye_reprise'}:
            set_range(a,b,{'mode':'verified_nonhair_source_hold','source_reference_frame':1668,
                           'path':'source/action-key-source/f01668.png',
                           'classification':'safe_single_frame_holds/source-passthrough-ranges.json; original eyes/eyebrows/nose graphic, no hair/accessory',
                           'baked_caption_bbox':[270,406,579,464],'baked_caption_reference_text':'=Dynamite='})
        elif sid=='S119_end_card':
            final=base_record('final_card_chaos')
            set_range(a,min(5016,b),{'mode':'native_to_verified_endcard_dissolve','native':final,
                                    'source_reference_frame':5016,'fade_start':a,'fade_end':5016,
                                    'reason':'DS-only fade, never source-Miku residue'})
            set_range(5016,b,{'mode':'safe_source_stream','safe_range':[5016,5212]})
        elif not posed:
            raise ValueError(f'Unclassified shot{sid}')
        else:
            if posed[0]=='center_card' and any(p.startswith('dab_') for p in posed):
                posed=[p for p in posed if p!='center_card']
            records=[base_record(key) for key in posed]
            if len(records)==1:
                set_range(a,b,records[0])
            elif set(posed)=={'chorus_tilt_left','chorus_tilt_right'}:
                # Select whole held drawings by actual original central character
                # appearance. This is an analysis-only classifier, not a warp.
                roi=(slice(10,52),slice(43,104))
                templates=[small[original_keys[p]['source_frame']][roi].astype(np.float32) for p in posed]
                state=0;last_change=a
                switches=[]
                for i in range(a,b):
                    sample=small[i][roi].astype(np.float32)
                    costs=[float(np.mean((sample-t)**2)) for t in templates]
                    candidate=int(np.argmin(costs))
                    if i==a:state=candidate
                    elif candidate!=state and i-last_change>=6 and costs[candidate]<costs[state]*.92:
                        state=candidate;last_change=i;switches.append(i)
                    set_range(i,i+1,records[state])
                shot['analysis_only_selection']={'method':'central-source appearance nearest two native source extrema; hysteresis; minimum6-frame hold',
                                                  'roi_small_xyxy':[43,10,104,52],'switch_frames':switches,
                                                  'requires_actual_encoded_adjacency_review':True}
            else:
                reference_shot=next(s for s in shots if [canonical(k) for k in s['core_pose_candidates']]==[canonical(k) for k in shot['core_pose_candidates']])
                origin=reference_shot['start_frame_proposal']
                switches=[(a,0)]
                for j,key in enumerate(posed[1:],1):
                    source_index=original_keys.get(key,{}).get('source_frame',origin)
                    offset=max(1,source_index-origin)
                    if a+offset<b:switches.append((a+offset,j))
                switches.sort()
                for j,(start,state) in enumerate(switches):
                    stop=switches[j+1][0] if j+1<len(switches) else b
                    set_range(start,stop,records[state])
            for i in range(a,b):
                timeline[i]['action_key_policy']='whole approved drawing held; connecting poses discarded'
        for i in range(a,b):timeline[i]['shot_id']=sid
        plan_shots.append({'shot_id':sid,'start_frame':a,'end_frame_exclusive':b,'source_proposal':shot})

    # Original391..395 is a verified fully covering graphic after the dab.
    for i in range(391,396):
        timeline[i]={'mode':'safe_source_stream','safe_range':[391,396],'shot_id':'S007_dab_graphic_gap'}
    # Read-only original green-hair centroid chooses which already approved
    # complete taunt key is on screen, including the additional1634 side cut.
    # This is classification only: no source-hair matte enters production.
    taunt_side_switches=[];previous_side=None
    for i,row in enumerate(timeline):
        if row and row.get('blank_mouth'):
            sample=small[i,:65].astype(np.int16)
            green=(sample[:,:,1]-sample[:,:,0]>18)&(sample[:,:,1]-sample[:,:,2]>2)
            ys,xs=np.nonzero(green)
            side=('neutral' if float(xs.mean())<71 else 'flank') if len(xs)>10 else (previous_side or 'neutral')
            if side!=previous_side:taunt_side_switches.append({'frame':i,'side':side,'source_green_centroid_x_small':float(xs.mean()) if len(xs) else None})
            key='taunt_close_A' if side=='neutral' else 'taunt_flank_A'
            replacement=base_record(key);replacement['shot_id']=row['shot_id'];replacement['source_side']=side
            replacement['action_key_policy']='choose same-side approved whole held key; no body deformation'
            timeline[i]=replacement;previous_side=side
        else:previous_side=None
    mouth_file=PROJECT/'production/pair-a/mouth-events.json'
    mouth_table=read(mouth_file) if mouth_file.exists() else None
    if mouth_table:
        for override in mouth_table['action_cut_overrides']:
            for i in range(override['start_frame'],override['end_frame_exclusive']):
                old=timeline[i]
                replacement=base_record('taunt_close_A' if override['side']=='neutral' else 'taunt_flank_A')
                if replacement.get('sha256')!=override['body_sha256']:raise ValueError('Mouth/body adoption mismatch')
                replacement['shot_id']=old['shot_id'];replacement['source_side']=override['side']
                replacement['action_key_policy']='actual source side event -> matching independently approved whole held body'
                timeline[i]=replacement
    # User revision: source-audited new actions and native hair corrections.
    # Apply after old aliases/mouth-body defaults so corrected hand choices
    # cannot silently be overwritten by the historical two-pose table.
    revision_path=PROJECT/'revision-v2-plan.json'
    revision=read(revision_path) if revision_path.exists() else {}
    replacements=revision.get('asset_replacements',{})
    for i,old in enumerate(timeline):
        if old and old.get('asset_id') in replacements:
            target=replacements[old['asset_id']]
            new=base_record(target)
            new['effect_behavior_id']=approved[target].get('effect_behavior_id',old.get('effect_behavior_id',old['asset_id']))
            for k in ['shot_id','source_side','action_key_policy']:
                if k in old:new[k]=old[k]
            new['revision_basis']='native replacement: '+old['asset_id']
            timeline[i]=new
    for override in revision.get('range_overrides',[]):
        a,b=int(override['start_frame']),int(override['end_frame_exclusive'])
        if not 0<=a<b<=N:raise ValueError('Invalid revision range')
        for i in range(a,b):
            old=timeline[i]
            new=base_record(override['asset_id'])
            new['shot_id']=old['shot_id']
            if override.get('source_side',old.get('source_side')):
                new['source_side']=override.get('source_side',old.get('source_side'))
            new['action_key_policy']='source-audited action extreme held; no geometric pose synthesis'
            new['revision_basis']=override['reason']
            timeline[i]=new
    revision3_path=PROJECT/'revision-v3-plan.json'
    revision3=read(revision3_path) if revision3_path.exists() else {}
    for override in revision3.get('range_overrides',[]):
        for i in range(override['start_frame'],override['end_frame_exclusive']):
            new=base_record(override['asset_id']);new['shot_id']=timeline[i]['shot_id']
            new['action_key_policy']=override['reason'];timeline[i]=new
    gaps=[i for i,r in enumerate(timeline) if r is None]
    if gaps:raise ValueError(f'Film coverage gaps:{gaps[:10]}')
    runs=[]
    for i,row in enumerate(timeline):
        if runs and row==runs[-1]['spec']:
            runs[-1]['end_frame_exclusive']=i+1
        else:runs.append({'start_frame':i,'end_frame_exclusive':i+1,'spec':row})
    clears={}
    for i,row in enumerate(timeline):
        caption=caption_at(i)
        if row.get('baked_caption_bbox') and (not caption or caption['kind']=='clear_caption'):
            clears.setdefault(row.get('asset_id',row['mode']),[]).append(i)
    clear_report={};short_blank_bars={}
    for k,v in clears.items():
        runs_clear=[]
        for i in v:
            if runs_clear and i==runs_clear[-1][-1]+1:runs_clear[-1].append(i)
            else:runs_clear.append([i])
        record={'first':v[0],'last':v[-1],'frames':len(v),'maximum_contiguous':max(map(len,runs_clear))}
        if record['maximum_contiguous']<=6:
            short_blank_bars[k]=record
        else:clear_report[k]=record
    result={'schema_version':'uno-fullfilm-held-action-plan-1','source_frames':N,'fps_container':24,
            'duration':N/24,'generation_sampling':'adaptive action keys; no24fps/new fixed4fps artwork obligation',
            'all_coverage_resolved':not missing,'missing_approved_assets':sorted(missing),
            'unresolved_baked_caption_clears':clear_report,
            'brief_clear_reconstructed_blank_bar':short_blank_bars,
            'only_geometry':'shared raster normalization; optional whole-pose uniform magnification; actual plan all pose_scale1',
            'source_passthrough_gate':'audit/source-passthrough-ranges.json',
            'adoption_sha256':digest(PROJECT/'audit/adopted-assets.json'),
            'aliases_deliberate_connecting_pose_omissions':ALIASES,
            'source_cut_refinement':timing,'taunt_side_selection_events':taunt_side_switches,
            'actual_source_mouth_body_override_table':'production/pair-a/mouth-events.json' if mouth_table else None,
            'mouth_event_table_sha256':digest(mouth_file) if mouth_table else None,
            'user_revision_v2':revision if revision else None,
            'user_revision_v3':revision3 if revision3 else None,
            'shots':plan_shots,'runs':runs}
    (PROJECT/'edit-plan.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'runs':len(runs),'shots':len(plan_shots),'missing':sorted(missing),'baked_caption_clears':clear_report},ensure_ascii=False))

if __name__=='__main__':main()
