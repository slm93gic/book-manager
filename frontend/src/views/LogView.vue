<template>
  <section class="page record-page">
    <header class="page-head">
      <h1>操作日志</h1>
      <p v-if="items.length" class="stat-line"><strong>{{ items.length }}</strong> 条</p>
    </header>
    <p v-if="message" class="notice" :class="{ error: message.includes('失败') }">{{ message }}</p>
    <p v-if="ready && !items.length" class="empty">没有操作记录。</p>
    <article v-for="item in items" :key="item.id" class="panel log-card" :data-undone="item.undone ? '1' : '0'">
      <div class="panel-head">
        <h2>#{{ item.id }} {{ item.summary }}</h2>
        <span v-if="item.undone" class="status-pill" data-status="cancelled">已撤销</span>
        <span class="status-pill log-time">{{ when(item.created_at) }}</span>
      </div>
      <div class="sheet-body">
      <div class="row">
        <button class="ghost" @click="open(item.id)">{{ openId === item.id ? "收起明细" : "明细" }}</button>
        <button class="danger" :disabled="item.undone" @click="undo(item.id)">撤销</button>
      </div>
      <ul v-if="openId === item.id" class="log-detail">
        <li v-for="row in details" :key="row.id" class="log-line">
          <span class="log-status" :data-status="row.status">{{ statusLabel(row.status) }}</span>
          <span class="log-paths">
            {{ row.src_path }} → {{ row.dst_path }}
            <span class="error" v-if="row.error">{{ row.error }}</span>
          </span>
        </li>
      </ul>
      </div>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, ApiError } from "../api";

const items = ref<any[]>([]);
const details = ref<any[]>([]);
const openId = ref<number | null>(null);
const message = ref("");
const ready = ref(false);

const statusText: Record<string, string> = {
  success: "成功",
  failed: "失败",
  skipped: "跳过",
  marked: "已标记",
  retry: "重试",
};

function statusLabel(status: string) {
  return statusText[status] || status;
}

function when(value: string) {
  return (value || "").replace("T", " ").slice(0, 16);
}

async function load() {
  items.value = (await api<{ items: any[] }>("/api/operations")).items;
  ready.value = true;
}

async function showDetails(id: number) {
  openId.value = id;
  details.value = (await api<{ items: any[] }>(`/api/operations/${id}`)).items;
}

async function open(id: number) {
  if (openId.value === id) {
    openId.value = null;
    return;
  }
  await showDetails(id);
}

async function undo(id: number) {
  try {
    const result = await api<{ undone: number; failed: number }>(`/api/operations/${id}/undo`, { method: "POST" });
    message.value = `已撤销 ${result.undone} 项，失败 ${result.failed} 项`;
    await load();
    if (openId.value === id) await showDetails(id);
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "撤销失败";
  }
}

onMounted(load);
</script>
