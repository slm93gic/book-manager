<template>
  <section class="page">
    <h1>分类规则</h1>
    <p class="muted">规则按顺序匹配，先命中的先生效。扫描结束不会自动移动，必须在这里预览后执行。</p>
    <form class="panel" style="padding: 12px; margin-bottom: 12px" @submit.prevent="save">
      <div class="toolbar">
        <input v-model="form.name" placeholder="规则名称" />
        <input v-model="form.target" placeholder="目标相对路径，如 分类/科幻" />
        <label class="row"><input type="checkbox" v-model="form.create_missing" /> 目标不存在则创建</label>
      </div>
      <div class="toolbar">
        <input v-model="form.tags" placeholder="标签，逗号分隔，需全部满足" />
        <input v-model="form.authors" placeholder="作者" />
        <input v-model="form.series" placeholder="丛书" />
        <input v-model="form.language" placeholder="语言" />
        <input v-model="form.formats" placeholder="格式，如 epub,pdf" />
        <input v-model="form.path_prefix" placeholder="当前路径前缀" />
      </div>
      <button class="primary" type="submit">保存规则</button>
    </form>
    <article v-for="rule in rules" :key="rule.id" class="panel" style="padding: 12px; margin-bottom: 8px">
      <strong>{{ rule.priority }}. {{ rule.name }}</strong>
      <span class="muted"> → {{ rule.target_relative_path }}</span>
      <span v-if="!rule.enabled" class="tag">停用</span>
      <p class="path">{{ JSON.stringify(rule.conditions) }}</p>
      <div class="row">
        <button class="ghost" @click="toggle(rule)">{{ rule.enabled ? "停用" : "启用" }}</button>
        <button class="danger" @click="remove(rule.id)">删除</button>
      </div>
    </article>
    <div class="toolbar">
      <label class="row"><input type="radio" value="unclassified" v-model="scope" /> 全部未分类</label>
      <button class="ghost" @click="preview">预览</button>
      <button class="primary" :disabled="!previewed" @click="apply">执行预览结果</button>
    </div>
    <div v-if="plan">
      <p>将移动 {{ plan.total }} 本。</p>
      <article v-for="group in plan.groups" :key="group.rule_id" class="panel" style="padding: 10px; margin-bottom: 8px">
        <strong>{{ group.name }}</strong> → {{ group.target }}（{{ group.count }}）
        <div class="muted">{{ group.samples.join("、") }}</div>
      </article>
    </div>
    <p v-if="message" :class="error ? 'error' : 'muted'">{{ message }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, ApiError, waitJob } from "../api";

interface Rule {
  id: number;
  priority: number;
  name: string;
  target_relative_path: string;
  conditions: Record<string, unknown>;
  create_missing: number;
  enabled: number;
}

const rules = ref<Rule[]>([]);
const form = reactive({ name: "", target: "", tags: "", authors: "", series: "", language: "", formats: "", path_prefix: "", create_missing: true });
const scope = ref("unclassified");
const plan = ref<{ total: number; groups: { rule_id: number; name: string; target: string; count: number; samples: string[] }[] } | null>(null);
const previewed = ref(false);
const message = ref("");
const error = ref(false);

function list(value: string) {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}

async function load() {
  rules.value = (await api<{ items: Rule[] }>("/api/rules")).items;
}

async function save() {
  error.value = false;
  try {
    await api("/api/rules", {
      method: "POST",
      body: JSON.stringify({
        name: form.name || form.target,
        target: form.target,
        create_missing: form.create_missing,
        conditions: {
          tags: list(form.tags),
          authors: list(form.authors),
          series: form.series,
          language: form.language,
          formats: list(form.formats),
          path_prefix: form.path_prefix,
        },
      }),
    });
    await load();
    previewed.value = false;
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "保存失败";
    error.value = true;
  }
}

async function toggle(rule: Rule) {
  await api(`/api/rules/${rule.id}`, {
    method: "PUT",
    body: JSON.stringify({
      name: rule.name,
      target: rule.target_relative_path,
      conditions: rule.conditions,
      create_missing: !!rule.create_missing,
      enabled: !rule.enabled,
    }),
  });
  await load();
}

async function remove(id: number) {
  await api(`/api/rules/${id}`, { method: "DELETE" });
  await load();
}

async function preview() {
  plan.value = await api("/api/rules/preview", { method: "POST", body: JSON.stringify({ scope: scope.value }) });
  previewed.value = true;
  message.value = "";
}

async function apply() {
  const job = await api<{ id: number }>("/api/rules/apply", {
    method: "POST",
    body: JSON.stringify({ scope: scope.value, confirmed: true }),
  });
  const done = await waitJob(job.id);
  message.value = done.message || done.status;
  error.value = done.status === "failed";
  previewed.value = false;
  await preview();
}

onMounted(load);
</script>
