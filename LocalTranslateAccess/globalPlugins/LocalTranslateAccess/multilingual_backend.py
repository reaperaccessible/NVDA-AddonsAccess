"""CPU INT8 M2M-100 and SMaLL-100, using SentencePiece without Transformers."""
from .i18n import _
import json
import re
from pathlib import Path
from .detection import libraries

class MultilingualBackend:
    def __init__(self,path,family,device='cpu',device_index=0,runtime=None):
        self.path=Path(path);self.family=family;self.translator=None;self.tokenizer=None
        self.device,self.device_index,self.runtime=device,device_index,runtime
    def warmup(self):
        if self.translator is not None:return
        ct2,spm,_unused=libraries()
        self.tokenizer=spm.SentencePieceProcessor()
        self.tokenizer.LoadFromSerializedProto((self.path/'sentencepiece.bpe.model').read_bytes())
        self.vocabulary=set(json.loads((self.path/'model/shared_vocabulary.json').read_text(encoding='utf-8')))
        from .gpu_support import create_translator
        self.translator=create_translator(self.path/'model',self.device,self.device_index,self.runtime)
    def translate(self,text,source,target):
        if source==target:return text
        self.warmup()
        source_tag='__'+source+'__';target_tag='__'+target+'__'
        if source_tag not in self.vocabulary or target_tag not in self.vocabulary:
            raise ValueError(_('Langue non prise en charge par ce modèle multilingue.'))
        output=[]
        for part in re.split(r'(\r\n|\n|\r)',text):
            if not part.strip():output.append(part);continue
            tokens=self.tokenizer.encode(part,out_type=str);translated=[]
            for offset in range(0,len(tokens),256):
                chunk=tokens[offset:offset+256]
                prefix=target_tag if self.family=='small100' else source_tag
                options={} if self.family=='small100' else {'target_prefix':[[target_tag]]}
                result=self.translator.translate_batch([[prefix]+chunk+['</s>']],
                    beam_size=1,num_hypotheses=1,max_input_length=0,
                    max_decoding_length=min(512,max(64,len(chunk)*3+32)),**options)[0]
                translated.extend(t for t in result.hypotheses[0]
                    if t not in ('<s>','</s>','<pad>') and not (t.startswith('__') and t.endswith('__')))
            output.append(self.tokenizer.decode(translated))
        return ''.join(output)
    def close(self):
        if self.translator is not None:self.translator.unload_model()
        self.translator=None;self.tokenizer=None;self.vocabulary=set()
