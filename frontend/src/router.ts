import { createRouter, createWebHistory } from "vue-router";
import LoginView from "./views/LoginView.vue";
import LibraryView from "./views/LibraryView.vue";
import JobsView from "./views/JobsView.vue";
import LogView from "./views/LogView.vue";
import SettingsView from "./views/SettingsView.vue";
import DuplicatesView from "./views/DuplicatesView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginView },
    { path: "/", redirect: "/unclassified" },
    { path: "/unclassified", component: LibraryView, props: { unclassified: true } },
    { path: "/classified", component: LibraryView, props: { organized: true } },
    { path: "/duplicates/unclassified", component: DuplicatesView, props: { organized: false } },
    { path: "/duplicates/classified", component: DuplicatesView, props: { organized: true } },
    { path: "/jobs", component: JobsView },
    { path: "/log", component: LogView },
    { path: "/settings", component: SettingsView },
  ],
});

export default router;
