<template>
  <div v-if="bare" class="login-wrap"><router-view /></div>
  <div v-else class="app-shell">
    <aside class="nav">
      <p class="brand"><span class="brand-mark">书</span>图书分类</p>
      <p class="nav-label">浏览</p>
      <router-link to="/unclassified"><span class="nav-icon">📚</span>未分类</router-link>
      <router-link to="/classified"><span class="nav-icon">📗</span>已整理</router-link>
      <p class="nav-label">整理</p>
      <router-link to="/duplicates/unclassified"><span class="nav-icon">🔁</span>未分类重复</router-link>
      <router-link to="/duplicates/classified"><span class="nav-icon">📑</span>已整理重复</router-link>
      <p class="nav-label">记录</p>
      <router-link to="/jobs"><span class="nav-icon">📋</span>任务</router-link>
      <router-link to="/log"><span class="nav-icon">📝</span>操作日志</router-link>
      <div class="nav-spacer"></div>
      <router-link to="/settings"><span class="nav-icon">⚙️</span>设置</router-link>
    </aside>
    <div class="main">
      <div v-if="active.length" class="job-banners">
      <div v-for="job in active" :key="job.id" class="job-banner">
        <div class="job-meter">
          <span>{{ label(job) }}</span>
          <div
            v-if="percent(job) !== null"
            class="job-track"
            role="progressbar"
            :aria-valuenow="percent(job) ?? 0"
            aria-valuemin="0"
            aria-valuemax="100"
          >
            <div class="job-fill" :style="{ width: `${percent(job)}%` }"></div>
          </div>
        </div>
        <router-link to="/jobs">查看任务</router-link>
      </div>
      </div>
      <router-view />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, type Job } from "./api";

const route = useRoute();
const router = useRouter();
const bare = computed(() => route.path === "/login");
const active = ref<Job[]>([]);
let timer = 0;
let source: EventSource | null = null;

const labels: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

const names: Record<string, string> = { scan: "扫描", move: "移动", delete: "删除", rules: "整理" };

function percent(job: Job) {
  const done = Number(job.progress?.done);
  const total = Number(job.progress?.total);
  if (!total || Number.isNaN(done)) return null;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}

function label(job: Job) {
  const name = names[job.type] || job.type;
  const state = labels[job.status] || job.status;
  const ratio = percent(job);
  const count = ratio === null ? "" : `${job.progress.done}/${job.progress.total}`;
  return [name, state, count, job.message].filter(Boolean).join(" ");
}

async function refresh() {
  if (bare.value) return;
  try {
    const data = await api<{ items: Job[] }>("/api/jobs?page=1&page_size=100");
    active.value = data.items.filter((item) => ["queued", "running", "paused"].includes(item.status));
  } catch {
    active.value = [];
  }
}

onMounted(async () => {
  const status = await api<{ has_password: boolean; logged_in: boolean }>("/api/auth/status");
  if (!status.logged_in && route.path !== "/login") router.replace("/login");
  if (status.logged_in && route.path === "/login") router.replace("/unclassified");
  timer = window.setInterval(refresh, 2000);
  if (status.logged_in) {
    source = new EventSource("/api/jobs/stream");
    source.onmessage = () => refresh();
  }
  refresh();
});

onUnmounted(() => {
  window.clearInterval(timer);
  source?.close();
});
</script>
