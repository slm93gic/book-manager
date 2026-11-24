<template>
  <section class="page dup-page">
    <header class="page-head dup-hero">
      <h1>{{ organized ? "已整理重复" : "未分类重复" }}</h1>
      <p v-if="groupCount" class="dup-stats"><strong>{{ groupCount }}</strong> 组<span></span><strong>{{ bookCount }}</strong> 本</p>
    </header>
    <div class="dup-delete-bar">
      <span class="dup-selected">已选 <strong>{{ picked.size }}</strong> 本</span>
      <button class="danger" :disabled="!picked.size" @click="removeBooks">删除所选</button>
    </div>
    <p v-if="message" :class="error ? 'error' : 'muted'">{{ message }}</p>
    <p v-if="!groupCount" class="empty">没有重复图书。</p>
    <article v-for="group in groups" :key="group.label + group.books[0].id" class="panel dup-group">
      <div class="dup-head">
        <div class="dup-title">
          <h2>{{ group.label }}</h2>
          <span class="dup-count">{{ group.count }} 本</span>
        </div>
        <div class="dup-actions">
          <span class="dup-picked">已选 {{ groupPicked(group) }} 本</span>
          <button class="danger" :disabled="!groupPicked(group)" @click="removeGroup(group)">删除所选</button>
          <button class="ghost" @click="ignoreGroup(group)">忽视</button>
        </div>
      </div>
      <div class="dup-cards">
        <article v-for="book in group.books" :key="book.id" class="dup-card" :class="{ picked: picked.has(book.id), pending: !hasFormat(book.primary_format) }" @click="toggle(book.id)">
          <input type="checkbox" :checked="picked.has(book.id)" @click.stop @change="toggle(book.id)" />
          <img
            v-if="!hiddenCovers[book.id]"
            class="cover"
            :src="`/api/covers/${book.id}?store=${store}`"
            alt=""
            draggable="false"
            loading="lazy"
            @error="hiddenCovers[book.id] = true"
          />
          <span v-else class="cover placeholder">无封面</span>
          <span class="dup-meta">
            <span class="dup-name">
              <button class="book-title" :title="book.title || '未命名'" @click.stop="detailId = book.id">{{ book.title || "未命名" }}</button>
              <span v-if="!picked.has(book.id)" class="dup-keep">保留</span>
              <span v-if="isLargest(group, book)" class="dup-max">最大</span>
            </span>
            <span v-if="seriesLabel(book)" class="dup-series">{{ seriesLabel(book) }}</span>
            <span class="card-foot">
              <span class="dup-facts">
                <span class="fact-size">大小 {{ formatSize(book.size) }}</span>
                <span class="fact-format" :class="{ 'pending-format': !hasFormat(book.primary_format) }">格式 {{ hasFormat(book.primary_format) ? formatLabel(book.primary_format) : "待删除" }}</span>
                <span class="fact-author" :title="book.authors_text || '—'">作者 {{ book.authors_text || "—" }}</span>
              </span>
              <span class="dup-path" :title="book.relative_path">{{ book.relative_path }}</span>
            </span>
          </span>
        </article>
      </div>
    </article>
    <div v-if="groupCount" class="pager">
      <button class="ghost" :disabled="page <= 1" @click="load(page - 1)">上一页</button>
      <span>第 {{ page }} 页 / 共 {{ groupCount }} 组</span>
      <button class="ghost" :disabled="page * pageSize >= groupCount" @click="load(page + 1)">下一页</button>
    </div>
    <BookDrawer :book-id="detailId" :store="store" @close="detailId = null" @saved="load" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import BookDrawer from "../components/BookDrawer.vue";
import { api, ApiError, waitJob } from "../api";

const props = defineProps<{ organized?: boolean }>();
const organized = computed(() => !!props.organized);
const store = computed(() => (organized.value ? "shelf" : "library"));
const groups = ref<{ label: string; count: number; books: DuplicateBook[] }[]>([]);
const groupCount = ref(0);
const bookCount = ref(0);
const page = ref(1);
const pageSize = 10;
const picked = ref(new Set<number>());
const hiddenCovers = reactive<Record<number, boolean>>({});
const detailId = ref<number | null>(null);
const message = ref("");
const error = ref(false);

interface DuplicateBook {
  id: number;
  title: string;
  authors_text: string;
  primary_format: string;
  size: number;
  relative_path: string;
  series?: string;
  series_index?: string;
}

