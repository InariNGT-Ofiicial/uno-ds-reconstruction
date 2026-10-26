"""List candidate neighboring native keys; never auto-approve consistency."""
from pathlib import Path
import argparse,json
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    plan=json.loads((ROOT/'project/edit-plan.json').read_text(encoding='utf-8'));previous=None;pairs={}
    for run in plan['runs']:
        spec=run['spec'];current=spec.get('asset_id') if spec['mode']=='native_key' else None
        if current and previous and current!=previous:
            pairs.setdefault((previous,current),[]).append(run['start_frame'])
        previous=current
    doc={'scope':'Current timeline candidate pairs, consecutive native-key runs only; not the historical81-pair audit and not semantic approval.',
      'pairs':[{'previous':a,'current':b,'switch_frames':frames} for (a,b),frames in pairs.items()]}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'unique_candidate_pairs':len(pairs)}))
if __name__=='__main__':main()
