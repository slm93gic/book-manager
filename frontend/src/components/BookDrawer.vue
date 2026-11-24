<template>
  <transition name="pop">
  <div v-if="open" class="drawer-back" @click.self="$emit('close')">
    <aside class="drawer" v-if="book">
      <div class="detail-head">
        <h2>图书详情</h2>
        <button class="ghost" @click="$emit('close')">关闭</button>
      </div>
      <div class="detail-body">
        <div class="detail-side">
          <img class="cover lg" :src="`/api/covers/${book.id}?store=${store}`" alt="" @error="hideCover" v-show="showCover" />
          <p class="path drawer-path">{{ book.relative_path }}</p>
          <p class="muted">来源 {{ book.metadata_source || "未知" }} · 可信度 {{ book.confidence || "未知" }} · {{ book.primary_format }}</p>
        </div>
        <div class="detail-form">
          <div class="field">
            <span class="field-name">书名<label class="check"><input type="checkbox" v-model="form.lock_title" />锁定</label></span>
            <input v-model="form.title" />
          </div>
          <div class="field">
            <span class="field-name">作者<label class="check"><input type="checkbox" v-model="form.lock_authors" />锁定</label></span>
            <input v-model="authors" placeholder="用顿号或逗号分隔" />
          </div>
          <div class="field">
            <span class="field-name">标签<label class="check"><input type="checkbox" v-model="form.lock_tags" />锁定</label></span>
            <input v-model="tags" placeholder="用顿号或逗号分隔" />
            <p v-if="store !== 'shelf' && book.suggested_category" class="muted detail-note">推荐分类 {{ book.suggested_category }}。按中国图书馆分类法，用这本书自己的书名和标签算出，不写回文件。</p>
          </div>
          <div class="detail-pair">
            <div class="field">
              <span class="field-name">丛书<label class="check"><input type="checkbox" v-model="form.lock_series" />锁定</label></span>
              <input v-model="form.series" />
            </div>
            <div class="field">
              <span class="field-name">序号</span>
              <input v-model="form.series_index" />
            </div>
          </div>
          <p class="muted detail-note">保存时只回写改过的书名、作者、标签和丛书。没有 metadata.opf 会新建，不改电子书正文。</p>
          <p v-if="error" class="notice error">{{ error }}</p>
          <button class="primary" @click="save">保存并锁定已改字段</button>
        </div>
      </div>
      <div class="detail-files">
        <h3>文件</h3>
        <ul>
          <li v-for="file in book.files" :key="file.relative_path">{{ file.relative_path }}</li>
        </ul>
      </div>
    </aside>
  </div>
  </transition>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import { api, ApiError } from "../api";

const props = defineProps<{ bookId: number | null; store?: string }>();
const emit = defineEmits<{ close: []; saved: [] }>();
const open = ref(false);
const book = ref<any>(null);
const authors = ref("");
const tags = ref("");
const error = ref("");
const showCover = ref(true);
const form = reactive({
  title: "",
  series: "",
  series_index: "",
  lock_title: false,
  lock_authors: false,
  lock_tags: false,
  lock_series: false,
});

watch(
  () => props.bookId,
  async (id) => {
    open.value = id != null;
    showCover.value = true;
    error.value = "";
    if (!id) return;
    book.value = await api(`/api/books/${id}?store=${props.store || "library"}`);
    form.title = book.value.title || "";
    form.series = book.value.series || "";
    form.series_index = book.value.series_index || "";
    form.lock_title = !!book.value.lock_title;
    form.lock_authors = !!book.value.lock_authors;
    form.lock_tags = !!book.value.lock_tags;
    form.lock_series = !!book.value.lock_series;
    authors.value = book.value.authors_text || (book.value.authors || []).join("、");
    tags.value = book.value.tags_text || (book.value.tags || []).join("、");
  },
);

function hideCover(event: Event) {
  showCover.value = false;
  (event.target as HTMLImageElement).style.display = "none";
}

function split(value: string) {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}

async function save() {
  if (!props.bookId || !book.value) return;
  error.value = "";
  const authorText = book.value.authors_text || (book.value.authors || []).join("、");
  const tagText = book.value.tags_text || (book.value.tags || []).join("、");
  if (form.title !== (book.value.title || "")) form.lock_title = true;
  if (authors.value !== authorText) form.lock_authors = true;
  if (tags.value !== tagText) form.lock_tags = true;
  if (form.series !== (book.value.series || "") || form.series_index !== (book.value.series_index || "")) {
    form.lock_series = true;
  }
  try {
    const saved = await api<{ opf_error?: string }>(`/api/books/${props.bookId}?store=${props.store || "library"}`, {
      method: "PATCH",
      body: JSON.stringify({
        title: form.title,
        authors: split(authors.value),
        tags: split(tags.value),
        series: form.series,
        series_index: form.series_index,
        lock_title: form.lock_title,
        lock_authors: form.lock_authors,
        lock_tags: form.lock_tags,
        lock_series: form.lock_series,
      }),
    });
    if (saved?.opf_error) {
      error.value = `修改已记在本机，回写 metadata.opf 失败：${saved.opf_error}`;
      emit("saved");
      return;
    }
    emit("saved");
    emit("close");
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "保存失败";
  }
}
</script>
