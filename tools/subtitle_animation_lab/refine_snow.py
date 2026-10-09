"""Fit subpixel star-shaped snow emitters, rather than circular dots."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cv2
import numpy as np
from common import *
from light_shader import shade_light
from renderer import paint_snow,settle_scene
from fit_motion import nonnegative_least_squares


def stars(reference,base,distance,geometry):
    residual=reference.astype(np.float32)-base
    bright=np.maximum(residual,0).max(2)
    detail=bright-cv2.GaussianBlur(bright,(0,0),2.)
    original=reference.max(2).astype(np.float32)
    original_detail=original-cv2.GaussianBlur(original,(0,0),2.)
    peaks=(bright>=cv2.dilate(bright,np.ones((5,5),np.uint8)))&(detail>8)&(original_detail>7)&(bright>18)
    peaks&=(distance < -8)|((bright>40)&(original_detail>20))
    ys,xs=np.where(peaks);order=np.argsort(bright[ys,xs])[::-1]
    emission=np.zeros_like(base)
    particles=[];locations=[]
    width=dynamic_flowers.advance(REFERENCE_TEXT,geometry,str(FONT))
    shapes=[{'sx':sx,'sy':sy} for sx,sy in [(.65,.85),(.45,2.),(.55,3.5),(2.,.45),(2.8,2.8),(5.,5.)]]
    shapes += [{'kind':'diamond','sx':sx,'sy':sy,'power':power} for sx,sy,power in [(1.5,3.,1),(2.,4.,1),(2.5,5.,1),(3.,6.,2)]]
    for index in order[:650]:
        px,py=int(xs[index]),int(ys[index])
        if any((x-px)**2+(y-py)**2<6 for x,y in locations):continue
        # A 3x3 high-pass centroid preserves the subpixel center of the glow.
        xa,xb=max(0,px-1),min(base.shape[1],px+2);ya,yb=max(0,py-1),min(base.shape[0],py+2)
        yy,xx=np.mgrid[ya:yb,xa:xb];weight=np.maximum(detail[ya:yb,xa:xb],0)
        if weight.sum():
            x=float((xx*weight).sum()/weight.sum());y=float((yy*weight).sum()/weight.sum())
        else:x,y=float(px),float(py)
        x0,x1=max(0,px-10),min(base.shape[1],px+11);y0,y1=max(0,py-10),min(base.shape[0],py+11)
        yy,xx=np.mgrid[y0:y1,x0:x1]
        templates=[]
        for shape in shapes:
            sx,sy=shape['sx'],shape['sy']
            if shape.get('kind')=='diamond':
                template=np.zeros(xx.shape)
                for ox in [-.375,-.125,.125,.375]:
                    for oy in [-.375,-.125,.125,.375]:
                        template+=np.maximum(0.,1-np.abs(xx+ox-x)/sx-np.abs(yy+oy-y)/sy)**shape['power']
                template/=16
            else:template=np.exp(-.5*(((xx-x)/sx)**2+((yy-y)/sy)**2))
            templates.append(template.ravel())
        matrix=np.stack(templates)
        # Nearby stars must not bias this emitter's halo or streak amplitude.
        fitting_mask=np.ones(xx.shape,bool)
        neighbours=((xs-x)**2+(ys-y)**2<500)&((xs-px)**2+(ys-py)**2>2)
        current_distance=(xx-x)**2+(yy-y)**2
        for nx,ny in zip(xs[neighbours],ys[neighbours]):
            fitting_mask &= current_distance <= (xx-nx)**2+(yy-ny)**2
        matrix_fit=matrix[:,fitting_mask.ravel()]
        target=(residual-emission)[y0:y1,x0:x1].reshape(-1,3)
        gram=matrix_fit@matrix_fit.T+np.eye(len(shapes))*.015
        weights=np.stack([nonnegative_least_squares(gram,matrix_fit@target[fitting_mask.ravel(),c],100) for c in range(3)],1)
        components=[{**shape,'rgb':w.round(5).tolist()} for shape,w in zip(shapes,weights) if w.max()>1.]
        if not components:continue
        p={'u':(x-geometry['x'])/width,'dy':y-geometry['y'],'components':components}
        paint_snow(emission,p,geometry['x'],geometry['y'],width)
        particles.append(p);locations.append((x,y))
    return particles,emission


def refine(frames,output):
    parameters=read_json(LAB/'parameters/ice_drift.json')
    origin,shape=(560,2120),(480,860)
    analysis=ROOT/'work/subtitle-animation-calibration/motion'
    for n,reference in enumerate(decode(LAB/'references/ice_drift.mp4',(*origin,shape[1],shape[0]))):
        if n not in frames:continue
        item=parameters['frames'][n]
        if not item['visible'] or not item.get('shader',{}).get('lighting_grid'):continue
        geometry=item['geometry'];text=REFERENCE_TEXT[:min(4,n//7+1)]
        distance=signed_distance(glyph_alpha(text,geometry,shape))
        base=shade_light(distance,item['shader'])
        static=read_json(LAB/'parameters/static.json')['shader']
        settling=[0.]*len(text)
        # Once the flash passes a glyph, restore its original opaque face/stroke.
        # Calibration selects only a scalar opacity, never a reference glyph image.
        old_score=foreground_ssim(reference,np.clip(np.rint(base),0,255).astype(np.uint8))
        for i in range(len(text)):
            best=old_score,0.,base
            for weight in [.25,.5,.75,1.]:
                weights=[0.]*len(text);weights[i]=weight
                proposed=settle_scene(base,text,geometry,static,weights)
                score=foreground_ssim(reference,np.clip(np.rint(proposed),0,255).astype(np.uint8))
                if score>best[0]:best=score,weight,proposed
            old_score,settling[i],base=best
        particles,emission=stars(reference,base,distance,geometry)
        best=None
        for gain in [.4,.6,.8,1.]:
            got=np.clip(np.rint(base+emission*gain),0,255).astype(np.uint8)
            score=foreground_ssim(reference,got)
            if best is None or score>best[0]:best=score,gain,got
        score,gain,got=best
        print(f'star snow {n:02}: {score:.6f} count={len(particles)} gain={gain}',flush=True)
        if score>parameters['calibration_scores'][n]:
            for p in particles:
                for c in p['components']:c['rgb']=(np.array(c['rgb'])*gain).round(5).tolist()
            parameters['frames'][n]={**item,'particles':particles,'settle_weights':settling}
            parameters['calibration_scores'][n]=score
        parameters['calibration_entry_mean']=float(np.mean(parameters['calibration_scores']))
        write_json(output,parameters)
        cv2.imwrite(str(analysis/f'ice-stars-{n:02}.png'),cv2.cvtColor(np.hstack([reference,got]),cv2.COLOR_RGB2BGR))
    return parameters


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--frames',default=','.join(str(n) for n in range(1,30)));parser.add_argument('--output',type=Path,default=ROOT/'work/subtitle-animation-calibration/ice-stars.json')
    args=parser.parse_args();cv2.setNumThreads(2);refine(set(map(int,args.frames.split(','))),args.output)
