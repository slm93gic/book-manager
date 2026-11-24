<template>
  <div v-if="bare" class="login-wrap">
    <router-view v-slot="{ Component, route: current }">
      <transition name="page-switch" mode="out-in" appear>
        <component :is="Component" :key="current.path" />
      </transition>
    </router-view>
  </div>
  <div v-else class="app-shell" :class="{ 'nav-collapsed': navCollapsed }">
    <aside class="nav">
      <div class="nav-head">
        <p class="brand"><span class="brand-mark">书</span><span class="brand-name">图书分类</span></p>
        <button type="button" class="nav-collapse" :title="navCollapsed ? '展开菜单' : '收起菜单'" @click="navCollapsed = !navCollapsed">{{ navCollapsed ? "›" : "‹" }}</button>
      </div>
      <p class="nav-label">浏览</p>
      <router-link to="/unclassified" title="未分类"><span class="nav-icon">📚</span><span class="nav-text">未分类</span></router-link>
      <router-link to="/classified" title="已整理"><span class="nav-icon">📗</span><span class="nav-text">已整理</span></router-link>
      <p class="nav-label">整理</p>
      <router-link to="/duplicates/unclassified" title="未分类重复"><span class="nav-icon">🔁</span><span class="nav-text">未分类重复</span></router-link>
      <router-link to="/duplicates/classified" title="已整理重复"><span class="nav-icon">📑</span><span class="nav-text">已整理重复</span></router-link>
      <p class="nav-label">记录</p>
      <router-link to="/jobs" title="任务"><span class="nav-icon">📋</span><span class="nav-text">任务</span></router-link>
      <router-link to="/log" title="操作日志"><span class="nav-icon">📝</span><span class="nav-text">操作日志</span></router-link>
      <div class="nav-spacer"></div>
      <router-link to="/settings" title="设置"><span class="nav-icon">⚙️</span><span class="nav-text">设置</span></router-link>
    </aside>
    <div class="main">
      <transition-group v-if="active.length" name="banner" tag="div" class="job-banners">
      <div v-for="job in active" :key="job.id" class="job-banner" :data-status="job.status">
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
      </transition-group>
      <router-view v-slot="{ Component, route: current }">
        <transition name="page-switch" mode="out-in" appear>
          <component :is="Component" :key="current.path" />
        </transition>
      </router-view>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, type Job } from "./api";

const route = useRoute();
const router = useRouter();
const bare = computed(() => route.path === "/login");
const navCollapsed = ref(localStorage.getItem("bm_nav_collapsed") === "1");
watch(navCollapsed, (value) => localStorage.setItem("bm_nav_collapsed", value ? "1" : "0"));
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
