import test from 'node:test';
import assert from 'node:assert/strict';
import { concurrencyLevels, speedComparison } from './src/speed-comparison.js';

const card = (model, levels, max_tokens = 256) => ({model, levels, settings: {max_tokens, rounds: 3, warmup: 1, ignore_eos: false}});
test('aligns by concurrency and separates throughput gains from lower latency and failures', () => {
  const base = card('before', [{n:8,agg_tps:100,stream_tps:12.5,ttft_p50:0.4,ttft_p95:0.8,errors:2,requests:48,tokens_source:'usage'}]);
  const next = card('after', [{n:8,agg_tps:150,stream_tps:18.75,ttft_p50:0.3,ttft_p95:1.2,errors:1,requests:96,tokens_source:'deltas'}],512);
  const result = speedComparison(base,next,8);
  assert.equal(result.bothLevels,true);
  assert.equal(result.rows[0].percent,50); assert.equal(result.rows[0].tone,'better');
  assert.ok(Math.abs(result.rows[2].percent+25)<1e-10); assert.equal(result.rows[2].tone,'better');
  assert.equal(result.rows[3].tone,'worse'); assert.equal(result.rows[4].percent,-50);
  assert.equal(result.rows[5].tone,'neutral');
  assert.deepEqual(result.conditions.filter(c=>c.changed).map(c=>c.key),['model','max_tokens','tokens_source']);
  assert.equal(result.conditions.find(c=>c.key==='ignore_eos').base,false);
});
test('zero baseline keeps the absolute difference without inventing a percentage', () => {
  const result = speedComparison(card('m',[{n:1,agg_tps:0,errors:0}]),card('m',[{n:1,agg_tps:20,errors:0}]),1);
  assert.equal(result.rows[0].base,0); assert.equal(result.rows[0].delta,20); assert.equal(result.rows[0].percent,null);
  assert.equal(result.rows[4].delta,0); assert.equal(result.rows[4].percent,null);
});
test('missing concurrency and missing metrics stay unknown, while a measured zero stays zero', () => {
  const base = card('m',[{n:8,agg_tps:40},{n:1,agg_tps:10}]);
  const next = card('m',[{n:16,agg_tps:0,stream_tps:null},{n:8,agg_tps:null,ttft_p50:Infinity}]);
  assert.deepEqual(concurrencyLevels(base,next),[1,8,16]);
  const sixteen = speedComparison(base,next,16);
  assert.equal(sixteen.bothLevels,false); assert.equal(sixteen.rows[0].base,null); assert.equal(sixteen.rows[0].selected,0); assert.equal(sixteen.rows[0].delta,null);
  const eight = speedComparison(base,next,8);
  assert.equal(eight.rows[0].delta,null); assert.equal(eight.rows[2].selected,null);
});