function hasFormat(value: string | null | undefined) {
  return !!(value || "").replace(/^\./, "").trim();
}

function formatLabel(value: string | null | undefined) {
  const text = (value || "").replace(/^\./, "").trim();
  return text ? text.toUpperCase() : "—";
}

function seriesLabel(book: DuplicateBook) {
  const name = (book.series || "").trim();
  const index = (book.series_index || "").trim();
  if (name && index) return `${name} #${index}`;
  if (index) return `#${index}`;
  return name;
}

function formatSize(size: number | null | undefined) {
  if (!size || size <= 0) return "—";
  const mb = size / (1024 * 1024);
  if (mb < 0.01) return "0.01 MB";
  return `${mb.toFixed(2)} MB`;
}

function bySize(a: DuplicateBook, b: DuplicateBook) {
  return (b.size || 0) - (a.size || 0) || a.id - b.id;
}

function isLargest(group: { books: DuplicateBook[] }, book: DuplicateBook) {
  const largest = [...group.books].sort(bySize)[0];
  return largest?.id === book.id;
}

function bookFormat(book: DuplicateBook) {
  return (book.primary_format || "").replace(/^\./, "").trim().toLowerCase();
}

function selectDefaultCopies(list: { books: DuplicateBook[] }[]) {
  const next = new Set<number>();
  for (const group of list) {
    const ranked = [...group.books].sort((a, b) => {
      const rank = (book: DuplicateBook) => {
        const format = bookFormat(book);
        if (format === "epub") return 0;
        if (format && format !== "pdf") return 1;
        if (format === "pdf") return 2;
        return 3;
      };
      return rank(a) - rank(b) || bySize(a, b);
    });
    const keep = ranked[0];
    for (const book of group.books) {
      if (book.id !== keep?.id) next.add(book.id);
    }
  }
  picked.value = next;
}

function groupPicked(group: { books: DuplicateBook[] }) {
  return group.books.filter((book) => picked.value.has(book.id)).length;
}

function toggle(id: number) {
  const next = new Set(picked.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  picked.value = next;
}

async function load(next = page.value) {
  const wanted = Math.max(1, next);
  const data = await api<{ groups: typeof groups.value; group_count: number; book_count: number; page: number }>(
    `/api/books/duplicates?store=${store.value}&page=${wanted}&page_size=${pageSize}`,
  );
  const pages = Math.max(1, Math.ceil(data.group_count / pageSize));
  if (wanted > pages) {
    await load(pages);
    return;
  }
  groups.value = data.groups.map((group) => ({
    ...group,
    books: [...group.books].sort(bySize),
  }));
  groupCount.value = data.group_count;
  bookCount.value = data.book_count;
  page.value = data.page || wanted;
  selectDefaultCopies(groups.value);
  groups.value = groups.value.map((group) => ({
    ...group,
    books: [
      ...group.books.filter((book) => !picked.value.has(book.id)),
      ...group.books.filter((book) => picked.value.has(book.id)),
    ],
  }));
}

async function ignoreGroup(group: { label: string; books: DuplicateBook[] }) {
  error.value = false;
  try {
    await api(`/api/books/duplicates/ignore?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: group.books.map((book) => book.id) }),
    });
    message.value = "已忽视这一组";
    picked.value = new Set([...picked.value].filter((id) => !group.books.some((book) => book.id === id)));
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "忽视失败";
    error.value = true;
  }
  await load();
}

async function removeGroup(group: { books: DuplicateBook[] }) {
  const ids = group.books.map((book) => book.id).filter((id) => picked.value.has(id));
  if (!ids.length) return;
  error.value = false;
  try {
    const job = await api<{ id: number }>(`/api/books/delete?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: ids }),
    });
    const done = await waitJob(job.id);
    message.value = done.message || done.status;
    error.value = done.status === "failed";
    picked.value = new Set([...picked.value].filter((id) => !ids.includes(id)));
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "删除失败";
    error.value = true;
  }
  await load();
}

async function removeBooks() {
  const ids = [...picked.value];
  if (!ids.length) return;
  error.value = false;
  try {
    const job = await api<{ id: number }>(`/api/books/delete?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: ids }),
    });
    const done = await waitJob(job.id);
    message.value = done.message || done.status;
    error.value = done.status === "failed";
    picked.value = new Set();
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "删除失败";
    error.value = true;
  }
  await load();
}

watch(organized, () => {
  picked.value = new Set();
  message.value = "";
  page.value = 1;
  load(1);
});

onMounted(load);
</script>
