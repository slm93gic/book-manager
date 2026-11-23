<template>
  <section class="page">
    <h1>标签</h1>
    <p class="muted">重命名和合并会锁定相关图书的标签，并写回每本书文件夹里的 metadata.opf，避免下次扫描覆盖。</p>
    <table class="plain panel">
      <thead><tr><th>标签</th><th>数量</th><th></th></tr></thead>
      <tbody>
        <tr v-for="tag in tags" :key="tag.id">
          <td>{{ tag.name }}</td>
          <td>{{ tag.count }}</td>
          <td class="row">
            <button class="ghost" @click="rename(tag)">重命名</button>
            <button class="ghost" @click="merge(tag)">合并到…</button>
            <button class="primary" @click="draft(tag)">生成规则草稿</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="message">{{ message }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, ApiError } from "../api";

const tags = ref<{ id: number; name: string; count: number }[]>([]);
const message = ref("");

async function load() {
  tags.value = (await api<{ items: typeof tags.value }>("/api/tags")).items;
}

async function rename(tag: { id: number; name: string }) {
  const name = window.prompt("新标签名", tag.name);
  if (!name) return;
  try {
    const result = await api<{ written: number; failed?: { error: string }[] }>("/api/tags/rename", {
      method: "POST",
      body: JSON.stringify({ tag_id: tag.id, name }),
    });
    const failed = result.failed?.length || 0;
    message.value = failed ? `已重命名，回写 ${result.written} 本，失败 ${failed} 本` : `已重命名，并回写 ${result.written} 本`;
    await load();
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "重命名失败";
  }
}

async function merge(tag: { id: number; name: string }) {
  const name = window.prompt("合并到哪个标签（填写已有标签名）");
  const target = tags.value.find((item) => item.name === name);
  if (!target) {
    message.value = "没有找到目标标签";
    return;
  }
  const result = await api<{ written: number; failed?: { error: string }[] }>("/api/tags/merge", {
    method: "POST",
    body: JSON.stringify({ source_id: tag.id, target_id: target.id }),
  });
  const failed = result.failed?.length || 0;
  message.value = failed
    ? `已将「${tag.name}」并入「${target.name}」，回写 ${result.written} 本，失败 ${failed} 本`
    : `已将「${tag.name}」并入「${target.name}」，并回写 ${result.written} 本`;
  await load();
}

async function draft(tag: { id: number }) {
  const rule = await api<{ name: string }>("/api/tags/" + tag.id + "/draft-rule", { method: "POST" });
  message.value = `已生成规则草稿「${rule.name}」，请到分类规则页预览后再执行。`;
}

onMounted(load);
</script>
