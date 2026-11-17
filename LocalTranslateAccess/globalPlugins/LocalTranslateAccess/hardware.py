"""Read-only hardware inventory. Shared graphics memory is never counted as VRAM."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import winreg

GIB=1024**3

class Memory(ctypes.Structure):
    _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[
        (name,ctypes.c_ulonglong) for name in ('total','available','totalPage','availablePage','totalVirtual','availableVirtual','extended')]

class Guid(ctypes.Structure):
    _fields_=[('a',wintypes.DWORD),('b',wintypes.WORD),('c',wintypes.WORD),('d',ctypes.c_ubyte*8)]

class Desc(ctypes.Structure):
    _fields_=[('name',wintypes.WCHAR*128),('vendor',wintypes.UINT),('device',wintypes.UINT),
        ('subsystem',wintypes.UINT),('revision',wintypes.UINT),('dedicated',ctypes.c_size_t),
        ('system',ctypes.c_size_t),('shared',ctypes.c_size_t),('luidLow',wintypes.DWORD),
        ('luidHigh',wintypes.LONG),('flags',wintypes.UINT)]

def method(pointer,index,restype,*args):
    table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype,ctypes.c_void_p,*args)(table[index])

def graphics():
    result=[]
    factory=ctypes.c_void_p()
    guid=Guid(0x770aae78,0xf26f,0x4dba,(ctypes.c_ubyte*8)(0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87))
    dll=ctypes.WinDLL('dxgi')
    dll.CreateDXGIFactory1.argtypes=[ctypes.POINTER(Guid),ctypes.POINTER(ctypes.c_void_p)]
    dll.CreateDXGIFactory1.restype=ctypes.c_long
    if dll.CreateDXGIFactory1(ctypes.byref(guid),ctypes.byref(factory))<0:return result
    try:
        enumerate_=method(factory,12,ctypes.c_long,wintypes.UINT,ctypes.POINTER(ctypes.c_void_p))
        for index in range(32):
            adapter=ctypes.c_void_p()
            if enumerate_(factory,index,ctypes.byref(adapter))<0:break
            try:
                desc=Desc()
                if method(adapter,10,ctypes.c_long,ctypes.POINTER(Desc))(adapter,ctypes.byref(desc))>=0 and not desc.flags&2:
                    result.append(dict(name=desc.name,vendor=desc.vendor,vram_total=desc.dedicated,
                                       shared=desc.shared,vram_free=None))
            finally:method(adapter,2,wintypes.ULONG)(adapter)
    finally:method(factory,2,wintypes.ULONG)(factory)
    try:
        text=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total,memory.free,driver_version',
            '--format=csv,noheader,nounits'],text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW,stderr=subprocess.DEVNULL)
        nvidia=[x for x in result if x['vendor']==0x10de]
        for index,line in enumerate(text.splitlines()):
            name,total,free,driver=[x.strip() for x in line.split(',')]
            gpu=nvidia[index] if index<len(nvidia) else dict(name=name,vendor=0x10de,shared=0)
            gpu.update(vram_total=int(total)*1024**2,vram_free=int(free)*1024**2,driver=driver,cuda_index=index)
            if gpu not in result:result.append(gpu)
    except (OSError,ValueError,subprocess.SubprocessError):pass
    return result

def inspect():
    result=dict(cpu='Processeur inconnu',cores=os.cpu_count(),ram_total=None,ram_available=None,gpus=[])
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            result['cpu']=winreg.QueryValueEx(key,'ProcessorNameString')[0].strip()
    except OSError:pass
    kernel=ctypes.WinDLL('kernel32')
    memory=Memory();memory.length=ctypes.sizeof(memory)
    if kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
        result.update(ram_total=memory.total,ram_available=memory.available)
    result['sse41']=bool(kernel.IsProcessorFeaturePresent(37))
    result['avx2']=bool(kernel.IsProcessorFeaturePresent(40))
    try:result['gpus']=graphics()
    except (OSError,ValueError,ctypes.ArgumentError):pass
    return result

def recommend(info):
    # Provisional heuristics. Local performance tests can override this preference.
    available=info.get('ram_available')
    if info.get('sse41') is False:return 'bergamot'
    if available is not None and available<GIB:return 'bergamot'
    return 'bergamot'

def gpu_candidate(info):
    for gpu in info.get('gpus',[]):
        if gpu.get('vendor')==0x10de and gpu.get('vram_total',0)>=12*GIB:
            if gpu.get('vram_free') is None or gpu['vram_free']>=11*GIB:
                return gpu
    return None

def cuda_inventory():
    """Match CUDA device indices to PCI bus IDs, independent of DXGI/SMI ordering."""
    result=[]
    try:
        driver=ctypes.WinDLL('nvcuda');version=ctypes.c_int();count=ctypes.c_int()
        if driver.cuInit(0) or driver.cuDriverGetVersion(ctypes.byref(version)) or driver.cuDeviceGetCount(ctypes.byref(count)):return result
        for index in range(count.value):
            major=ctypes.c_int();minor=ctypes.c_int();bus=ctypes.create_string_buffer(32)
            if driver.cuDeviceGetAttribute(ctypes.byref(major),75,index) or driver.cuDeviceGetAttribute(ctypes.byref(minor),76,index):continue
            if driver.cuDeviceGetPCIBusId(bus,len(bus),index):continue
            result.append(dict(cuda_index=index,compute_capability=(major.value,minor.value),cuda_driver=version.value,pci_bus=bus.value.decode('ascii').lower()))
    except (OSError,AttributeError,ValueError):pass
    return result

def gpu_inventory(info):
    """A positive match needs dedicated memory, a working driver and known free VRAM."""
    try:
        text=subprocess.check_output(['nvidia-smi','--query-gpu=pci.bus_id,memory.total,memory.free','--format=csv,noheader,nounits'],
            text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW,stderr=subprocess.DEVNULL)
        memory={line.split(',')[0].strip().lower()[-12:]:(int(line.split(',')[1])*1024**2,int(line.split(',')[2])*1024**2) for line in text.splitlines()}
        devices=[]
        for gpu in cuda_inventory():
            match=memory.get(gpu['pci_bus'][-12:])
            if match:
                gpu.update(vendor=0x10de,vram_total=match[0],vram_free=match[1]);devices.append(gpu)
        info['cuda_devices']=devices
    except (OSError,ValueError,subprocess.SubprocessError):info['cuda_devices']=[]
    return info
