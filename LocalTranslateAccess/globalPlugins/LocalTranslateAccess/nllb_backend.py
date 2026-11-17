from .i18n import _
from pathlib import Path
from types import SimpleNamespace
import importlib.util
from .detection import libraries,nllb_libraries
from .languages import NLLB

class LocalTokenizer:
    """Small adapter using the exact saved NLLB tokenizer, without Transformers."""
    def __init__(self, backend):
        self.backend = backend
        self.src_lang = 'eng_Latn'
        self.unk_token_id = backend.token_to_id('<unk>')

    def convert_tokens_to_ids(self, tokens):
        if isinstance(tokens, str):
            value = self.backend.token_to_id(tokens)
            return self.unk_token_id if value is None else value
        return [self.convert_tokens_to_ids(t) for t in tokens]

    def convert_ids_to_tokens(self, ids):
        if isinstance(ids, int):
            return self.backend.id_to_token(ids)
        return [self.backend.id_to_token(i) for i in ids]

    def __call__(self, text, add_special_tokens=True):
        tokens = self.backend.encode(text, add_special_tokens=False).tokens
        if add_special_tokens:
            tokens = [self.src_lang] + tokens + ['</s>']
        return SimpleNamespace(input_ids=self.convert_tokens_to_ids(tokens))

    def convert_tokens_to_string(self, tokens):
        return self.backend.decoder.decode(tokens)

class NLLBBackend:
    def __init__(self,path,device='cpu',device_index=0,runtime=None):
        self.path=Path(path);self.translator=None;self.tokenizer=None;self.pipeline=None
        self.device,self.device_index,self.runtime=device,device_index,runtime
    def warmup(self):
        if self.translator is not None:return
        ct2,_unused,_unused=libraries();tokenizers=nllb_libraries()
        self.tokenizer=LocalTokenizer(tokenizers.Tokenizer.from_str((self.path/'tokenizer.json').read_text(encoding='utf-8')))
        for tag in NLLB.values():
            if self.tokenizer.convert_tokens_to_ids(tag)==self.tokenizer.unk_token_id:
                raise ValueError(_('Langue NLLB absente du tokenizer : ')+tag)
        from .gpu_support import create_translator
        self.translator=create_translator(self.path/'model',self.device,self.device_index,self.runtime)
        spec=importlib.util.spec_from_file_location(__package__+'._nllb_pipeline',Path(__file__).parent/'nllb_pipeline.py')
        self.pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.pipeline)
        self.pipeline.init_models(self.tokenizer,self.translator)
    def translate(self,text,source,target):
        if source not in NLLB or target not in NLLB:raise ValueError(_('Langue non prise en charge par NLLB.'))
        if NLLB[source]==NLLB[target]:return text
        self.warmup()
        return self.pipeline.run(text,NLLB[source],NLLB[target])
    def close(self):
        if self.translator is not None:self.translator.unload_model()
        self.translator=None;self.tokenizer=None;self.pipeline=None


