"""Losslessly compact numerical shader tables; no bitmap assets are generated."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from common import LAB,read_json,write_json,sha


def compact():
    path=LAB.parents[1] / 'assets/packaging/animations/parameters/shout_wave.json';parameters=read_json(path)
    for n,item in enumerate(parameters['frames']):
        shader=item.get('shader',{})
        if 'rgb' not in shader:continue
        filename=f'shout-surface-{n:02}.npz';target=path.parent/filename
        table=np.asarray(shader.pop('rgb'),np.float32)
        np.savez_compressed(target,coefficients=table)
        shader['table_file']=filename;shader['table_sha256']=sha(target)
        shader['table_shape']=list(table.shape)
    write_json(path,parameters)


if __name__=='__main__':compact()
