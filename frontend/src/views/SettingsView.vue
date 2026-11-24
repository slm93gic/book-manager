<template>
  <section class="page">
    <header class="page-head">
      <h1>设置</h1>
      <p v-if="status" class="stat-line"><strong>{{ status.book_count }}</strong> 本<span></span><strong>{{ status.folder_count }}</strong> 个目录</p>
    </header>
    <form class="panel sheet" @submit.prevent="save">
      <div class="panel-head"><h2>书库连接</h2></div>
      <div class="sheet-body">
      <label class="field">原始目录<input v-model="form.smb_host" placeholder="smb://192.168.31.88/书库/" @change="applySmbAddress" /></label>
      <label class="field">共享名<input v-model="form.smb_share" placeholder="粘贴完整地址时自动填入，例如 书库" /></label>
      <label class="field">共享内子路径<input v-model="form.smb_path" placeholder="可选" /></label>
      <label class="field">分类目录<input v-model="form.classify_target" placeholder="smb://192.168.31.88/books/" @change="applyClassifyAddress" /></label>
      <label class="field">账户<input v-model="form.smb_username" placeholder="Samba 用户名" /></label>
      <label class="field">密码<input v-model="form.smb_password" type="password" :placeholder="status?.smb_password_set ? '已保存，留空则不修改' : 'SMB 密码'" /></label>
      <label class="field">本机路径（不使用 SMB 时填写）<input v-model="form.library_root" placeholder="例如 D:\Books 或 Z:\Books" /></label>
      </div>
      <div class="panel-head"><h2>索引</h2></div>
      <div class="sheet-body">
      <label class="field">识别扩展名，一行一个<textarea v-model="form.extensions" /></label>
      <label class="field">排除目录，一行一个<textarea v-model="form.excludes" /></label>
      <label class="field">只索引这些路径（可空）<textarea v-model="form.includes" /></label>
      <label class="field">收件箱路径，其中的书始终算未分类<textarea v-model="form.inbox" /></label>
      <label class="field">解析并发<input v-model.number="form.concurrency" type="number" min="1" max="16" /></label>
      <label class="field">批量确认阈值<input v-model.number="form.batch_threshold" type="number" min="1" /></label>
      <p class="muted" v-if="status">当前路径 {{ status.root_display || "未连接" }} · 图书 {{ status.book_count }} · 目录 {{ status.folder_count }} · 上次扫描 {{ status.last_scan_at || "还没有" }}</p>
      <p class="muted" v-if="status">索引 {{ status.db_path }} · {{ status.readable ? "可读" : "不可读" }} · {{ status.writable ? "可写" : "不可写" }}</p>
      <p v-if="message" :class="error ? 'error' : 'muted'">{{ message }}</p>
      <button class="primary" type="submit">保存</button>
      </div>
    </form>
    <form class="panel sheet" @submit.prevent="changePassword">
      <div class="panel-head"><h2>更换口令</h2></div>
      <div class="sheet-body">
      <label class="field">原口令<input v-model="oldPassword" type="password" /></label>
      <label class="field">新口令<input v-model="password" type="password" /></label>
      <button class="ghost" type="submit">更新口令</button>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, ApiError } from "../api";

const form = reactive({
  smb_host: "",
  smb_share: "",
  smb_path: "",
  classify_target: "",
  classify_share: "",
  classify_path: "",
  smb_username: "",
  smb_password: "",
  library_root: "",
  extensions: "",
  excludes: "",
  includes: "",
  inbox: "",
  concurrency: 6,
  batch_threshold: 50,
});
const status = ref<any>(null);
const message = ref("");
const error = ref(false);
const oldPassword = ref("");
const password = ref("");

function lines(value: string[]) {
  return (value || []).join("\n");
}

async function load() {
  const data = await api<any>("/api/settings");
  status.value = data;
  form.library_root = data.root_display || data.library_root || "";
  form.smb_host = data.smb_host || "";
  form.smb_share = data.smb_share || "";
  form.smb_path = data.smb_path || "";
  form.smb_username = data.smb_username || "";
  form.smb_password = "";
  form.classify_share = data.classify_share || "";
  form.classify_path = data.classify_path || "";
  form.classify_target = data.classify_share
    ? `smb://${data.smb_host || ""}/${data.classify_share}${data.classify_path ? "/" + data.classify_path : ""}/`
    : "";
  form.extensions = lines(data.extensions);
  form.excludes = lines(data.excludes);
  form.includes = lines(data.includes);
  form.inbox = lines(data.inbox);
  form.concurrency = data.concurrency;
  form.batch_threshold = data.batch_threshold;
}

function applySmbAddress() {
  const raw = form.smb_host.trim();
  if (!raw.includes("://") && !raw.includes("/") && !raw.includes("\\")) return;
  const text = raw.replace(/^[a-z]+:\/\//i, "").replace(/^[/\\]+/, "");
  const parts = text.split(/[/\\]+/).filter(Boolean);
  form.smb_host = parts[0] || "";
  if (parts[1]) form.smb_share = parts[1];
  if (parts.length > 2) form.smb_path = parts.slice(2).join("/");
}

function applyClassifyAddress() {
  const raw = form.classify_target.trim();
  if (!raw) {
    form.classify_share = "";
    form.classify_path = "";
    return;
  }
  if (!raw.includes("://") && !raw.includes("/") && !raw.includes("\\")) {
    form.classify_share = raw;
    form.classify_path = "";
    return;
  }
  const text = raw.replace(/^[a-z]+:\/\//i, "").replace(/^[/\\]+/, "");
  const parts = text.split(/[/\\]+/).filter(Boolean);
  const hostLike = parts.length >= 2 && (raw.includes("://") || parts[0].includes(".") || parts[0] === form.smb_host);
  form.classify_share = hostLike ? parts[1] : parts[0];
  form.classify_path = parts.slice(hostLike ? 2 : 1).join("/");
}

async function save() {
  error.value = false;
  applySmbAddress();
  applyClassifyAddress();
  try {
    status.value = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        smb_host: form.smb_host,
        smb_share: form.smb_share,
        smb_path: form.smb_path,
        smb_username: form.smb_username,
        smb_password: form.smb_password,
        classify_share: form.classify_share,
        classify_path: form.classify_path,
        library_root: form.smb_host ? "" : form.library_root,
        extensions: form.extensions,
        excludes: form.excludes,
        includes: form.includes,
        inbox: form.inbox,
        concurrency: form.concurrency,
        batch_threshold: form.batch_threshold,
      }),
    });
    message.value = form.smb_host ? "已连接并保存" : "已保存";
    form.smb_password = "";
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "保存失败";
    error.value = true;
  }
}

async function changePassword() {
  try {
    await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ old_password: oldPassword.value, password: password.value }),
    });
    message.value = "口令已更新";
    error.value = false;
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "更新失败";
    error.value = true;
  }
}

onMounted(load);
</script>
