"""Fit the first partially visible bounce frame without treating offscreen pixels as black."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import cv2
import numpy as np
from common import *
from renderer import text_sprite,vertical_blur,place


def refine():
    p=read_json(LAB/'parameters/bounce_up.json');item=p['frames'][4]
    sprite=text_sprite(REFERENCE_TEXT);length,center,radius=1024,352,128
    source=np.zeros((length,730,3),np.float64);source[center:center+320]=sprite.rgb
    origin=round(sprite.y+item['dy']);crop_y=origin-center
    ref=list(decode(LAB/'references/bounce_up.mp4',(590,0,730,HEIGHT)))[4]
    target=np.zeros_like(source);valid=np.zeros(length,bool)
    y0,y1=max(0,crop_y),min(HEIGHT,crop_y+length)
    target[y0-crop_y:y1-crop_y]=ref[y0:y1];valid[y0-crop_y:y1-crop_y]=True
    sf=np.fft.rfft(source,axis=0)
    def forward(k):
        impulse=np.zeros((length,3));impulse[-radius:]=k[:radius];impulse[:radius+1]=k[radius:]
        return np.fft.irfft(sf*np.fft.rfft(impulse,axis=0)[:,None,:],n=length,axis=0)
    def transpose(pixels):
        corr=np.fft.irfft((sf.conj()*np.fft.rfft(pixels*valid[:,None,None],axis=0)).sum(1),n=length,axis=0)
        return np.r_[corr[-radius:],corr[:radius+1]]
    ridge=(source*source).sum((0,1))*1e-6
    operator=lambda k:transpose(forward(k))+k*ridge
    k=np.asarray(item['kernels']).T.copy();residual=transpose(target)-operator(k);direction=residual.copy();rr=(residual*residual).sum(0)
    best=p['calibration_scores'][4],k.copy()
    for i in range(100):
        ad=operator(direction);step=rr/np.maximum((direction*ad).sum(0),1e-15)
        k+=direction*step;residual-=ad*step;nr=(residual*residual).sum(0)
        direction=residual+direction*nr/np.maximum(rr,1e-15);rr=nr
        if i%10==9:
            got=forward(k);got[~valid]=0
            score=foreground_ssim(target.astype(np.uint8),np.clip(np.rint(got),0,255).astype(np.uint8))
            print('bounce edge',i+1,score,flush=True)
            if score>best[0]:best=score,k.copy()
    if best[0]>p['calibration_scores'][4]:
        p['frames'][4]['kernels']=best[1].T.round(7).tolist();p['calibration_scores'][4]=best[0]
        p['calibration_entry_mean']=float(np.mean(p['calibration_scores']))
        write_json(LAB/'parameters/bounce_up.json',p)


if __name__=='__main__':cv2.setNumThreads(2);refine()
