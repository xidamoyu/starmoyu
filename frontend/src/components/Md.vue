<script setup lang="ts">
/** Markdown 渲染：markdown-it 解析 + DOMPurify 消毒。修原始语法直出问题。 */
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const props = withDefaults(defineProps<{ text: string }>(), { text: '' })

const md = new MarkdownIt({
  html: false,          // 不裸渲染 HTML（安全）
  linkify: true,
  breaks: true,         // 单换行即 <br>，贴合聊天
})

const html = computed(() => DOMPurify.sanitize(md.render(props.text || '')))
</script>

<template>
  <!-- 助手消息内容物：穿透气泡宽度限制，表格/卡片按内容决定宽度（网格破坏签名） -->
  <div class="md" v-html="html" />
</template>

<style scoped>
.md { line-height: 1.6; font-size: 14px; color: var(--ink); overflow-wrap: anywhere; }
.md :deep(> *:first-child) { margin-top: 0; }
.md :deep(> *:last-child) { margin-bottom: 0; }

.md :deep(h1), .md :deep(h2), .md :deep(h3), .md :deep(h4) {
  margin: 14px 0 8px; line-height: 1.3; font-weight: 700; color: var(--ink);
}
.md :deep(h1) { font-size: 18px; } .md :deep(h2) { font-size: 16px; }
.md :deep(h3) { font-size: 15px; } .md :deep(h4) { font-size: 14px; }
.md :deep(p) { margin: 8px 0; }
.md :deep(ul), .md :deep(ol) { margin: 8px 0; padding-left: 22px; }
.md :deep(li) { margin: 3px 0; }
.md :deep(strong) { font-weight: 700; color: var(--ink); }
.md :deep(a) { color: var(--brand); text-decoration: none; }
.md :deep(a:hover) { text-decoration: underline; }
.md :deep(hr) { border: none; border-top: 1px solid var(--line); margin: 12px 0; }
.md :deep(blockquote) {
  margin: 10px 0; padding: 2px 12px; color: var(--ink-2);
  border-left: none; background: var(--surface-2); border-radius: 0 var(--r-sm) var(--r-sm) 0;
}
.md :deep(code) {
  font-family: var(--mono); font-size: 12.5px; background: var(--surface-2);
  padding: 1px 5px; border-radius: 4px; color: var(--brand-strong);
}
.md :deep(pre) {
  background: #14232b; color: #dfe8ea; padding: 12px 14px; border-radius: var(--r-md);
  overflow-x: auto; margin: 10px 0;
}
.md :deep(pre code) { background: transparent; color: inherit; padding: 0; }

/* 表格：核心修复对象。横向滚动 + 紧凑 + 清晰线 */
.md :deep(table) {
  border-collapse: collapse; margin: 12px 0; width: max-content;
  min-width: 100%; font-size: 13px;
}
.md :deep(thead th) {
  text-align: left; font-weight: 600; color: var(--ink-2);
  background: var(--surface-2); padding: 7px 12px;
  border-bottom: 2px solid var(--line-strong); white-space: nowrap;
}
.md :deep(tbody td) { padding: 7px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
.md :deep(tbody tr:nth-child(even)) { background: #fbfaf7; }
.md :deep(tbody tr:hover) { background: var(--brand-softer); }
</style>
