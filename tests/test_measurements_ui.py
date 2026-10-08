"""The recipe panel keeps nested observations and failures readable in both languages."""
from pathlib import Path
import shutil
import subprocess


def test_nested_measurements_and_units():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    assert node, "Node.js is required for the UI checks"
    script = r'''
import assert from 'node:assert/strict';
import { measurementRows, measurementValue } from './client/src/measurements.js';
import { makeT } from './client/src/i18n.js';
const data = {prompt_tokens: 1000000, needle_pass: false, needle_depths: [.1,.5,.9],
  kv_cache_bytes: 8589934592, baseline_64k: {prompt_tokens:64000, ttft_seconds:20.64},
  decode_tokens_s_estimate:40.101, unknown_future_field:{ count:0, flags:[true,false], absent:null },
  nested_list:[{label:'preserved'}]};
for (const lang of ['es','en']) {
  const t=makeT(lang), rows=measurementRows(data,t,lang);
  assert.equal(rows.length,11);
  assert.equal(rows.find(r=>r.id==='needle_pass').value,'No');
  assert.equal(rows.find(r=>r.id==='needle_depths').value,'10 % · 50 % · 90 %');
  assert.match(rows.find(r=>r.id==='kv_cache_bytes').value,/8[,.]0 GB/);
  assert.match(rows.find(r=>r.id==='decode_tokens_s_estimate').value,/40[,.]1 tokens\/s/);
  assert.match(rows.find(r=>r.id==='baseline_64k/ttft_seconds').value,/20[,.]6 s/);
  assert.equal(rows.find(r=>r.id==='unknown_future_field/count').value,'0');
  assert.equal(rows.find(r=>r.id==='unknown_future_field/absent').value,'—');
  assert.match(rows.find(r=>r.id==='nested_list').value,/preserved/);
  assert(!rows.some(r=>r.value.includes('[object Object]')));
  assert(!rows.some(r=>r.label.includes('prompt_tokens')));
  assert.equal(measurementValue('duration_seconds', Infinity,t,lang),'—');
  assert.equal(measurementValue('empty',[],t,lang),'—');
  assert.equal(measurementValue('bool',true,t,lang),lang==='es'?'Sí':'Yes');
}
'''
    result = subprocess.run([node, "--input-type=module", "-e", script], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
