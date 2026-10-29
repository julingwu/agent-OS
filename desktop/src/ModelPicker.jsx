import React, {useState} from 'react';

export default function ModelPicker({items, value, defaultModel, onChange}) {
  const [saving, setSaving] = useState(false);
  const save = async value => {
    setSaving(true);
    try { await onChange(value); } finally { setSaving(false); }
  };
  const base = (value || '').split('#')[0];
  const models = items.filter(item => !item.id.includes('#'));
  if (base && !models.some(item => item.id === base)) models.push({id: base, label: base});
  const efforts = items.filter(item => item.id.startsWith(base + '#'));
  return <>
    <div className="kv model-row">
      <label htmlFor="agent-model">模型</label>
      <select id="agent-model" aria-label="模型" value={base} disabled={saving} onChange={e => save(e.target.value)}>
        <option value="">跟随全局默认（{defaultModel}）</option>
        {models.map(m => <option key={m.id} value={m.id}>{m.label.replace(' · 默认推理','')}</option>)}
      </select>
    </div>
    {base.startsWith('codex-cli/') && <div className="kv model-row">
      <label htmlFor="agent-reasoning">推理强度</label>
      <select id="agent-reasoning" aria-label="推理强度" value={(value || '').split('#')[1] || ''} disabled={saving}
        onChange={e => save(base + (e.target.value ? '#' + e.target.value : ''))}>
        <option value="">模型默认</option>
        {efforts.map(m => <option key={m.id} value={m.id.split('#')[1]}>
          {m.label.split(' · ').at(-1)}
        </option>)}
      </select>
    </div>}
  </>;
}
