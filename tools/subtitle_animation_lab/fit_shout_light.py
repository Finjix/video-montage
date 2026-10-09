"""Fit the central flash separately from the expanding, occluded echo."""
from __future__ import annotations
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cv2
import numpy as np
from common import *
from light_shader import fit_light,freeze,shade_light
from renderer import text_sprite,transform
from fit_motion import expanded_ice_shader


def fit(frames):
    static=read_json(LAB.parents[1] / 'assets/packaging/animations/parameters/static.json')
    parameters=read_json(LAB.parents[1] / 'assets/packaging/animations/parameters/shout_wave.json')
    sprite=text_sprite(REFERENCE_TEXT)
    origin,shape=(420,2130),(460,1080)
    scales={10:1.066,11:1.066,12:1.066,13:1.064,14:1.044,15:1.021}
    analysis=ROOT/'work/subtitle-animation-calibration/motion'
    for n,ref in enumerate(decode(LAB/'references/shout_wave.mp4',(*origin,shape[1],shape[0]))):
        if n not in frames:continue
        old=parameters['frames'][n]
        scale=scales.get(n,1.)
        layers=[layer for layer in old.get('layers',[]) if layer['scale']>scale+.055]
        g=static['geometry']
        geometry={'x':(sprite.x+g['x']-960)*scale+960-origin[0],'y':(sprite.y+g['y']-2358)*scale+2358-origin[1],
                  'size':g['size']*scale,'sx':g['sx'],'tracking':g['tracking']*scale}
        prior=expanded_ice_shader(static['shader'],True)
        prior={**prior,'distance':(np.array(prior['distance'])*scale).tolist(),'normal_scale':prior['normal_scale']*scale}
        distance=signed_distance(glyph_alpha(REFERENCE_TEXT,geometry,shape))
        echo=np.zeros_like(ref,np.float32)
        for layer in layers:echo+=transform(sprite.rgb,sprite,layer['scale'],shape,origin)*np.array(layer['weight'],np.float32)
        # Opaque letters and strokes occlude the echo rather than adding it on top.
        alpha=np.where(distance>=-8.5*scale,1.,0.).astype(np.float32)
        echo*=1-alpha[:,:,None]
        wanted=np.clip(ref.astype(np.float32)-echo,0,255).astype(np.uint8)
        shader,distance=fit_light(wanted,REFERENCE_TEXT,geometry,prior,iterations=90,node_spacing=28,cutoff=80.)
        main=shade_light(distance,shader)
        # Glyphs occlude echoes; emissive outer glow remains additive.
        alpha=np.where(distance>=-8.5*scale,1.,0.)
        final_echo=np.zeros_like(echo)
        for layer in layers:final_echo+=transform(sprite.rgb,sprite,layer['scale'],shape,origin)*np.array(layer['weight'],np.float32)
        generated=np.clip(np.rint(main+final_echo*(1-alpha[:,:,None])),0,255).astype(np.uint8)
        score=foreground_ssim(ref,generated)
        print(f'Shout contour light {n:02}: {score:.6f}',flush=True)
        if score>parameters['calibration_scores'][n]:
            metadata=freeze(shader,LAB.parents[1] / 'assets/packaging/animations/parameters',f'shout-light-{n:02}.npz')
            parameters['frames'][n]={'model':'surface','geometry':geometry,'shader':metadata,'layers':layers,'echo_occlusion':True}
            parameters['calibration_scores'][n]=score
            parameters['calibration_entry_mean']=float(np.mean(parameters['calibration_scores']))
            write_json(LAB.parents[1] / 'assets/packaging/animations/parameters/shout_wave.json',parameters)
        cv2.imwrite(str(analysis/f'shout-light-{n:02}.png'),cv2.cvtColor(np.hstack([ref,generated]),cv2.COLOR_RGB2BGR))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--frames',default='10,11,12,13,14,15,16,17,18,19,20,21,22')
    args=parser.parse_args();cv2.setNumThreads(2)
    fit(set(map(int,args.frames.split(','))))
