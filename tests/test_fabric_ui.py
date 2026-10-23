"""Exercise the shipping JSX: destination direction, nulls and reported interface facts."""
from pathlib import Path
import shutil
import subprocess


def test_native_pair_directions_and_missing_interface_readings():
    root = Path(__file__).resolve().parents[1]
    script = r'''
import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const entry=`
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {FabricSnapshot} from './client/src/components/FabricPairs.jsx';
import {AppContext} from './client/src/context.js';
import {makeT} from './client/src/i18n.js';
export function render(data,lang){
 return renderToStaticMarkup(<AppContext.Provider value={{t:makeT(lang),lang,nodeName:id=>id}}><FabricSnapshot data={data} queriedAt={1000} nodes={[{id:'a',name:'Demo A'},{id:'b',name:'Demo B'},{id:'c',name:'Demo C'}]}/></AppContext.Provider>);
}`;
const built=buildSync({stdin:{contents:entry,resolveDir:process.cwd(),loader:'jsx'},bundle:true,write:false,platform:'node',format:'cjs',external:['react','react-dom','react-dom/server']});
const module={exports:{}};
new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
const data={pairs:[{a:'a',b:'b',ip_b_from_a:'192.0.2.12',ip_a_from_b:'192.0.2.11'},{a:'a',b:'c',ip_b_from_a:null,ip_a_from_b:null}],links:{a:{cx7:{up:false,speed_mbps:0,mtu:9000,v4:['192.0.2.11/24']}},c:{}}};
const original=JSON.stringify(data);
for(const lang of ['es','en']){
 const html=module.exports.render(data,lang);
 const directions=[...html.matchAll(/<div class="fabric-direction">([\s\S]*?)(?=<div class="fabric-direction">|<\/li>)/g)].map(match=>match[1]);
 assert.equal(directions.length,4);
 assert.match(directions[0],/Demo B.*Demo A/);assert.match(directions[0],/192\.0\.2\.12/);
 assert.doesNotMatch(directions[0],/192\.0\.2\.11/);
 assert.match(directions[1],/Demo A.*Demo B/);assert.match(directions[1],/192\.0\.2\.11/);
 assert.equal((html.match(/aria-label="(?:Copiar|Copy) /g)||[]).length,2);
 assert.match(directions[2],lang==='es'?/Sin dirección detectada/:/No address detected/);
 assert.match(html,/0 Mb\/s/);assert.match(html,/192\.0\.2\.11\/24/);
 assert.match(html,lang==='es'?/Desconectado/:/Down/);
 assert.match(html,lang==='es'?/Sin lectura de interfaces/:/No interface reading/);
 assert.match(html,lang==='es'?/no contiene interfaces CX7/:/contains no CX7 interfaces/);
 assert.equal(JSON.stringify(data),original);
}
'''
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
