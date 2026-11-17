import {join} from 'node:path';
import {readFile} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {LatencyOptimisedTranslator, TranslatorBacking} from './bergamot/translator.js';

class LocalBacking extends TranslatorBacking {
  async loadModelRegistery() { return [{from:process.argv[3],to:process.argv[4]}]; }
  async loadTranslationModel() {
    const load = async name => {
      const bytes = await readFile(join(process.argv[2],name));
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset+bytes.byteLength);
    };
    const [model,shortlist,vocab] = await Promise.all(
      ['model.bin','lexicalShortlist.bin','vocab.bin'].map(load));
    return {model,shortlist,vocabs:[vocab,vocab],config:{'beam-size':1}};
  }
}
// No remote model registry or model downloads: all buffers come from this addon.
const options = {cacheSize:0,useNativeIntGemm:false,pivotLanguage:null};
const translator = new LatencyOptimisedTranslator(options,new LocalBacking(options));
const send = data => process.stdout.write(JSON.stringify(data)+'\n');
const lines = createInterface({input:process.stdin,crlfDelay:Infinity});
try {
  for await (const line of lines) {
    let request;
    try {
      request = JSON.parse(line);
      if (request.op === 'warmup') {
        await translator.translate({from:process.argv[3],to:process.argv[4],text:'Hello.',html:false});
        send({id:request.id,result:true});
      } else if (request.op === 'translate' && typeof request.text === 'string') {
        const result = await translator.translate({from:process.argv[3],to:process.argv[4],text:request.text,html:false});
        send({id:request.id,result:result.target.text});
      } else throw new Error('Unsupported request');
    } catch (error) { send({id:request?.id,error:String(error.message || error)}); }
  }
} finally { await translator.delete(); }
