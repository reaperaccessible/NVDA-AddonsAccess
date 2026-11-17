import json,os,queue,subprocess,threading
from pathlib import Path
BASE=Path(__file__).resolve().parent
class BergamotBackend:
    def __init__(self,model_path,executable,source,target):
        self.model_path=Path(model_path)
        self.source,self.target=source,target
        self.executable=Path(executable)
        self.lock = threading.RLock()
        self.closed = False
        self.process = None
        self.lid = None
        self.ready = False
        self.sequence = 0
        self.replies = queue.Queue()

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    self.replies.put(json.loads(line))
                except (ValueError,TypeError):
                    continue
        except (OSError, ValueError):
            pass
        finally:
            self.replies.put({'error':'Translation worker stopped'})

    def _start(self):
        if self.closed:
            raise RuntimeError('Translation engine stopped')
        if self.process is not None:
            if self.process.poll() is not None:
                raise RuntimeError('Translation worker stopped; toggle translation to restart')
            return
        env = dict(os.environ)
        for key in ('NODE_OPTIONS','NODE_PATH'):
            env.pop(key,None)
        process = subprocess.Popen([str(self.executable),str(BASE/'bridge.mjs'),str(self.model_path),self.source,self.target],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',bufsize=1,env=env,
            creationflags=subprocess.CREATE_NO_WINDOW, cwd=str(BASE))
        self.process = process
        if self.closed:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError('Translation engine stopped')
        threading.Thread(target=self._read,args=(process,),daemon=True).start()

    def _call(self, op, **fields):
        self._start()
        self.sequence += 1
        request = {'id':self.sequence,'op':op,**fields}
        self.process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n')
        self.process.stdin.flush()
        while True:
            try:
                reply = self.replies.get(timeout=60)
            except queue.Empty:
                self.close()
                raise RuntimeError('Translation timeout')
            if reply.get('id') not in (None, self.sequence):
                continue
            if 'error' in reply:
                raise RuntimeError(reply['error'])
            return reply['result']

    def warmup(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('Translation engine stopped')
            if not self.ready:
                self._call('warmup')
                self.ready = True

    def translate(self,text,src,tgt):
        with self.lock:
            self.warmup()
            return self._call('translate',text=text)

    def close(self):
        # Interrupt an in-flight translation before acquiring the worker lock.
        self.closed = True
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        with self.lock:
            if process is not None:
                process.wait(timeout=5)
                for stream in (process.stdin,process.stdout):
                    if stream is not None:
                        stream.close()
            self.lid = None
            self.ready = False
