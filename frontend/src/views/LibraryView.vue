<template>
  <section class="page" :class="{ focused: unclassified || organized }">
    <header class="page-head">
      <h1>{{ pageTitle }}</h1>
    </header>
    <div class="toolbar" :class="{ compact: unclassified || organized }">
      <div class="toolbar-search">
        <input class="search" v-model="q" :placeholder="searchPlaceholder" @keyup.enter="load(1)" />
        <select v-model="sort" aria-label="排序" @change="changeSort">
          <option value="name">文件名</option>
          <option value="title">书名</option>
          <option value="author">作者</option>
          <option value="size">大小</option>
          <option value="format">格式</option>
          <option value="path">路径</option>
          <option value="mtime">修改时间</option>
          <option value="series">丛书</option>
        </select>
        <template v-if="!(unclassified || organized)">
          <label class="check"><input type="checkbox" v-model="recursive" /> 包含子目录</label>
          <button class="ghost" @click="load(1)">查询</button>
        </template>
      </div>
      <div v-if="!(unclassified || organized)" class="toolbar-actions">
        <button class="ghost" @click="askFolder">{{ categoryShare ? `在 ${categoryShare} 下新建分类` : "在书库下新建分类" }}</button>
        <button class="ghost" :disabled="!selectedFolder" @click="renameFolder">重命名</button>
        <button class="ghost" :disabled="!selectedFolder" @click="markClassified">标为已分类</button>
        <button class="danger" :disabled="!selectedFolder" @click="removeFolder">删除空目录</button>
        <span class="spacer"></span>
        <button class="primary" :disabled="!picked.size" @click="askMove">移动到…{{ picked.size ? `（${picked.size}）` : "" }}</button>
        <button class="danger" :disabled="!picked.size" @click="removeBooks">删除所选</button>
        <button class="danger" :disabled="!picked.size" @click="renamePicked">重命名标记</button>
        <button class="ghost" @click="scan">扫描书库</button>
        <button class="danger" @click="clearRecords">清空扫描记录</button>
      </div>
      <div v-else class="toolbar-actions">
        <button class="primary" :disabled="!picked.size" @click="askMove">移动到…{{ picked.size ? `（${picked.size}）` : "" }}</button>
        <button class="danger" :disabled="!picked.size" @click="removeBooks">删除所选</button>
        <span class="muted picked-count">已选 {{ picked.size }} / {{ total }} 本</span>
        <details ref="moreBox" class="more">
          <summary class="ghost">更多</summary>
          <div class="more-menu">
            <button class="ghost" @click="runMore(() => load(1))">查询</button>
            <label v-if="organized" class="check"><input type="checkbox" v-model="recursive" /> 包含子目录</label>
            <button class="ghost" @click="runMore(askFolder)">{{ categoryShare ? `在 ${categoryShare} 下新建分类` : "在书库下新建分类" }}</button>
            <button v-if="organized" class="ghost" :disabled="!selectedFolder" @click="runMore(renameFolder)">重命名</button>
            <button v-if="organized" class="ghost" :disabled="!selectedFolder" @click="runMore(markClassified)">标为已分类</button>
            <button v-if="organized" class="danger" :disabled="!selectedFolder" @click="runMore(removeFolder)">删除空目录</button>
            <button class="ghost" :disabled="!books.length" @click="runMore(selectPage)">全选本页</button>
            <button class="ghost" :disabled="!total" @click="runMore(selectResults)">全选当前结果</button>
            <button class="ghost" :disabled="!picked.size" @click="runMore(clearPicked)">取消选择</button>
            <button class="ghost" :disabled="!picked.size" @click="runMore(askTags)">修改标签</button>
            <button class="ghost" :disabled="!picked.size" @click="runMore(writeTags)">回写标签</button>
            <button class="danger" :disabled="!picked.size" @click="runMore(renamePicked)">重命名标记</button>
            <button class="ghost" @click="runMore(scan)">扫描书库</button>
            <button class="danger" @click="runMore(clearRecords)">清空扫描记录</button>
          </div>
        </details>
      </div>
    </div>
    <p v-if="message" :class="error ? 'error' : 'muted'">{{ message }}</p>
    <div class="split" :class="{ solo: !unclassified && !organized }">
      <SuggestTree v-if="unclassified" :items="suggestions" :selected="suggest" @select="chooseSuggest" />
      <FolderTree v-else :key="route.path" ref="tree" :store="store" :selected-id="selectedFolder?.id ?? null" @select="choose" @drop-books="onDrop" />
      <div class="panel list">
        <div v-if="!(unclassified || organized)" class="row list-tools">
          <span class="muted">已选 {{ picked.size }} / {{ total }} 本</span>
          <button class="ghost" :disabled="!books.length" @click="selectPage">全选本页</button>
          <button class="ghost" :disabled="!total" @click="selectResults">全选当前结果</button>
          <button class="ghost" :disabled="!picked.size" @click="clearPicked">取消选择</button>
          <button class="ghost" :disabled="!picked.size" @click="askTags">修改标签</button>
          <button class="ghost" :disabled="!picked.size" @click="writeTags">回写标签</button>
        </div>
        <template v-if="unclassified || organized">
          <div class="lib-select">
            <label class="check"><input ref="pageBox" type="checkbox" title="全选本页" :checked="pageChecked" @change="togglePage" /> 全选本页</label>
          </div>
          <div class="lib-cards poster-cards">
            <article
              v-for="book in books"
              :key="book.id"
              class="poster-card"
              :class="{ picked: picked.has(book.id), conflict: book.name_conflict, pending: !hasFormat(book.primary_format) }"
              draggable="true"
              @dragstart="drag($event, book.id)"
              @click="toggle(book.id)"
            >
              <span class="poster-cover">
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
                <span class="poster-detail">
                  <span class="fact-size">大小 {{ formatSize(book.size) }}</span>
                  <span class="fact-format" :class="{ 'pending-format': !hasFormat(book.primary_format) }">格式 {{ hasFormat(book.primary_format) ? formatLabel(book.primary_format) : "待删除" }}</span>
                  <span class="fact-author">作者 {{ book.authors_text || "—" }}</span>
                  <span v-if="book.series" class="poster-series">{{ book.series }} #{{ book.series_index }}</span>
                  <span v-if="book.suggested_category" class="poster-suggest">推荐 {{ book.suggested_category }}</span>
                  <span class="poster-path">{{ book.relative_path }}</span>
                </span>
              </span>
              <span class="poster-name">
                <button class="book-title" :class="{ bad: book.title_issue === 'brackets' }" :title="book.title || '未命名'" @click.stop="detailId = book.id">{{ book.title || "未命名" }}</button>
                <span v-if="book.name_conflict" class="mark">同名</span>
                <span v-if="book.title_issue === 'brackets'" class="mark bad">《》</span>
                <button v-if="book.title_issue === 'brackets'" type="button" class="rename-one" @click.stop="renameMarked([book])">重命名</button>
              </span>
            </article>
          </div>
        </template>
        <table v-else class="books">
          <colgroup>
            <col class="col-check" />
            <col class="col-cover" />
            <col class="col-title" />
            <col class="col-author" />
            <col class="col-size" />
            <col class="col-format" />
            <col class="col-path" />
          </colgroup>
          <thead>
            <tr>
              <th><input ref="pageBox" type="checkbox" title="全选本页" :checked="pageChecked" @change="togglePage" /></th>
              <th>封面</th>
              <th><button type="button" class="sort-head" :class="{ on: sort === 'title' }" @click="toggleSort('title')">书名{{ mark('title') }}</button></th>
              <th><button type="button" class="sort-head" :class="{ on: sort === 'author' }" @click="toggleSort('author')">作者{{ mark('author') }}</button></th>
              <th><button type="button" class="sort-head" :class="{ on: sort === 'size' }" @click="toggleSort('size')">大小{{ mark('size') }}</button></th>
              <th><button type="button" class="sort-head" :class="{ on: sort === 'format' }" @click="toggleSort('format')">格式{{ mark('format') }}</button></th>
              <th><button type="button" class="sort-head" :class="{ on: sort === 'path' }" @click="toggleSort('path')">路径{{ mark('path') }}</button></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="book in books" :key="book.id" class="book-row" :class="{ conflict: book.name_conflict, 'bad-title': book.title_issue }" draggable="true" @dragstart="drag($event, book.id)">
              <td><input type="checkbox" :checked="picked.has(book.id)" @change="toggle(book.id)" /></td>
              <td class="cover-cell">
                <img
                  v-if="!hiddenCovers[book.id]"
                  class="cover"
                  :src="`/api/covers/${book.id}?store=${store}`"
                  alt=""
                  loading="lazy"
                  @error="hiddenCovers[book.id] = true"
                />
                <span v-else class="cover placeholder">无封面</span>
              </td>
              <td>
                <button class="book-title" :class="{ bad: book.title_issue }" @click="detailId = book.id">{{ book.title || "未命名" }}</button>
                <span v-if="book.name_conflict" class="mark">同名</span>
                <span v-if="book.title_issue" class="mark bad">{{ book.title_issue === "pinyin" ? "拼音" : "《》" }}</span>
                <button v-if="book.title_issue" type="button" class="rename-one" @click="renameMarked([book])">重命名</button>
                <div class="muted" v-if="book.series">{{ book.series }} #{{ book.series_index }}</div>
              </td>
              <td class="clip">{{ book.authors_text || "—" }}</td>
              <td class="clip">{{ formatSize(book.size) }}</td>
              <td class="clip">{{ formatLabel(book.primary_format) }}</td>
              <td class="path">{{ book.relative_path }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!books.length" class="empty">这个范围里没有图书。</p>
        <div v-if="(listingQuery || listingSuggest) && books.length >= total" class="pager">
          <span>共 {{ total }} 本</span>
        </div>
        <div v-else class="pager">
          <button class="ghost" :disabled="page <= 1" @click="load(page - 1)">上一页</button>
          <span>第 {{ page }} 页 / 共 {{ total }} 本</span>
          <button class="ghost" :disabled="page * pageSize >= total" @click="load(page + 1)">下一页</button>
        </div>
      </div>
    </div>
    <BookDrawer :book-id="detailId" :store="store" @close="detailId = null" @saved="load(page)" />
    <div v-if="dialog" class="modal-back" @click.self="dialog = ''">
      <div class="modal">
        <h2>{{ dialogTitle }}</h2>
        <p v-if="dialog === 'move'">把选中的 {{ moveIds.length }} 本图书从书库手动移到 {{ categoryShare || "书库" }} 下，例如 {{ categoryShare || "书库" }}/小说。</p>
        <p v-else-if="dialog === 'tags'" class="muted">只改选中图书的标签，并把改过的标签写回各自文件夹的 metadata.opf。书名、作者和其他内容不动。没有该文件会新建。</p>
        <p v-else class="muted">分类文件夹建在 {{ categoryShare || "书库" }} 下，不会建到某一本书里面。</p>
        <template v-if="dialog === 'tags'">
          <label class="field">方式
            <select v-model="tagMode">
              <option value="add">添加标签</option>
              <option value="remove">移除标签</option>
              <option value="replace">替换为这些标签</option>
            </select>
          </label>
          <label class="field">标签<input v-model="tagInput" placeholder="多个标签用顿号或逗号分隔，例如 小说、历史" /></label>
        </template>
        <template v-else-if="dialog === 'move'">
          <label class="field">已有分类
            <button type="button" class="category-trigger" @click="openCategoryPanel">
              {{ chosenCategoryName && !newCategory.trim() ? chosenCategoryName : "请选择" }}
            </button>
          </label>
          <label class="field">或在 {{ categoryShare || "书库" }} 下新建并移入<input v-model="newCategory" placeholder="例如 小说" /></label>
          <p v-if="newCategory.trim()" class="muted">将新建「{{ newCategory.trim() }}」并移入。</p>
        </template>
        <input v-else v-model="dialogValue" placeholder="分类名称，例如 小说" />
        <p v-if="dialog === 'move' && moveIds.length > threshold">数量超过 {{ threshold }}，确认后才会移动。</p>
        <div class="row" style="margin-top: 12px">
          <button class="primary" @click="confirmDialog">确定</button>
          <button class="ghost" @click="dialog = ''">取消</button>
        </div>
      </div>
    </div>
    <div v-if="categoryPanel" class="modal-back category-panel-back" @click.self="categoryPanel = false">
      <div class="modal category-panel">
        <h2>选择分类</h2>
        <label class="field">
          <input v-model="categoryQuery" placeholder="搜索分类，例如 小说" />
          <span class="muted">{{ categoryHint }}</span>
        </label>
        <div ref="categoryList" class="category-cards" role="listbox">
          <button
            v-for="folder in filteredCategories"
            :key="folder.id"
            type="button"
            class="category-card"
            :class="{ on: dialogValue === folder.relative_path }"
            :style="{ background: categoryFill(folder.relative_path) }"
            :data-path="folder.relative_path"
            role="option"
            :aria-selected="dialogValue === folder.relative_path"
            @click="chooseCategory(folder.relative_path)"
          >
            <span>{{ folder.name }}</span>
          </button>
          <p v-if="!filteredCategories.length" class="muted">没有匹配的分类</p>
        </div>
        <div class="row" style="margin-top: 12px">
          <button class="ghost" type="button" @click="categoryPanel = false">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import BookDrawer from "../components/BookDrawer.vue";
import FolderTree from "../components/FolderTree.vue";
import SuggestTree from "../components/SuggestTree.vue";
import { api, ApiError, waitJob, type BookRow, type FolderNode } from "../api";

const props = defineProps<{ unclassified?: boolean; organized?: boolean }>();
const route = useRoute();
const unclassified = ref(!!props.unclassified || route.path === "/unclassified");
const organized = ref(!!props.organized || route.path === "/classified");
const pageTitle = computed(() => (organized.value ? "已整理" : unclassified.value ? "未分类" : "书库"));
const searchPlaceholder = computed(() =>
  unclassified.value ? "搜索书名、作者、标签、路径、推荐分类" : "搜索书名、作者、标签、路径",
);
const store = computed(() => (organized.value ? "shelf" : "library"));
const books = ref<BookRow[]>([]);
const hiddenCovers = reactive<Record<number, boolean>>({});
const total = ref(0);
const page = ref(1);
const pageSize = 40;
const listingQuery = ref("");
const listingSuggest = ref("");
const q = ref("");
const suggest = ref("");
const suggestions = ref<{ name: string; count: number }[]>([]);
const sort = ref("name");
const order = ref("asc");
const recursive = ref(false);
const selectedFolder = ref<FolderNode | null>(null);
const picked = ref(new Set<number>());
const pageBox = ref<HTMLInputElement | null>(null);
const pageChecked = computed(() => books.value.length > 0 && books.value.every((book) => picked.value.has(book.id)));
const pagePartial = computed(() => books.value.some((book) => picked.value.has(book.id)) && !pageChecked.value);
const detailId = ref<number | null>(null);
const message = ref("");
const error = ref(false);
function hasFormat(value: string | null | undefined) {
  return !!(value || "").replace(/^\./, "").trim();
}

function formatLabel(value: string | null | undefined) {
  const text = (value || "").replace(/^\./, "").trim();
  return text ? text.toUpperCase() : "—";
}

function formatSize(size: number | null | undefined) {
  if (!size || size <= 0) return "—";
  const mb = size / (1024 * 1024);
  if (mb < 0.01) return "0.01 MB";
  return `${mb.toFixed(2)} MB`;
}
const threshold = ref(50);
const moreBox = ref<HTMLDetailsElement | null>(null);
function runMore(action: () => void) {
  if (moreBox.value) moreBox.value.open = false;
  action();
}
const tree = ref<{ reload: () => Promise<void> } | null>(null);
const dialog = ref("");
const dialogTitle = ref("");
const dialogValue = ref("");
const newCategory = ref("");
const categories = ref<FolderNode[]>([]);
const categoryQuery = ref("");
const categoryPanel = ref(false);
const categoryList = ref<HTMLElement | null>(null);
const filteredCategories = computed(() => {
  const query = categoryQuery.value.trim().toLowerCase();
  if (!query) return categories.value;
  return categories.value.filter((folder) =>
    folder.name.toLowerCase().includes(query) || folder.relative_path.toLowerCase().includes(query),
  );
});
const categoryHint = computed(() => {
  const totalCount = categories.value.length;
  const shown = filteredCategories.value.length;
  if (!categoryQuery.value.trim()) return `共 ${totalCount} 个`;
  return `找到 ${shown} 个 / 共 ${totalCount} 个`;
});
const chosenCategoryName = computed(
  () => categories.value.find((folder) => folder.relative_path === dialogValue.value)?.name || "",
);
const classifyShare = ref("");
const smbHost = ref("");
const categoryShare = computed(() => classifyShare.value || (smbHost.value ? "books" : ""));
const moveIds = ref<number[]>([]);
const tagMode = ref("add");
const tagInput = ref("");

watch(() => route.path, () => {
  unclassified.value = route.path === "/unclassified";
  organized.value = route.path === "/classified";
  selectedFolder.value = null;
  picked.value = new Set();
  suggest.value = "";
  dialog.value = "";
  categoryPanel.value = false;
  load(1);
});

watch(dialog, (value) => {
  if (value !== "move") categoryPanel.value = false;
});

watch([pageChecked, pagePartial, books], () => {
  if (pageBox.value) pageBox.value.indeterminate = pagePartial.value;
});

watch(recursive, () => load(1));

function queryParams(nextPage?: number) {
  const params = new URLSearchParams({
    sort: sort.value,
    order: order.value,
    q: q.value,
  });
  if (unclassified.value && suggest.value) params.set("suggest", suggest.value);
  if (nextPage) {
    params.set("page", String(nextPage));
    params.set("page_size", String(pageSize));
  }
  params.set("store", store.value);
  if (selectedFolder.value) params.set("folder_id", String(selectedFolder.value.id));
  if (recursive.value) params.set("recursive", "1");
  return params;
}

function changeSort() {
  order.value = sort.value === "size" || sort.value === "mtime" ? "desc" : "asc";
  load(1);
}

function toggleSort(key: string) {
  if (sort.value === key) order.value = order.value === "asc" ? "desc" : "asc";
  else {
    sort.value = key;
    order.value = key === "size" || key === "mtime" ? "desc" : "asc";
  }
  load(1);
}

function mark(key: string) {
  if (sort.value !== key) return "";
  return order.value === "desc" ? " ↓" : " ↑";
}

function choose(node: FolderNode) {
  selectedFolder.value = node;
  load(1);
}

function chooseSuggest(name: string) {
  suggest.value = name;
  picked.value = new Set();
  load(1);
}

let listRequest = 0;

async function load(next = page.value) {
  const request = ++listRequest;
  const wanted = store.value;
  page.value = next;
  const data = await api<{ items: BookRow[]; total: number; suggestions?: { name: string; count: number }[] }>(`/api/books?${queryParams(page.value)}`);
  if (request !== listRequest || store.value !== wanted) return;
  books.value = data.items;
  total.value = data.total;
  if (unclassified.value) suggestions.value = data.suggestions || [];
  listingQuery.value = q.value.trim();
  listingSuggest.value = unclassified.value ? suggest.value : "";
  const refreshSelection = unclassified.value || organized.value;
  const selected = refreshSelection ? new Set<number>() : new Set(picked.value);
  for (const book of data.items) {
    if (!hasFormat(book.primary_format)) selected.add(book.id);
  }
  picked.value = selected;
  if (refreshSelection) {
    moveIds.value = [];
    if (dialog.value === "move") dialog.value = "";
  }
}

function selectPage() {
  const next = new Set(picked.value);
  for (const book of books.value) next.add(book.id);
  picked.value = next;
}

function togglePage(event: Event) {
  const on = (event.target as HTMLInputElement).checked;
  const next = new Set(picked.value);
  for (const book of books.value) {
    if (on) next.add(book.id);
    else next.delete(book.id);
  }
  picked.value = next;
}

function clearPicked() {
  picked.value = new Set();
}

async function selectResults() {
  const data = await api<{ ids: number[]; total: number }>(`/api/books/ids?${queryParams()}`);
  picked.value = new Set(data.ids);
  message.value = `已选当前结果 ${data.total} 本`;
  error.value = false;
}

function askTags() {
  if (!picked.value.size) return;
  dialog.value = "tags";
  dialogTitle.value = `修改 ${picked.value.size} 本的标签`;
  tagMode.value = "add";
  tagInput.value = "";
}

function tagNames(value: string) {
  return value.split(/[、,，]/).map((item) => item.trim()).filter(Boolean);
}

async function confirmTags() {
  const tags = tagNames(tagInput.value);
  if (tagMode.value !== "remove" && !tags.length) {
    message.value = "请填写要修改的标签";
    error.value = true;
    return;
  }
  const ids = [...picked.value];
  const mode = tagMode.value;
  dialog.value = "";
  await act(async () => {
    const result = await api<{ updated: number; written: number; failed: { error: string }[] }>(`/api/books/tags?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: ids, mode, tags }),
    });
    const failed = result.failed?.length || 0;
    if (!result.updated) {
      message.value = "这些书的标签没有变化，未回写";
      error.value = false;
      return;
    }
    message.value = failed
      ? `已修改 ${result.updated} 本，回写 ${result.written} 本，失败 ${failed} 本`
      : `已修改 ${result.updated} 本，并回写 ${result.written} 本`;
    error.value = failed > 0;
  });
  await load(page.value);
}

async function writeTags() {
  if (!picked.value.size) return;
  if (!window.confirm(`把选中的 ${picked.value.size} 本标签写回各自文件夹的 metadata.opf？没有该文件会新建。`)) return;
  await act(async () => {
    const result = await api<{ written: number; failed: { error: string }[] }>(`/api/books/write-tags?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: [...picked.value] }),
    });
    const failed = result.failed?.length || 0;
    message.value = failed ? `已回写 ${result.written} 本，失败 ${failed} 本` : `已回写 ${result.written} 本`;
    error.value = failed > 0;
  });
}

