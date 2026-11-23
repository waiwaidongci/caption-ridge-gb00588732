import {feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

function planResult(plan, version) {
  const result = plan.result;
  const outdated = plan.input_version !== null && plan.input_version !== version &&
    p(`术语库已更新（版本 ${plan.input_version} → ${version}），此结果基于旧覆盖范围，建议重新计算。`, 'feedback');
  return el('section', {class:'analysis', 'data-testid':'glossary-complete'},
    el('div', {class:'result-head'}, h(2, result.headline), p(result.note, 'intro-copy'),
      el('div', {class:'actions'}, pill(STATUS[plan.status], plan.status)), outdated),
    metrics(result.metrics),
    (result.sections || []).map(item => panel(item.title, p(item.text))),
    result.tables.map(item => panel(item.title, table(item.columns, item.rows, {caption:item.title}))));
}

function planDetail(plan, version, refresh) {
  if (!plan) return null;
  if (plan.status === 'queued' || plan.status === 'running') {
    const original = location.hash;
    const status = el('p', {role:'status'}, '后台正在计算最优组合，完成后会自动展示。');
    const finished = item => item.status !== 'queued' && item.status !== 'running';
    poll(() => workspace.glossaryPlan(plan.id), finished, () => location.hash === original)
      .then(result => {
        if (result && status.isConnected && location.hash === original) refresh();
        else if (status.isConnected) status.textContent = '仍在执行，请点击刷新检查。';
      })
      .catch(error => { if (status.isConnected) status.textContent = error.message; });
    return panel('计算结果', pill(STATUS[plan.status], plan.status), status,
      button('刷新', {class:'quiet', onClick:refresh}));
  }
  if (plan.status === 'complete' && plan.result) return planResult(plan, version);
  return panel('计算结果', pill(STATUS[plan.status], plan.status),
    p(plan.error || '计算未完成，请重新发起。', 'feedback'));
}

export async function glossaryPage(route, refresh) {
  const {spec} = workspace;
  const guide = spec.glossary;
  const [{version, terms}, plans] = await Promise.all([workspace.glossaryTerms(), workspace.glossaryPlans()]);
  const completed = workspace.runs.filter(run => run.status === 'complete');
  const error = feedback();

  const submit = button('计算最优术语组合', {type:'submit', class:'primary', 'data-testid':'start-glossary'});
  const form = el('form', {class:'fields', 'data-testid':'glossary-form',
    onSubmit:handler(submit, error, async event => {
      const data = new FormData(event.target);
      const plan = await workspace.startGlossary({run_id:Number(data.get('run_id')), budget:Number(data.get('budget'))});
      location.hash = `#glossary/${plan.id}`;
    })},
    el('label', {class:'field', for:'plan-run'}, el('span', {}, '估算方案（已完成运行）'),
      el('select', {id:'plan-run', name:'run_id', required:true},
        completed.map(run => el('option', {value:run.id}, run.name)))),
    field({key:'budget', label:'整理预算（分钟）', type:'number', min:0.01, max:1000000, help:guide.budget_help},
      guide.budget_default),
    el('div', {class:'actions'}, submit));

  const id = route.id || plans[0]?.id;
  const plan = id ? await workspace.glossaryPlan(id) : null;
  const detail = plan ? planDetail(plan, version, refresh)
    : panel('计算结果', empty('设置预算后开始计算，所选术语与节省分钟将在此列出。'));
  const planList = table([
    {label:'计算', render:item => link(`#${item.id} · 预算 ${value(item.budget)} 分钟`, `#glossary/${item.id}`)},
    {label:'状态', render:item => pill(STATUS[item.status], item.status)},
    {label:'录入人', key:'author'},
    {label:'时间', render:item => date(item.created_at)},
  ], plans, {caption:'术语组合计算记录', empty:'还没有术语组合计算记录。'});

  let editing = null;
  const termSubmit = button('录入术语', {type:'submit', class:'primary', 'data-testid':'save-term'});
  const cancel = button('取消补报', {class:'quiet', hidden:true});
  const termForm = el('form', {class:'fields', 'data-testid':'term-form',
    onSubmit:handler(termSubmit, error, async event => {
      const data = new FormData(event.target);
      await workspace.saveTerm(editing?.id,
        {term:data.get('term'), cost:Number(data.get('cost')), coverage_text:data.get('coverage_text')});
      await refresh();
    })},
    field({key:'term', label:'术语', type:'string', max_length:120}),
    field({key:'cost', label:'整理成本（分钟）', type:'number', min:0.01, max:100000}),
    el('label', {class:'field', for:'coverage-text'}, el('span', {}, '可免核的工单位置'),
      el('textarea', {id:'coverage-text', name:'coverage_text', required:true, rows:5, maxlength:20000,
        placeholder:'QJ-N02: 1 3 4'}),
      el('small', {}, guide.coverage_help)),
    el('div', {class:'actions'}, termSubmit, cancel));
  const loadIntoForm = row => {
    editing = row;
    termForm.elements.term.value = row.term;
    termForm.elements.cost.value = row.cost;
    termForm.elements.coverage_text.value = row.coverage_text;
    termSubmit.textContent = '保存补报';
    cancel.hidden = false;
    termForm.scrollIntoView({block:'nearest'});
  };
  cancel.addEventListener('click', () => {
    editing = null;
    termForm.reset();
    termSubmit.textContent = '录入术语';
    cancel.hidden = true;
  });
  const termList = table([
    {key:'term', label:'术语'},
    {key:'cost', label:'整理成本（分钟）'},
    {key:'positions', label:'免核位置（处）'},
    {label:'更新', render:row => date(row.updated_at)},
    session.canWrite && {label:'操作', render:row => {
      const edit = button('补报', {class:'quiet'});
      edit.addEventListener('click', () => loadIntoForm(row));
      return edit;
    }},
  ].filter(Boolean), terms, {caption:'术语库'});

  const sourceRunId = plan?.run_id || completed[0]?.id;
  const sourceRun = sourceRunId ? await workspace.getRun(sourceRunId) : null;
  const queue = sourceRun?.result?.tables?.[0];
  const reference = queue && panel('待审工单参考',
    p(`来自「${sourceRun.name}」的待审队列，登记位置时请使用这些工单号。`, 'subtle'),
    table([{key:'job_id', label:'工单号'}, {key:'estimated_minutes', label:'估算分钟'}],
      queue.rows, {caption:'待审工单'}));

  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'},
      panel('术语组合计算',
        session.canWrite
          ? (completed.length ? form : empty('先完成一次工时估算运行。', '#run', '前往运行'))
          : p('只读角色可以查看术语库与计算结果。', 'empty'),
        error),
      detail,
      panel('计算记录', planList)),
    el('aside', {class:'sidebar-panel'},
      panel(`术语库（版本 ${version}）`, termList),
      session.canWrite && panel('录入 / 补报术语', termForm),
      reference,
      panel('计算口径', el('ul', {class:'method-list'}, guide.methodology.map(text => el('li', {}, text))))));
}
