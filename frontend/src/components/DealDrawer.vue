<script setup lang="ts">
/** M6 商单路线图抽屉：stage 时间线 + 跟进流水 + 改阶段 + 跟进备注 + 结案表单。
 *  数据源 GET /api/deals/{id}/timeline。截图抽取能力置灰（视觉未验证）。 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  dealTimeline, setDealStage, addFollowup, closeDeal, sedimentPreview, sedimentConfirm,
  type Timeline,
} from '../api'

const props = defineProps<{ dealId: string | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const open = computed(() => !!props.dealId)
const tl = ref<Timeline | null>(null)
const loading = ref(false)

// 改阶段
const stageDlg = ref(false)
const newStage = ref('')
const stageNote = ref('')
const stageSaving = ref(false)
// 跟进备注
const noteDlg = ref(false)
const noteText = ref('')
const noteSaving = ref(false)
// 结案表单
const closeDlg = ref(false)
const closeForm = ref({ roi: null as number | null, gmv: null as number | null,
  exposure: null as number | null, interaction: null as number | null,
  cpm: null as number | null, views: null as number | null, summary_note: '' })
const closeSaving = ref(false)
const shotPreview = ref<string | null>(null)   // 截图附件预览
const shotDataUrl = ref<string | null>(null)

async function load() {
  if (!props.dealId) return
  loading.value = true
  try {
    tl.value = await dealTimeline(props.dealId)
  } catch {
    tl.value = null
  } finally {
    loading.value = false
  }
}
watch(() => props.dealId, load, { immediate: true })

const currentIdx = computed(() =>
  tl.value ? tl.value.stages.indexOf(tl.value.stage) : -1)
const fmtMoney = (n: unknown) =>
  typeof n === 'number' ? '¥' + n.toLocaleString() : '-'

function openStage() { newStage.value = tl.value?.stage ?? ''; stageNote.value = ''; stageDlg.value = true }
async function saveStage() {
  if (!props.dealId || !newStage.value) return
  stageSaving.value = true
  try {
    await setDealStage(props.dealId, newStage.value, stageNote.value)
    ElMessage.success(`阶段已更新为「${newStage.value}」`)
    stageDlg.value = false
    await load(); emit('changed')
  } finally { stageSaving.value = false }
}

function openNote() { noteText.value = ''; noteDlg.value = true }
async function saveNote() {
  if (!props.dealId || !noteText.value.trim()) return
  noteSaving.value = true
  try {
    await addFollowup(props.dealId, noteText.value.trim())
    ElMessage.success('跟进已记录')
    noteDlg.value = false
    await load(); emit('changed')
  } finally { noteSaving.value = false }
}

function openClose() {
  closeForm.value = { roi: null, gmv: null, exposure: null, interaction: null,
    cpm: null, views: null, summary_note: '' }
  shotPreview.value = null; shotDataUrl.value = null
  closeDlg.value = true
}
function onShot(f: File) {
  const reader = new FileReader()
  reader.onload = () => { shotPreview.value = String(reader.result); shotDataUrl.value = String(reader.result) }
  reader.readAsDataURL(f)
}
async function saveClose() {
  if (!props.dealId) return
  const f = closeForm.value
  if ([f.roi, f.gmv, f.exposure, f.interaction, f.cpm, f.views].every((v) => v == null)) {
    ElMessage.warning('至少填写一个效果指标'); return
  }
  closeSaving.value = true
  try {
    // 视觉抽取能力未验证可用：截图仅作为 traits 留档 source_quote（诚实降级，不假装抽取）。
    // 能力就绪后接 /vision/enabled + 抽取 UI。
    const traits = shotDataUrl.value
      ? [{ kol_id: (tl.value?.kol_ids?.[0] ?? ''), trait_category: '其他',
           trait_content: '（聊天记录截图，待视觉能力启用后抽取）',
           source_quote: shotDataUrl.value.slice(0, 200), severity: 'info' }]
      : []
    await closeDeal(props.dealId, {
      roi: f.roi, gmv: f.gmv, exposure: f.exposure, interaction: f.interaction,
      cpm: f.cpm, views: f.views, summary_note: f.summary_note, traits,
    })
    ElMessage.success('已结案归档')
    closeDlg.value = false
    await load(); emit('changed')
    // 结案沉淀流：主动提醒 → 预览 → 确认入库（两次确认）
    try {
      await ElMessageBox.confirm('本单已结案。建议沉淀成案例文档进知识库（会自动提炼跟进流水里的复盘经验），先出预览吗？',
        '沉淀提醒', { confirmButtonText: '出预览', cancelButtonText: '暂不', type: 'info' })
      const pv = await sedimentPreview(props.dealId ?? '')
      await ElMessageBox.alert(
        `<pre style="white-space:pre-wrap;max-height:50vh;overflow:auto;font-size:12px">${pv.preview.replace(/</g,'&lt;')}</pre>`,
        pv.title, { dangerouslyUseHTMLString: true, confirmButtonText: '确认入库',
                    distinguishCancelAndClose: true, cancelButtonText: '补充说明后入库' })
        .then(() => sedimentConfirm(props.dealId ?? ''))
        .then(r => ElMessage.success(`已入库：${r.sediment.chunks} 块（替换旧 ${r.sediment.replaced_old}），知识库即时可检索`))
        .catch(a => { if (a === 'cancel') ElMessage.info('可在对话里让助手补充经验要点后沉淀'); })
    } catch { /* 用户选暂不 */ }

  } finally { closeSaving.value = false }
}
</script>