async function renamePicked() {
  if (!picked.value.size) return;
  if (!window.confirm(`检查选中的 ${picked.value.size} 本，把书名含《》或拼音的改掉，回写书名和标签，并在名称不长时重命名文件夹？`)) return;
  await act(async () => {
    const result = await api<{ renamed: number; written: number; failed: { error: string }[] }>(`/api/books/rename-titles?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: [...picked.value] }),
    });
    const failed = result.failed?.length || 0;
    if (!result.renamed && !result.written && !failed) {
      message.value = "选中的书里没有带《》或拼音的书名";
      error.value = true;
      return;
    }
    message.value = failed
      ? `已改 ${result.renamed} 本，回写 ${result.written} 本，失败 ${failed} 本`
      : `已改 ${result.renamed} 本，并回写 ${result.written} 本`;
    error.value = failed > 0;
  });
  await load(page.value);
}

async function renameMarked(rows: BookRow[]) {
  const marked = rows.filter((book) => book.title_issue);
  if (!marked.length) {
    message.value = "选中的书里没有带《》或拼音的书名";
    error.value = true;
    return;
  }
  const preview = marked.length === 1
    ? `把「${marked[0].title || "未命名"}」改为「${marked[0].title_suggestion}」，回写书名和标签，并在名称不长时重命名文件夹？`
    : `把选中的 ${marked.length} 本带《》或拼音的书改名，回写书名和标签，并在名称不长时重命名文件夹？`;
  if (!window.confirm(preview)) return;
  await act(async () => {
    const result = await api<{ renamed: number; written: number; failed: { error: string }[] }>(`/api/books/rename-titles?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: marked.map((book) => book.id) }),
    });
    const failed = result.failed?.length || 0;
    message.value = failed
      ? `已改 ${result.renamed} 本，回写 ${result.written} 本，失败 ${failed} 本`
      : `已改 ${result.renamed} 本，并回写 ${result.written} 本`;
    error.value = failed > 0;
  });
  await load(page.value);
}

