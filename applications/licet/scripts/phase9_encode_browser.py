"""encode actual screen frames at recorded timing; optional idle wait shortening"""
import argparse
import json
import subprocess
from pathlib import Path


def encode(root, *, shorten_idle=False, screen_directory='screen'):
    root=Path(root).resolve()
    screen=root/screen_directory
    data=json.loads((screen/'frames.json').read_text())
    frames=data['frames']
    marks_path=root/'recording.json'
    marks=json.loads(marks_path.read_text()) if marks_path.exists() else {}
    replay_start=marks.get('replay_started',float('inf'))-marks.get('started',0)
    if len(frames)<2:
        raise ValueError('A browser recording needs at least two captured frames')
    name='licet-browser-demo' if shorten_idle else 'licet-browser-full'
    lines=[]
    duration=0
    cuts=[]
    for i,frame in enumerate(frames):
        end=frames[i+1]['seconds'] if i+1<len(frames) else data['duration']
        elapsed=max(0.001,end-frame['seconds'])
        used=min(elapsed,2.0) if shorten_idle and frame['seconds']<replay_start else elapsed
        if shorten_idle and i == len(frames)-1:
            used=max(used,5.0)
        duration+=used
        if used<elapsed:cuts.append({'start':frame['seconds'],'removed_seconds':elapsed-used})
        lines.extend(["file '"+str(screen/frame['file'])+"'",'option framerate 1000',f'duration {used:.6f}'])
    lines.append("file '"+str(screen/frames[-1]['file'])+"'")
    lines.append('option framerate 1000')
    playlist=root/(name+'.ffconcat');playlist.write_text('\n'.join(lines)+'\n')
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(playlist),
                    '-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2','-r','30','-c:v','libx264','-preset','fast','-crf','19',
                    '-pix_fmt','yuv420p','-movflags','+faststart',str(root/(name+'.mp4'))],check=True)
    (root/(name+'-edits.json')).write_text(json.dumps({'type':'continuous actual browser screencast','idle_waits_shortened':shorten_idle,
        'final_frame_minimum_hold_seconds':5 if shorten_idle else None,
        'frames':len(frames),'duration_seconds':duration,'removed_idle_intervals':cuts,'audio':False},indent=2)+'\n')
    print(name,len(frames),'frames',round(duration,1),'seconds',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root')
    parser.add_argument('--screen-directory', default='screen')
    args=parser.parse_args()
    encode(args.root,screen_directory=args.screen_directory)
    encode(args.root,shorten_idle=True,screen_directory=args.screen_directory)