<template>
  <el-drawer :model-value="open" size="440px" :with-header="false"
             @close="emit('close')" class="deal-drawer">
    <div v-if="tl" class="panel" v-loading="loading">
      <!-- 头部：商单号 + 阶段 -->
      <div class="head">
        <div class="deal-id">{{ tl.deal_id }}</div>
        <div class="brand">{{ tl.brand_name }} · {{ tl.category }}</div>
        <div class="meta">
          <span class="chip">{{ tl.goal }}</span>
          <span class="chip money tabular">{{ fmtMoney(tl.budget) }}</span>
          <span class="chip">{{ tl.start_date }} ~ {{ tl.end_date }}</span>
        </div>
        <div class="actions">
          <el-button size="small" @click="openNote">跟进备注</el-button>
          <el-button size="small" @click="openStage">修改阶段</el-button>
          <el-button size="small" type="primary" :disabled="tl.stage === '结案'"
                     @click="openClose">结案归档</el-button>
        </div>
      </div>

      <!-- stage 时间线 -->
      <div class="tl">
        <div v-for="(s, i) in tl.stages" :key="s" class="node"
             :class="{ done: i < currentIdx, cur: i === currentIdx, todo: i > currentIdx }">
          <div class="dot" />
          <div class="txt">
            <div class="stage-name">{{ s }}</div>
            <div v-if="i === currentIdx" class="stage-now">当前阶段</div>
          </div>
        </div>
      </div>

      <!-- 跟进流水 -->
      <div class="sec">
        <div class="sec-title">跟进流水</div>
        <div v-if="!tl.followups.length" class="empty">暂无跟进记录</div>
        <div v-for="f in tl.followups" :key="f.id" class="log">
          <div class="log-head">
            <span class="flow">{{ f.stage_from }} → {{ f.stage_to }}</span>
            <span class="op">{{ f.operator }}</span>
          </div>
          <div class="log-note">{{ f.note }}</div>
          <div class="log-time">{{ (f.created_at || '').replace('T', ' ').slice(0, 16) }}</div>
        </div>
      </div>

      <!-- 特质溯源 -->
      <div class="sec" v-if="tl.traits.length">
        <div class="sec-title">本单沉淀特质</div>
        <div v-for="t in tl.traits" :key="t.trait_id" class="trait">
          <span class="cat">{{ t.trait_category }}</span>
          <span class="content">{{ t.trait_content }}</span>
        </div>
      </div>
    </div>
    <div v-else class="panel empty-root" v-loading="loading">
      <span v-if="!loading">商单不存在或无权限</span>
    </div>

    <!-- 改阶段弹窗 -->
    <el-dialog v-model="stageDlg" title="修改阶段" width="360px" append-to-body>
      <el-select v-model="newStage" placeholder="选择阶段" style="width:100%">
        <el-option v-for="s in tl?.stages ?? []" :key="s" :label="s" :value="s" />
      </el-select>
      <el-input v-model="stageNote" type="textarea" :rows="2" placeholder="备注（可选）"
                style="margin-top:12px" />
      <template #footer>
        <el-button @click="stageDlg = false">取消</el-button>
        <el-button type="primary" :loading="stageSaving" @click="saveStage">保存</el-button>
      </template>
    </el-dialog>

    <!-- 跟进备注弹窗 -->
    <el-dialog v-model="noteDlg" title="跟进备注" width="360px" append-to-body>
      <el-input v-model="noteText" type="textarea" :rows="3" placeholder="随手一句话记录进展…" />
      <template #footer>
        <el-button @click="noteDlg = false">取消</el-button>
        <el-button type="primary" :loading="noteSaving" @click="saveNote">保存</el-button>
      </template>
    </el-dialog>

    <!-- 结案表单弹窗 -->
    <el-dialog v-model="closeDlg" title="结案归档" width="460px" append-to-body>
      <div class="close-form">
        <div class="grid">
          <label>ROI<input v-model.number="closeForm.roi" type="number" step="0.01" placeholder="2.3" /></label>
          <label>GMV<input v-model.number="closeForm.gmv" type="number" placeholder="180000" /></label>
          <label>曝光<input v-model.number="closeForm.exposure" type="number" placeholder="4200000" /></label>
          <label>互动<input v-model.number="closeForm.interaction" type="number" placeholder="65000" /></label>
          <label>CPM<input v-model.number="closeForm.cpm" type="number" placeholder="120" /></label>
          <label>播放<input v-model.number="closeForm.views" type="number" placeholder="500000" /></label>
        </div>
        <label class="block">经验总结（随手一句话）
          <textarea v-model="closeForm.summary_note" rows="2"
                    placeholder="这次达人配合度如何、报价口径、有什么坑…"></textarea>
        </label>
        <div class="shot">
          <div class="shot-label">聊天记录截图（可选）</div>
          <input type="file" accept="image/*" @change="(e:any) => e.target.files?.[0] && onShot(e.target.files[0])" />
          <img v-if="shotPreview" :src="shotPreview" class="shot-preview" alt="截图预览" />
          <div class="shot-note">视觉抽取能力未启用，截图先作为附件留档；能力就绪后自动抽取特质供勾选。</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="closeDlg = false">取消</el-button>
        <el-button type="primary" :loading="closeSaving" @click="saveClose">确认结案</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.panel { padding: 4px 2px; }