async function removeBooks() {
  if (!picked.value.size) return;
  await act(async () => {
    const job = await api<{ id: number }>(`/api/books/delete?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: [...picked.value] }),
    });
    const done = await waitJob(job.id);
    message.value = done.message || done.status;
    error.value = done.status === "failed";
    picked.value = new Set();
  });
  await tree.value?.reload();
  await load(page.value);
}

async function clearRecords() {
  if (!window.confirm("清空本机扫描记录？网盘上的图书文件会保留，清空后需要重新扫描。")) return;
  await act(async () => {
    const result = await api<{ books: number }>(`/api/scan/clear?store=${store.value}`, { method: "POST" });
    picked.value = new Set();
    selectedFolder.value = null;
    message.value = `已清空 ${result.books} 本扫描记录`;
    error.value = false;
  });
  await tree.value?.reload();
  await load(1);
}

function toggle(id: number) {
  const next = new Set(picked.value);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  picked.value = next;
}

const dragging = ref<number[]>([]);

function drag(event: DragEvent, id: number) {
  const ids = picked.value.has(id) ? [...picked.value] : [id];
  dragging.value = ids;
  event.dataTransfer?.setData("text/plain", ids.join(","));
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
}

async function onDrop(node: FolderNode) {
  const ids = dragging.value.length ? dragging.value : [...picked.value];
  await move(ids, node.relative_path);
}

async function askMove() {
  dialog.value = "move";
  dialogTitle.value = categoryShare.value ? `移动到 ${categoryShare.value}` : "移动到书库下的分类";
  const hint = suggestedTarget();
  dialogValue.value = "";
  newCategory.value = "";
  categoryQuery.value = "";
  moveIds.value = [...picked.value];
  const data = await api<{ items: FolderNode[] }>("/api/folders?store=shelf");
  categories.value = data.items;
  dialogValue.value = folderForSuggestion(hint, data.items);
  categoryPanel.value = false;
}

const warmTones = [
  { hue: 8, sat: 74, light: 46 },
  { hue: 18, sat: 78, light: 42 },
  { hue: 28, sat: 76, light: 38 },
  { hue: 36, sat: 80, light: 34 },
  { hue: 348, sat: 70, light: 46 },
  { hue: 336, sat: 68, light: 44 },
];
const coolTones = [
  { hue: 152, sat: 58, light: 32 },
  { hue: 168, sat: 70, light: 30 },
  { hue: 188, sat: 78, light: 30 },
  { hue: 204, sat: 74, light: 40 },
  { hue: 222, sat: 72, light: 46 },
  { hue: 262, sat: 64, light: 46 },
];

function categoryPaint(tone: { hue: number; sat: number; light: number }, top: number) {
  const bottom = Math.max(22, Math.round(top - 8));
  const deep = Math.min(88, tone.sat + 8);
  return `linear-gradient(165deg, hsl(${tone.hue} ${tone.sat}% ${Math.round(top)}%), hsl(${tone.hue} ${deep}% ${bottom}%))`;
}

const categoryFills = computed(() => {
  const fills = new Map<string, string>();
  const groups = new Map<string, string[]>();
  for (const folder of categories.value) {
    const name = folder.name || "";
    const mark = name.indexOf("_");
    if (mark <= 0) continue;
    const key = name.slice(0, mark);
    const paths = groups.get(key) || [];
    paths.push(folder.relative_path);
    groups.set(key, paths);
  }
  const toneOf = new Map<string, { hue: number; sat: number; light: number }>();
  let warmCount = 0;
  let coolCount = 0;
  let wantWarm = false;
  const takeTone = () => {
    const tone = wantWarm ? warmTones[warmCount++ % warmTones.length] : coolTones[coolCount++ % coolTones.length];
    wantWarm = !wantWarm;
    return tone;
  };
  for (const folder of categories.value) {
    const name = folder.name || "";
    const mark = name.indexOf("_");
    const id = mark > 0 ? `g:${name.slice(0, mark)}` : `p:${folder.relative_path}`;
    if (!toneOf.has(id)) toneOf.set(id, takeTone());
  }
  for (const folder of categories.value) {
    const name = folder.name || "";
    if (name.indexOf("_") > 0) continue;
    const tone = toneOf.get(`p:${folder.relative_path}`) || coolTones[0];
    fills.set(folder.relative_path, categoryPaint(tone, tone.light));
  }
  for (const [key, paths] of groups) {
    const tone = toneOf.get(`g:${key}`) || warmTones[0];
    const floor = Math.max(24, tone.light - 12);
    paths.forEach((path, index) => {
      const step = paths.length <= 1 ? 0 : index / (paths.length - 1);
      fills.set(path, categoryPaint(tone, tone.light - step * (tone.light - floor)));
    });
  }
  return fills;
});

function categoryFill(path: string) {
  return categoryFills.value.get(path) || categoryPaint(coolTones[4], 46);
}

function openCategoryPanel() {
  categoryQuery.value = "";
  categoryPanel.value = true;
  scrollChosenCategory();
}

function chooseCategory(path: string) {
  dialogValue.value = path;
  newCategory.value = "";
  categoryPanel.value = false;
}

function scrollChosenCategory() {
  nextTick(() => {
    const root = categoryList.value;
    const chosen = dialogValue.value;
    if (!root || !chosen) return;
    const hit = [...root.querySelectorAll<HTMLButtonElement>(".category-option")].find((node) => node.dataset.path === chosen);
    hit?.scrollIntoView({ block: "nearest" });
  });
}

function folderForSuggestion(suggestion: string, folders: FolderNode[]) {
  if (!suggestion) return "";
  if (folders.some((folder) => folder.relative_path === suggestion)) return suggestion;
  const name = suggestion.replace(/^[A-Za-z0-9.]+(?:\.[0-9]+)*\s+/, "").trim();
  const parts = name.split(/[、，,]/).map((item) => item.trim()).filter(Boolean);
  const names = new Set(parts);
  const matched = folders.find((folder) => names.has(folder.name) || names.has(folder.relative_path));
  return matched?.relative_path || "";
}

function suggestedTarget() {
  if (!unclassified.value) return "";
  if (suggest.value) return suggest.value;
  const chosen = books.value.filter((book) => picked.value.has(book.id));
  const names = new Set(chosen.map((book) => book.suggested_category).filter(Boolean));
  if (chosen.length === picked.value.size && names.size === 1 && chosen.every((book) => book.suggested_category)) {
    return [...names][0] || "";
  }
  return "";
}

function askFolder() {
  dialog.value = "folder";
  dialogTitle.value = categoryShare.value ? `在 ${categoryShare.value} 下新建分类` : "在书库下新建分类";
  dialogValue.value = "";
}

async function confirmDialog() {
  if (dialog.value === "tags") {
    await confirmTags();
    return;
  }
  if (dialog.value === "folder") {
    await act(() =>
      api("/api/folders?store=shelf", {
        method: "POST",
        body: JSON.stringify({ parent_id: null, name: dialogValue.value }),
      }),
    );
    if (error.value) return;
    dialog.value = "";
    await tree.value?.reload();
    return;
  }
  if (dialog.value === "move") {
    const fresh = newCategory.value.trim();
    let target = dialogValue.value.trim();
    if (fresh) {
      try {
        const created = await api<{ relative_path: string }>("/api/folders?store=shelf", {
          method: "POST",
          body: JSON.stringify({ parent_id: null, name: fresh }),
        });
        target = created.relative_path;
      } catch (err) {
        message.value = err instanceof ApiError ? err.message : "操作失败";
        error.value = true;
        return;
      }
    }
    if (!target) {
      message.value = "请选择分类，或填写新分类名，例如 小说";
      error.value = true;
      return;
    }
    dialog.value = "";
    await move(moveIds.value, target, true);
  }
}

async function move(ids: number[], target: string, confirmed = false) {
  if (!ids.length) return;
  try {
    const job = await api<{ id: number }>(`/api/moves?store=${store.value}`, {
      method: "POST",
      body: JSON.stringify({ book_ids: ids, target, confirmed: confirmed || ids.length <= threshold.value }),
    });
    const done = await waitJob(job.id);
    message.value = done.message || done.status;
    error.value = done.status === "failed";
    picked.value = new Set();
    await tree.value?.reload();
    await load(page.value);
  } catch (err) {
    if (err instanceof ApiError && err.status === 409 && (err.detail as { need_confirm?: boolean })?.need_confirm) {
      const data = await api<{ items: FolderNode[] }>("/api/folders?store=shelf");
      categories.value = data.items;
      categoryQuery.value = "";
      dialog.value = "move";
      dialogTitle.value = "确认批量移动";
      dialogValue.value = target;
      newCategory.value = "";
      moveIds.value = ids;
      scrollChosenCategory();
      return;
    }
    message.value = err instanceof ApiError ? err.message : "移动失败";
    error.value = true;
  }
}

async function renameFolder() {
  const name = window.prompt("新的目录名", selectedFolder.value?.name || "");
  if (!name || !selectedFolder.value) return;
  await act(() => api(`/api/folders/${selectedFolder.value!.id}?store=${store.value}`, { method: "PATCH", body: JSON.stringify({ name }) }));
  await tree.value?.reload();
}

async function markClassified() {
  if (!selectedFolder.value) return;
  await act(() =>
    api(`/api/folders/${selectedFolder.value!.id}?store=${store.value}`, {
      method: "PATCH",
      body: JSON.stringify({ is_classified: true, cascade: true }),
    }),
  );
  await tree.value?.reload();
  await load(page.value);
}

async function removeFolder() {
  if (!selectedFolder.value) return;
  await act(() => api(`/api/folders/${selectedFolder.value!.id}?store=${store.value}`, { method: "DELETE" }));
  selectedFolder.value = null;
  await tree.value?.reload();
}

async function scan() {
  await act(async () => {
    const job = await api<{ id: number }>("/api/jobs/scan", { method: "POST" });
    message.value = "正在扫描，已入库的书可以先搜索";
    const done = await waitJob(job.id);
    message.value = done.message || done.status;
    error.value = done.status !== "completed";
  });
  await tree.value?.reload();
  await load(1);
}

async function act(fn: () => Promise<unknown>) {
  error.value = false;
  try {
    await fn();
  } catch (err) {
    message.value = err instanceof ApiError ? err.message : "操作失败";
    error.value = true;
  }
}

onMounted(async () => {
  const settings = await api<{ batch_threshold: number; classify_share?: string; smb_host?: string }>("/api/settings");
  threshold.value = settings.batch_threshold || 50;
  classifyShare.value = settings.classify_share || "";
  smbHost.value = settings.smb_host || "";
  await load(1);
});
</script>
