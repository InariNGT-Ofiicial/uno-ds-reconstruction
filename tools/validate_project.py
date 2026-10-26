"""Structural/data validation, not semantic or aesthetic image approval."""
from pathlib import Path
import argparse,hashlib,json
ROOT=Path(__file__).resolve().parents[1];PROJECT=ROOT/'project'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
def main():
    p=argparse.ArgumentParser();p.add_argument('--metadata-only',action='store_true');args=p.parse_args()
    plan=read(PROJECT/'edit-plan.json');cursor=0
    allowed={'native_key','solid_black','safe_source_stream','verified_plate_hold','verified_nonhair_source_hold','native_to_verified_endcard_dissolve'}
    for run in plan['runs']:
        assert run['start_frame']==cursor,'Timeline gap or overlap'
        assert run['end_frame_exclusive']>cursor,'Empty run'
        spec=run['spec'];assert spec['mode'] in allowed,'Unknown/unresolved rendering mode'
        if spec['mode']=='native_key':assert spec['pose_scale']==1 and not spec['pose_local_transforms']
        cursor=run['end_frame_exclusive']
    assert cursor==plan['source_frames']==5245
    assert not plan['missing_approved_assets'] and not plan['unresolved_baked_caption_clears']
    intro={r['spec'].get('asset_id') for r in plan['runs'] if r['start_frame']<145 and r['end_frame_exclusive']>31}
    assert intro=={'intro_palm_open'},'Final user opening policy changed'
    rows=read(PROJECT/'assets-manifest.json')['files'];assert len({r['path'] for r in rows})==len(rows)
    for row in rows:
        path=(ROOT/row['path']).resolve();assert path.is_relative_to(PROJECT.resolve())
        assert len(row['sha256'])==64
        if not args.metadata_only:
            if not path.exists():raise FileNotFoundError('Install assets first: '+row['path'])
            assert sha(path)==row['sha256'],'Asset hash mismatch: '+row['path']
    if not args.metadata_only:
        inputs=read(PROJECT/'source-inputs.json')
        for kind in ['video','audio']:assert sha(ROOT/inputs[kind]['path'])==inputs[kind]['sha256']
    ledger=read(ROOT/'evidence/generation-ledger.json')
    assert ledger['actual_call_count']==len(ledger['calls'])==130
    for row in ledger['calls']:assert sha(ROOT/row['prompt'])==row['public_prompt_sha256']
    print(json.dumps({'metadata_only':args.metadata_only,'covered_frames':cursor,'timeline_runs':len(plan['runs']),'declared_assets':len(rows),'calls':len(ledger['calls']),'semantic_approval_claimed':False}))
if __name__=='__main__':main()