.head { border-bottom: 1px solid var(--line); padding-bottom: 14px; }
.deal-id { font-family: var(--mono); font-size: 13px; color: var(--ink-3); }
.brand { font-size: 18px; font-weight: 700; margin: 4px 0 8px; }
.meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.chip { font-size: 12px; padding: 3px 9px; background: var(--surface-2); border-radius: 20px; color: var(--ink-2); }
.money { color: var(--brand-strong); font-weight: 600; }
.actions { display: flex; gap: 8px; }

.tl { display: flex; flex-direction: column; margin: 18px 0; }
.node { display: flex; gap: 12px; position: relative; padding: 7px 0 7px 4px; }
.node:not(:last-child)::before {
  content: ''; position: absolute; left: 9px; top: 22px; bottom: -8px;
  width: 2px; background: var(--line);
}
.node.done::before { background: var(--brand); }
.dot { width: 12px; height: 12px; border-radius: 50%; margin-top: 4px; flex: none;
  background: var(--line-strong); }
.node.done .dot { background: var(--brand); }
.node.cur .dot { background: var(--brand); box-shadow: 0 0 0 4px var(--brand-soft); }
.stage-name { font-size: 14px; font-weight: 500; color: var(--ink-3); }
.node.done .stage-name, .node.cur .stage-name { color: var(--ink); }
.node.cur .stage-name { font-weight: 700; }
.stage-now { font-size: 11px; color: var(--brand); margin-top: 1px; }

.sec { margin-top: 18px; }
.sec-title { font-size: 12px; font-weight: 700; color: var(--ink-3); text-transform: none;
  letter-spacing: .02em; margin-bottom: 8px; }
.empty, .empty-root { color: var(--ink-3); font-size: 13px; padding: 8px 0; }
.empty-root { display: flex; align-items: center; justify-content: center; height: 100%; }

.log { padding: 9px 0; border-bottom: 1px dashed var(--line); }
.log:last-child { border-bottom: none; }
.log-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 3px; }
.flow { color: var(--brand); font-weight: 600; }
.op { color: var(--ink-3); }
.log-note { font-size: 13px; color: var(--ink); }
.log-time { font-size: 11px; color: var(--ink-3); margin-top: 3px; }

.trait { display: flex; gap: 8px; font-size: 13px; padding: 6px 0; border-bottom: 1px dashed var(--line); }
.trait:last-child { border-bottom: none; }
.cat { flex: none; color: var(--brand); font-weight: 600; font-size: 12px; }
.content { color: var(--ink); }

.close-form .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 12px; }
.close-form label { font-size: 12px; color: var(--ink-2); display: flex; flex-direction: column; gap: 4px; }
.close-form input, .close-form textarea {
  font: inherit; font-size: 13px; padding: 7px 9px; border: 1px solid var(--line);
  border-radius: var(--r-sm); color: var(--ink); background: var(--surface);
}
.close-form input:focus, .close-form textarea:focus { outline: none; border-color: var(--brand); }
.close-form .block { margin-bottom: 12px; }
.shot-label { font-size: 12px; color: var(--ink-2); margin-bottom: 6px; }
.shot-preview { max-width: 100%; border-radius: var(--r-sm); border: 1px solid var(--line); margin-top: 8px; }
.shot-note { font-size: 11px; color: var(--ink-3); margin-top: 6px; }
</style>
