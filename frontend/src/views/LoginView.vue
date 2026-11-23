<template>
  <form class="login" @submit.prevent="submit">
    <h1>{{ setup ? "设置口令" : "登录" }}</h1>
    <p class="muted">这个服务能移动 NAS 上的图书文件夹，请设置口令后再在局域网使用。</p>
    <div class="field">
      <label>口令</label>
      <input v-model="password" type="password" autocomplete="current-password" />
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <button class="primary" type="submit">{{ setup ? "保存并进入" : "进入" }}</button>
  </form>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError, saveToken } from "../api";

const router = useRouter();
const setup = ref(false);
const password = ref("");
const error = ref("");

onMounted(async () => {
  const status = await api<{ has_password: boolean; logged_in: boolean }>("/api/auth/status");
  setup.value = !status.has_password;
  if (status.logged_in) router.replace("/");
});

async function submit() {
  error.value = "";
  try {
    const path = setup.value ? "/api/auth/setup" : "/api/auth/login";
    const result = await api<{ token: string }>(path, {
      method: "POST",
      body: JSON.stringify({ password: password.value }),
    });
    saveToken(result.token);
    location.assign("/");
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "登录失败";
  }
}
</script>
