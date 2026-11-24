<template>
  <section class="page">
    <header class="page-head">
      <h1>操作日志</h1>
      <p v-if="items.length" class="stat-line"><strong>{{ items.length }}</strong> 条</p>
    </header>
    <article v-for="item in items" :key="item.id" class="panel log-card">
      <div class="panel-head">
        <h2>#{{ item.id }} {{ item.summary }}</h2>
        <span class="status-pill">{{ item.created_at }}</span>
      </div>
      <div class="sheet-body">
      <div class="row">
        <button class="ghost" @click="open(item.id)">明细</button>
        <button class="danger" :disabled="item.undone" @click="undo(item.id)">撤销</button>
      </div>
      <ul v-if="openId === item.id">
        <li v-for="row in details" :key="row.id" class="log-line">
          {{ row.status }} {{ row.src_path }} → {{ row.dst_path }}
          <span class="error" v-if="row.error">{{ row.error }}</span>
        </li>
      </ul>
      </div>
    </article>
    <p v-if="message">{{ message }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, ApiError } from "../api";

const items = ref<any[]>([]);
const details = ref<any[]>([]);
const openId = ref<number | null>(null);
const message = ref("");

async function load() {
  items.value = (await api<{ items: any[] }>("/api/operations")).items;
}

async function open(id: number) {
  openId.value = id;
  details.value = (await api<{ items: any[] }>(`/api/operations/${id}`)).items;
}

async function undo(id: number) {
  try {
    const result = await api<{ undone: number; failed: number }>(`/api/operations/${id}/undo`, { method: "POST" });
    message.value = `已撤销 ${result.undone} 项，失败 ${result.failed} 项`;
    await load();
    if (openId.value === id) await open(id);
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "撤销失败";
  }
}

onMounted(load);
</script>
