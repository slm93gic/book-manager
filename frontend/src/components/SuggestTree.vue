<template>
  <div class="tree panel suggest-tree">
    <div class="panel-head"><h2>推荐分类</h2></div>
    <div class="tree-item" :class="{ active: !selected }" @click="emit('select', '')">
      <strong>全部</strong>
    </div>
    <SuggestNodes :nodes="tree" :selected="selected" :opened="opened" @select="emit('select', $event)" @toggle="toggle" />
    <p v-if="!items.length" class="muted">还没有推荐分类。</p>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, ref, watch } from "vue";

type SuggestItem = { name: string; count: number };
type SuggestNode = SuggestItem & { children: SuggestNode[] };

const props = defineProps<{ items: SuggestItem[]; selected: string }>();
const emit = defineEmits<{ select: [string] }>();
const opened = ref<Record<string, boolean>>({});

const tree = computed(() => buildSuggestTree(props.items));

watch(
  () => props.selected,
  (name) => {
    if (!name) return;
    const path = findPath(tree.value, name);
    if (!path) return;
    const next = { ...opened.value };
    path.slice(0, -1).forEach((node) => {
      next[node.name] = true;
    });
    opened.value = next;
  },
);

function toggle(name: string) {
  opened.value = { ...opened.value, [name]: !opened.value[name] };
}

const SuggestNodes = defineComponent({
  name: "SuggestNodes",
  props: {
    nodes: { type: Array, required: true },
    selected: { type: String, default: "" },
    opened: { type: Object, required: true },
  },
  emits: ["select", "toggle"],
  setup(nodeProps, { emit: nodeEmit }) {
    return () =>
      h(
        "div",
        (nodeProps.nodes as SuggestNode[]).map((node) =>
          h("div", { key: node.name }, [
            h(
              "div",
              {
                class: ["tree-item", { active: nodeProps.selected === node.name }],
                title: `${node.name}（${node.count}）`,
                onClick: () => nodeEmit("select", node.name),
              },
              [
                node.children.length
                  ? h(
                      "button",
                      {
                        class: "tree-toggle",
                        type: "button",
                        title: nodeProps.opened[node.name] ? "收起" : "展开",
                        onClick: (event: Event) => {
                          event.stopPropagation();
                          nodeEmit("toggle", node.name);
                        },
                      },
                      nodeProps.opened[node.name] ? "📂" : "📁",
                    )
                  : h("span", { class: "tree-toggle tree-leaf" }, "📁"),
                h("span", { class: "suggest-name" }, node.name),
                h("span", { class: "suggest-count" }, String(node.count)),
              ],
            ),
            node.children.length && nodeProps.opened[node.name]
              ? h("div", { class: "tree-children" }, [
                  h(SuggestNodes, {
                    nodes: node.children,
                    selected: nodeProps.selected,
                    opened: nodeProps.opened,
                    onSelect: (name: string) => nodeEmit("select", name),
                    onToggle: (name: string) => nodeEmit("toggle", name),
                  }),
                ])
              : null,
          ]),
        ),
      );
  },
});

function codeOf(name: string) {
  const matched = name.match(/^[A-Za-z][A-Za-z0-9.]*/);
  return matched ? matched[0] : "";
}

function buildSuggestTree(items: SuggestItem[]): SuggestNode[] {
  const rows = items.map((item) => ({ ...item, code: codeOf(item.name) }));
  const byCode = new Map<string, string>();
  rows.forEach((row) => {
    if (row.code && !byCode.has(row.code)) byCode.set(row.code, row.name);
  });
  const codes = [...byCode.keys()].sort((a, b) => b.length - a.length);
  const parentOf = new Map<string, string>();
  for (const code of byCode.keys()) {
    let parent = "";
    for (const other of codes) {
      if (other !== code && other.length < code.length && code.startsWith(other) && other.length > parent.length) parent = other;
    }
    if (parent) parentOf.set(code, parent);
  }
  const nodes = new Map<string, SuggestNode>();
  rows.forEach((row) => {
    nodes.set(row.code || row.name, { name: row.name, count: row.count, children: [] });
  });
  const roots: SuggestNode[] = [];
  rows.forEach((row) => {
    const node = nodes.get(row.code || row.name);
    if (!node) return;
    const parent = row.code ? parentOf.get(row.code) : "";
    const parentNode = parent ? nodes.get(parent) : undefined;
    if (parentNode && parentNode !== node) parentNode.children.push(node);
    else roots.push(node);
  });
  const sortNodes = (list: SuggestNode[]) => {
    list.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh"));
    list.forEach((node) => sortNodes(node.children));
  };
  sortNodes(roots);
  return roots;
}

function findPath(nodes: SuggestNode[], name: string, trail: SuggestNode[] = []): SuggestNode[] | null {
  for (const node of nodes) {
    const next = [...trail, node];
    if (node.name === name) return next;
    const found = findPath(node.children, name, next);
    if (found) return found;
  }
  return null;
}
</script>
