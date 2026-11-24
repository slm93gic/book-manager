<template>
  <section class="page settings-page">
    <header class="page-head">
      <h1>设置</h1>
      <p v-if="status" class="stat-line"><strong>{{ status.book_count }}</strong> 本<span></span><strong>{{ status.folder_count }}</strong> 个目录</p>
    </header>
    <p v-if="message" class="settings-toast" :class="{ error }">{{ message }}</p>
    <form class="settings-form" @submit.prevent="save">
      <div class="panel sheet">
        <div class="panel-head">
          <h2>NAS</h2>
          <span v-if="status" class="status-pill" :data-status="status.readable ? 'running' : 'failed'">{{ status.readable ? "已连接" : "未连接" }}</span>
        </div>
        <div class="sheet-body">
          <div v-if="status" class="route-board">
            <article class="route-card">
              <span>原始数据</span>
              <strong>{{ status.root_display || "未连接" }}</strong>
            </article>
            <div class="route-join" aria-hidden="true"></div>
            <article class="route-card dest">
              <span>迁移目标</span>
              <strong>{{ status.classify_display || "未设置" }}</strong>
            </article>
          </div>
          <div v-if="status" class="route-flags">
            <em :data-on="status.readable">{{ status.readable ? "可读" : "不可读" }}</em>
            <em :data-on="status.writable">{{ status.writable ? "可写" : "不可写" }}</em>
            <em data-on="true">上次扫描 {{ scanWhen(status.last_scan_at) }}</em>
          </div>
          <p class="settings-kicker">连接</p>
          <label class="field">SMB 基本地址<span class="field-hint">只填 NAS 地址，例如 smb://192.168.31.88</span><input v-model="form.smb_host" placeholder="smb://192.168.31.88" /></label>
          <div class="field-pair">
            <label class="field">账户<input v-model="form.smb_username" placeholder="Samba 用户名" autocomplete="username" /></label>
            <label class="field">密码<input v-model="form.smb_password" type="password" autocomplete="current-password" :placeholder="status?.smb_password_set ? '已保存，留空则不修改' : 'SMB 密码'" /></label>
          </div>
          <p class="settings-kicker">路径</p>
          <div class="field-pair">
            <label class="field">原始数据路径<span class="field-hint">共享名，例如 书库。容器里对应 /library</span><input v-model="form.source_path" placeholder="书库" /></label>
            <label class="field">迁移目标路径<span class="field-hint">共享名，例如 books。容器里对应 /shelf</span><input v-model="form.target_path" placeholder="books" /></label>
          </div>
          <p class="settings-note">路径和现在一样时，保存不会清空扫描记录。路径一旦改掉，索引会清空，需要重新扫描。</p>
        </div>
      </div>
      <div class="panel sheet">
        <div class="panel-head"><h2>扫描</h2></div>
        <div class="sheet-body">
          <div class="settings-scan">
            <label class="field">识别扩展名<span class="field-hint">一行一个</span><textarea v-model="form.extensions" rows="4" /></label>
            <label class="field">排除目录<span class="field-hint">一行一个，这些目录不进入索引</span><textarea v-model="form.excludes" rows="4" /></label>
            <label class="field">只索引这些路径<span class="field-hint">一行一个，留空表示整库</span><textarea v-model="form.includes" rows="3" /></label>
            <label class="field">收件箱<span class="field-hint">一行一个。这里面的书始终算未分类</span><textarea v-model="form.inbox" rows="3" /></label>
          </div>
          <div class="field-pair settings-numbers">
            <label class="field">解析并发<input v-model.number="form.concurrency" type="number" min="1" max="16" /></label>
            <label class="field">批量确认阈值<input v-model.number="form.batch_threshold" type="number" min="1" /></label>
          </div>
          <div class="settings-actions">
            <button class="primary" type="submit">保存</button>
          </div>
        </div>
      </div>
    </form>
    <form class="panel sheet" @submit.prevent="changePassword">
      <div class="panel-head"><h2>更换口令</h2></div>
      <div class="sheet-body">
        <div class="field-pair">
          <label class="field">原口令<input v-model="oldPassword" type="password" /></label>
          <label class="field">新口令<input v-model="password" type="password" /></label>
        </div>
        <div class="settings-actions">
          <button class="ghost" type="submit">更新口令</button>
        </div>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, ApiError } from "../api";

const form = reactive({
  smb_host: "",
  smb_username: "",
  smb_password: "",
  source_path: "",
  target_path: "",
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

function scanWhen(value: string) {
  if (!value) return "还没有";
  return value.replace("T", " ").slice(0, 16);
}

function joinPath(share: string, subpath: string) {
  const name = (share || "").trim();
  const extra = (subpath || "").trim().replace(/^[/\\]+/, "");
  if (!name) return "";
  return extra ? `${name}/${extra}` : name;
}

async function load() {
  const data = await api<any>("/api/settings");
  status.value = data;
  form.smb_host = data.smb_host ? `smb://${data.smb_host}` : "";
  form.smb_username = data.smb_username || "";
  form.smb_password = "";
  form.source_path = joinPath(data.smb_share, data.smb_path);
  form.target_path = joinPath(data.classify_share, data.classify_path);
  form.extensions = lines(data.extensions);
  form.excludes = lines(data.excludes);
  form.includes = lines(data.includes);
  form.inbox = lines(data.inbox);
  form.concurrency = data.concurrency;
  form.batch_threshold = data.batch_threshold;
}

async function save() {
  error.value = false;
  try {
    status.value = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({
        smb_host: form.smb_host.trim(),
        smb_username: form.smb_username.trim(),
        smb_password: form.smb_password,
        source_path: form.source_path.trim(),
        target_path: form.target_path.trim(),
        extensions: form.extensions,
        excludes: form.excludes,
        includes: form.includes,
        inbox: form.inbox,
        concurrency: form.concurrency,
        batch_threshold: form.batch_threshold,
      }),
    });
    message.value = "已保存";
    form.smb_password = "";
    form.smb_host = status.value.smb_host ? `smb://${status.value.smb_host}` : form.smb_host;
    form.source_path = joinPath(status.value.smb_share, status.value.smb_path);
    form.target_path = joinPath(status.value.classify_share, status.value.classify_path);
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
