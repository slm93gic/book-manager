<template>
  <section class="page">
    <h1>任务</h1>
    <div class="toolbar">
      <button class="danger" @click="clearRecords">清空扫描记录</button>
    </div>
    <p v-if="message" class="error">{{ message }}</p>
    <p v-if="!jobs.length" class="empty">没有任务。</p>
    <article v-for="job in jobs" :key="job.id" class="panel" style="padding: 12px; margin-bottom: 8px">
      <strong>#{{ job.id }} {{ names[job.type] || job.type }} · {{ labels[job.status] || job.status }}</strong>
      <p>{{ job.message }}</p>
      <div v-if="live(job)" class="job-ops">
        <button class="ghost" :disabled="job.status === 'paused'" @click="command(job, 'pause')">暂停</button>
        <button class="ghost" :disabled="job.status !== 'paused'" @click="command(job, 'resume')">继续</button>
        <button class="danger" @click="command(job, 'cancel')">取消</button>
      </div>
      <div v-if="percent(job) !== null" class="job-track" role="progressbar" :aria-valuenow="percent(job) ?? 0" aria-valuemin="0" aria-valuemax="100">
        <div class="job-fill" :style="{ width: `${percent(job)}%` }"></div>
      </div>
      <p class="muted" v-if="job.progress && percent(job) !== null">已处理 {{ job.progress.done || 0 }} / {{ job.progress.total || 0 }}，失败 {{ job.progress.failed || 0 }}</p>
      <p class="muted" v-else-if="job.progress">
        阶段 {{ job.progress.phase || "-" }}，目录 {{ job.progress.dirs || 0 }}，图书 {{ job.progress.books || job.progress.moved || 0 }}，已解析 {{ job.progress.parsed || 0 }}，失败 {{ job.progress.failed || 0 }}
      </p>
      <a :href="`/api/jobs/${job.id}/errors`" target="_blank">导出失败明细</a>
    </article>
    <div v-if="total" class="pager">
      <button class="ghost" :disabled="page <= 1" @click="load(page - 1)">上一页</button>
      <span>第 {{ page }} 页 / 共 {{ total }} 个</span>
      <button class="ghost" :disabled="page * pageSize >= total" @click="load(page + 1)">下一页</button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api, ApiError, type Job } from "../api";

const jobs = ref<Job[]>([]);
const page = ref(1);
const total = ref(0);
const pageSize = 10;
const message = ref("");
const names: Record<string, string> = { scan: "扫描", move: "移动", delete: "删除", rules: "整理" };
const labels: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

function live(job: Job) {
  return ["queued", "running", "paused"].includes(job.status);
}

function percent(job: Job) {
  const done = Number(job.progress?.done);
  const total = Number(job.progress?.total);
  if (!total || Number.isNaN(done)) return null;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}
let timer = 0;

async function load(next = page.value) {
  const wanted = Math.max(1, next);
  const data = await api<{ items: Job[]; total: number }>(`/api/jobs?page=${wanted}&page_size=${pageSize}`);
  const pages = Math.max(1, Math.ceil(data.total / pageSize));
  if (wanted > pages) {
    page.value = pages;
    await load(pages);
    return;
  }
  jobs.value = data.items;
  total.value = data.total;
  page.value = wanted;
}

async function clearRecords() {
  if (!window.confirm("清空本机扫描记录和任务列表？网盘上的图书文件会保留。")) return;
  message.value = "";
  try {
    await api("/api/scan/clear", { method: "POST" });
    await load();
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "清空失败";
  }
}

async function command(job: Job, name: string) {
  message.value = "";
  try {
    await api(`/api/jobs/${job.id}/${name}`, { method: "POST" });
    await load();
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "操作失败";
  }
}

onMounted(() => {
  load();
  timer = window.setInterval(load, 1500);
});
onUnmounted(() => window.clearInterval(timer));
</script>
