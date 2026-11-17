"""GPU eligibility, model-dependent memory budgets and CUDA library loading."""
from pathlib import Path
import ctypes,os,threading
from . import hardware
from .detection import libraries

FAMILIES={'argos','opus','nllb','small100','m2m100','hy'}
_lock=threading.Lock();_handles=[];_dlls=[];_loaded=None

def inspect():return hardware.gpu_inventory(hardware.inspect())

def required_memory(store,family,entries):
    if family not in FAMILIES:return None
    if family=='hy':return 11*hardware.GIB
    minimum={'nllb':2*hardware.GIB,'small100':1536*1024**2,'m2m100':2*hardware.GIB}.get(family,0)
    total=512*1024**2
    for entry in {e['id']:e for e in entries}.values():
        model=store.path(entry['id'])/'model/model.bin'
        try:weight=model.stat().st_size
        except OSError:weight=entry.get('bytes') or 512*1024**2
        total+=max(512*1024**2,int(weight*1.5))+256*1024**2
    return max(minimum,total)

def candidate(info,store,family,entries,index=None,resident=False):
    if family!='hy' and (info or {}).get('sse41') is not True:return None
    required=required_memory(store,family,entries)
    if required is None:return None
    for gpu in (info or {}).get('cuda_devices',[]):
        if index is not None and gpu.get('cuda_index')!=index:continue
        if gpu.get('vendor')!=0x10de or tuple(gpu.get('compute_capability',(0,0)))<(7,0):continue
        if gpu.get('cuda_driver',0)<(13000 if family=='hy' else 12000):continue
        if gpu.get('vram_total',0)>=required and (resident or (gpu.get('vram_free') or 0)>=required):return gpu
    return None

def create_translator(path,device='cpu',index=0,runtime=None):
    if device=='cuda':load_cuda(runtime)
    ct2,unused,unused=libraries()
    compute='int8'
    if device=='cuda':
        supported=ct2.get_supported_compute_types('cuda',index)
        if 'int8_float16' not in supported:raise RuntimeError('CUDA INT8/FP16 not supported on this device')
        compute='int8_float16'
    return ct2.Translator(str(path),device=device,device_index=index,compute_type=compute,inter_threads=1,intra_threads=2)

def load_cuda(runtime):
    global _loaded
    directory=Path(runtime).resolve()
    with _lock:
        if _loaded==directory:return
        for name in ('cublasLt64_12.dll','cublas64_12.dll','nvrtc-builtins64_129.dll','nvrtc64_120_0.dll'):
            if not (directory/name).is_file():raise FileNotFoundError('CUDA12 runtime missing: '+name)
        _handles.append(os.add_dll_directory(str(directory)))
        for name in ('cublasLt64_12.dll','cublas64_12.dll','nvrtc-builtins64_129.dll','nvrtc64_120_0.dll'):
            _dlls.append(ctypes.WinDLL(str(directory/name)))
        _loaded=directory
