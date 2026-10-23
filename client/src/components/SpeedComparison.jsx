import React, { useEffect, useRef, useState } from 'react';
import { useApp } from '../context.js';
import { clock, latency, num } from '../format.js';
import { concurrencyLevels, speedComparison } from '../speed-comparison.js';
import { useContextProvider } from './hooks.js';

export default function SpeedComparison({ cards, recipe }) {
  const { t, lang } = useApp();
  const [baseId, setBaseId] = useState(cards[1]?.id);
  const [selectedId, setSelectedId] = useState(cards[0]?.id);
  const [level, setLevel] = useState('');
  const panel = useRef(null);
  const heading = useRef(null);
  useEffect(() => {
    panel.current?.scrollIntoView({ block: 'start' });
    heading.current?.focus({ preventScroll: true });
  }, []);
  const selected = cards.find(card => card.id === selectedId) || cards[0];
  const base = cards.find(card => card.id === baseId && card.id !== selected?.id) || cards.find(card => card.id !== selected?.id);
  const levels = concurrencyLevels(base, selected);
  const n = levels.includes(Number(level)) ? Number(level) : levels[0];
  const comparison = speedComparison(base, selected, n);
  const differences = comparison.conditions.filter(condition => condition.changed);
  useContextProvider(() => base && selected ? { kind: 'speed-comparison', object_id: recipe, dirty: false,
    fields: { recipe, base_card_id: base.id, selected_card_id: selected.id, concurrency: n,
      base: { t: base.t, model: base.model, settings: base.settings },
      selected: { t: selected.t, model: selected.model, settings: selected.settings } } } : null);
  if (!base || !selected) return <p className="help sc-comparison">{t('sc_cmp_need_two')}</p>;

  const label = card => `${clock(card.t, lang)} · ${card.model || t('sc_cmp_unknown')}`;
  const value = (reading, unit) => reading === null ? '—' : unit === 'latency' ? latency(reading, lang)
    : unit === 'rate' ? `${num(reading, 1, lang)} ${t('sc_tps')}` : num(reading, 0, lang);
  const conditionValue = reading => reading === undefined || reading === null || reading === '' ? t('sc_cmp_unknown')
    : typeof reading === 'boolean' ? t(reading ? 'sc_cmp_yes' : 'sc_cmp_no')
    : reading === 'usage' ? t('sc_cmp_usage') : reading === 'deltas' ? t('sc_cmp_deltas') : String(reading);

  return <section className="sc-comparison" aria-label={t('sc_cmp_title')} ref={panel}>
    <h3 ref={heading} tabIndex={-1}>{t('sc_cmp_title')}</h3>
    <p className="help">{t('sc_cmp_help')}</p>
    <div className="sc-cmp-pickers">
      <label>{t('sc_cmp_base')}<select className="field" value={base.id} onChange={event => setBaseId(event.target.value)}>
        {cards.map(card => <option key={card.id} value={card.id} disabled={card.id === selected.id}>{label(card)}</option>)}
      </select></label>
      <label>{t('sc_cmp_selected')}<select className="field" value={selected.id} onChange={event => setSelectedId(event.target.value)}>
        {cards.map(card => <option key={card.id} value={card.id} disabled={card.id === base.id}>{label(card)}</option>)}
      </select></label>
    </div>
    <div className="sc-cmp-toolbar">
      <label>{t('sc_cmp_concurrency')}<select className="field field-sm" value={n ?? ''} onChange={event => setLevel(event.target.value)}>
        {levels.map(count => <option value={count} key={count}>{t('sc_cmp_streams', { n: count })}</option>)}
      </select></label>
      <button type="button" className="btn btn-sm" onClick={() => {setBaseId(selected.id); setSelectedId(base.id);}}>{t('sc_cmp_swap')}</button>
    </div>
    {!comparison.bothLevels && <p className="help sc-cmp-notice" role="status">{t('sc_cmp_missing_level', { n })}</p>}
    <table className="tbl sc-cmp-table">
      <caption>{t('sc_cmp_direction')}</caption>
      <thead><tr><th>{t('sc_col_n')}</th><th>{t('sc_cmp_metric')}</th><th className="r">{t('sc_cmp_base')}</th><th className="r">{t('sc_cmp_selected')}</th><th className="r">{t('sc_cmp_change')}</th></tr></thead>
      <tbody>{comparison.rows.map(row => <tr key={row.key}>
        <td className="num sc-cmp-n" data-label={t('sc_cmp_concurrency')}>{row.n}</td>
        <th scope="row">{t(row.label)}</th>
        <td className="num r" data-label={t('sc_cmp_base')}>{value(row.base, row.unit)}</td>
        <td className="num r" data-label={t('sc_cmp_selected')}>{value(row.selected, row.unit)}</td>
        <td className={`num r sc-cmp-change sc-cmp-${row.tone}`} data-label={t('sc_cmp_change')}>
          {row.delta === null ? '—' : <>
            <span>{row.delta > 0 ? '+' : row.delta < 0 ? '−' : ''}{value(Math.abs(row.delta), row.unit)}</span>
            <small>{row.percent === null ? t('sc_cmp_zero_base') : `${row.percent > 0 ? '+' : ''}${num(row.percent, 1, lang)} %`}</small>
          </>}
        </td>
      </tr>)}</tbody>
    </table>
    <details className="sc-cmp-conditions" open={differences.length > 0}>
      <summary>{t('sc_cmp_conditions')}{differences.length ? ` · ${t('sc_cmp_different')}` : ` · ${t('sc_cmp_same')}`}</summary>
      <dl>{comparison.conditions.map(condition => <div key={condition.key} className={condition.changed ? 'is-different' : ''}>
        <dt>{t(`sc_cmp_setting_${condition.key}`)}</dt>
        <dd><span>{t('sc_cmp_base')}: </span>{conditionValue(condition.base)}</dd>
        <dd><span>{t('sc_cmp_selected')}: </span>{conditionValue(condition.selected)}</dd>
      </div>)}</dl>
    </details>
    <p className="help">{t('sc_cmp_limit')}</p>
  </section>;
}
