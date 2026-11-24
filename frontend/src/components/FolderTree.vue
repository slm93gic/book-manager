<template>
  <div class="tree panel">
    <div class="panel-head"><h2>目录</h2></div>
    <div
      class="tree-item"
      :class="{ active: selectedId === root?.id, over: overId === root?.id }"
      v-if="root"
      @click="$emit('select', root)"
      @dragover.prevent="overId = root.id"
      @dragleave="overId = null"
      @drop.prevent="drop(root)"
    >
      <strong>{{ root.name || (store === "shelf" ? "books" : "书库") }}</strong>
    </div>
    <TreeNodes :nodes="nodes" :selected-id="selectedId" :over-id="overId" @select="$emit('select', $event)" @hover="overId = $event" @drop="drop" />
    <p v-if="!root" class="muted">{{ store === "shelf" ? "books 下还没有分类。" : "扫描之后这里会显示分类目录。" }}</p>
  </div>
</template>

<script setup lang="ts">
import { defineComponent, h, onMounted, ref } from "vue";
import { api, type FolderNode } from "../api";

const props = defineProps<{ selectedId: number | null; store?: string }>();
const emit = defineEmits<{ select: [FolderNode]; dropBooks: [FolderNode] }>();
const root = ref<FolderNode | null>(null);
const nodes = ref<Array<FolderNode & { children?: FolderNode[]; open?: boolean }>>([]);
const overId = ref<number | null>(null);

async function load() {
  try {
    const data = await api<{ items: FolderNode[]; root: FolderNode | null }>(
      `/api/folders?store=${props.store || "library"}`,
    );
    root.value = data.root;
    nodes.value = data.items;
  } catch {
    root.value = null;
    nodes.value = [];
  }
}

function drop(node: FolderNode) {
  overId.value = null;
  emit("dropBooks", node);
}

onMounted(load);
defineExpose({ reload: load });
const folderStore = props.store || "library";

const TreeNodes = defineComponent({
  name: "TreeNodes",
  props: {
    nodes: { type: Array, required: true },
    selectedId: { type: Number, default: null },
    overId: { type: Number, default: null },
  },
  emits: ["select", "hover", "drop"],
  setup(props, { emit: nodeEmit }) {
    const opened = ref<Record<number, FolderNode[]>>({});
    async function toggle(node: FolderNode) {
      if (opened.value[node.id]) {
        delete opened.value[node.id];
        opened.value = { ...opened.value };
        return;
      }
      const data = await api<{ items: FolderNode[] }>(`/api/folders?parent_id=${node.id}&store=${folderStore}`);
      opened.value = { ...opened.value, [node.id]: data.items };
    }
    return () =>
      h(
        "div",
        props.nodes.map((node) =>
          h("div", { key: (node as FolderNode).id }, [
            h(
              "div",
              {
                class: ["tree-item", { active: props.selectedId === (node as FolderNode).id, over: props.overId === (node as FolderNode).id }],
                onClick: () => nodeEmit("select", node),
                onDragover: (event: DragEvent) => {
                  event.preventDefault();
                  nodeEmit("hover", (node as FolderNode).id);
                },
                onDrop: (event: DragEvent) => {
                  event.preventDefault();
                  nodeEmit("drop", node);
                },
              },
              [
                h("button", { class: "ghost", type: "button", onClick: (event: Event) => { event.stopPropagation(); toggle(node as FolderNode); } }, opened.value[(node as FolderNode).id] ? "−" : "+"),
                h("span", (node as FolderNode).name),
                (node as FolderNode).is_classified ? h("span", { class: "tag" }, "已分类") : null,
              ],
            ),
            opened.value[(node as FolderNode).id]
              ? h(TreeNodes, {
                  nodes: opened.value[(node as FolderNode).id],
                  selectedId: props.selectedId,
                  overId: props.overId,
                  onSelect: (child: FolderNode) => nodeEmit("select", child),
                  onHover: (id: number) => nodeEmit("hover", id),
                  onDrop: (child: FolderNode) => nodeEmit("drop", child),
                })
              : null,
          ]),
        ),
      );
  },
});
</script>
