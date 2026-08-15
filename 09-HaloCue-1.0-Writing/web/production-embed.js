(() => {
  "use strict";

  const hostId = "productionModule";
  let loadPromise = null;
  let previousChrome = null;

  const sleep = (delay) => new Promise(resolve => setTimeout(resolve, delay));
  const app = () => document.querySelector("#app");
  const host = () => document.querySelector(`#${hostId}`);

  function linkedContext(trigger = null) {
    const linked = trigger?.matches?.("[data-open-production]")
      ? trigger
      : document.querySelector("[data-open-production]");
    const params = new URLSearchParams(location.search);
    return {
      runId: linked?.dataset.openProduction || params.get("run_id") || "",
      workId: linked?.dataset.workId || params.get("work_id") || "",
      releaseId: linked?.dataset.releaseId || params.get("release_id") || "",
    };
  }

  function updateUrl(context, replace = false) {
    const url = new URL(location.href);
    url.pathname = "/";
    url.search = "";
    url.searchParams.set("section", "production");
    if (context.runId) url.searchParams.set("run_id", context.runId);
    if (context.workId) url.searchParams.set("work_id", context.workId);
    if (context.releaseId) url.searchParams.set("release_id", context.releaseId);
    history[replace ? "replaceState" : "pushState"]({ section: "production", ...context }, "", url);
  }

  function setOuterChrome(context) {
    const crumb = document.querySelector("#crumb");
    const workTitle = document.querySelector("#workTitle")?.textContent?.trim();
    const save = document.querySelector("#saveStatus");
    if (!previousChrome) {
      previousChrome = {
        crumb: crumb?.textContent || "",
        save: save?.textContent || "",
        saveState: save?.dataset.state || "",
      };
    }
    if (crumb) crumb.textContent = `${workTitle && workTitle !== "尚未建立作品" ? workTitle : "当前作品"} / AA 制作`;
    if (save) {
      save.textContent = context.runId ? `制作任务 ${context.runId}` : "选择制作任务";
      save.dataset.state = "saved";
    }
    document.querySelectorAll("[data-section]").forEach(item => {
      item.classList.toggle("active", item.dataset.section === "production");
    });
  }

  function installOuterActions(root) {
    const topActions = document.querySelector("#app > .topbar .top-actions");
    if (!topActions) return;
    topActions.querySelector(".production-top-actions")?.remove();
    const controls = document.createElement("span");
    controls.className = "production-top-actions";
    controls.innerHTML = `
      <button type="button" class="quiet production-assets" data-production-proxy="openAssetLibrary">制作素材</button>
      <button type="button" class="quiet production-overview" data-production-proxy="openRunOverview">任务总览</button>
      <button type="button" class="quiet production-refresh" data-production-proxy="refreshRun" title="刷新制作任务" aria-label="刷新制作任务">↻</button>`;
    controls.addEventListener("click", event => {
      const button = event.target.closest("[data-production-proxy]");
      if (!button) return;
      root.querySelector(`#${button.dataset.productionProxy}`)?.click();
    });
    topActions.prepend(controls);
  }

  function restoreOuterChrome() {
    if (!previousChrome) return;
    const crumb = document.querySelector("#crumb");
    const save = document.querySelector("#saveStatus");
    if (crumb) crumb.textContent = previousChrome.crumb;
    if (save) {
      save.textContent = previousChrome.save;
      save.dataset.state = previousChrome.saveState;
    }
    document.querySelector(".production-top-actions")?.remove();
    previousChrome = null;
  }

  function ensureHost() {
    let element = host();
    if (element) return element;
    element = document.createElement("section");
    element.id = hostId;
    element.className = "production-module-host";
    element.setAttribute("aria-label", "AA 制作工作面");
    element.setAttribute("aria-busy", "true");
    element.hidden = true;
    app()?.append(element);
    return element;
  }

  function stylesheet(href) {
    const link = document.createElement("link");
    const loaded = new Promise((resolve, reject) => {
      link.rel = "stylesheet";
      link.href = href;
      link.onload = resolve;
      link.onerror = () => reject(new Error(`无法载入制作样式：${href}`));
    });
    loaded.link = link;
    return loaded;
  }

  async function loadProductionSurface() {
    const element = ensureHost();
    const root = element.shadowRoot || element.attachShadow({ mode: "open" });
    const loading = document.createElement("div");
    loading.className = "production-embed-loading";
    loading.textContent = "正在连接 AA 制作后端...";
    root.replaceChildren(loading);

    const response = await fetch("/production/", { headers: { Accept: "text/html" } });
    if (!response.ok) throw new Error(`AA 制作前端不可用（${response.status}）`);
    const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
    const sidebar = parsed.querySelector(".stage-sidebar");
    const workspace = parsed.querySelector(".workspace");
    if (!sidebar || !workspace) throw new Error("AA 制作前端缺少工作面结构");

    const styleUrls = [
      "/production/app.css",
      "/production/previews.css",
      "/production/preflight.css",
      "/production/cg-responsive.css",
      "/production/workspace-migration.css",
      "/production-embed.css",
    ];
    const styleLoads = styleUrls.map(stylesheet);
    const links = styleLoads.map(load => load.link);
    const shell = document.createElement("div");
    shell.className = "app-shell embedded-production-shell";
    const importedSidebar = document.importNode(sidebar, true);
    const importedWorkspace = document.importNode(workspace, true);
    const topActions = importedWorkspace.querySelector(".top-actions");

    [
      ["#openAssetLibrary", "制作素材"],
      ["#openTasks", "后台任务"],
      ["#openSettings", "设置"],
    ].forEach(([selector, label]) => {
      const source = parsed.querySelector(selector);
      if (!source || !topActions) return;
      const action = document.importNode(source, true);
      action.className = "embed-tool-button";
      action.textContent = label;
      topActions.prepend(action);
    });

    shell.append(importedSidebar, importedWorkspace);
    const auxiliary = [...parsed.body.querySelectorAll("dialog, #toast")].map(node => document.importNode(node, true));
    root.replaceChildren(...links, shell, ...auxiliary);
    await Promise.all(styleLoads);

    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/production/app-embedded.js";
      script.onload = resolve;
      script.onerror = () => reject(new Error("无法启动 AA 制作工作面"));
      document.head.append(script);
    });
    element.setAttribute("aria-busy", "false");
    return root;
  }

  async function selectRun(root, runId) {
    if (!runId) return;
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const button = [...root.querySelectorAll("[data-run-id]")].find(item => item.dataset.runId === runId);
      if (button) {
        button.click();
        return;
      }
      await sleep(100);
    }
  }

  async function open(options = {}) {
    const context = linkedContext(options.trigger);
    for (const key of ["runId", "workId", "releaseId"]) {
      if (options[key]) context[key] = options[key];
    }
    const element = ensureHost();
    element.hidden = false;
    app()?.classList.add("production-mode");
    setOuterChrome(context);
    updateUrl(context, Boolean(options.replaceHistory));
    try {
      loadPromise ||= loadProductionSurface();
      const root = await loadPromise;
      installOuterActions(root);
      await selectRun(root, context.runId);
      element.focus?.({ preventScroll: true });
    } catch (error) {
      loadPromise = null;
      element.setAttribute("aria-busy", "false");
      const root = element.shadowRoot || element.attachShadow({ mode: "open" });
      root.innerHTML = `<div style="padding:32px;color:#a54a42;font:14px/1.7 sans-serif"><b>AA 制作工作面没有打开</b><p>${String(error.message || error)}</p><button type="button" id="retryProduction">重试</button></div>`;
      root.querySelector("#retryProduction")?.addEventListener("click", () => open({ ...context, replaceHistory: true }));
    }
  }

  function close(options = {}) {
    app()?.classList.remove("production-mode");
    const element = host();
    if (element) element.hidden = true;
    restoreOuterChrome();
    if (options.section) {
      const url = new URL(location.href);
      url.pathname = "/";
      url.search = "";
      url.searchParams.set("section", options.section);
      const context = linkedContext();
      if (context.workId) url.searchParams.set("work_id", context.workId);
      if (options.section === "writing" && context.releaseId) {
        url.searchParams.set("stage", "release");
        url.searchParams.set("release_id", context.releaseId);
      }
      history.pushState({ section: options.section }, "", url);
    }
  }

  document.addEventListener("click", event => {
    if (!app()?.classList.contains("production-mode")) return;
    const section = event.target.closest("[data-section]")?.dataset.section;
    const mobile = event.target.closest("[data-mobile]")?.dataset.mobile;
    if ((section && section !== "production") || mobile) close();
  }, true);

  window.addEventListener("popstate", () => {
    const params = new URLSearchParams(location.search);
    if (params.get("section") === "production") {
      open({ ...linkedContext(), replaceHistory: true });
    } else {
      close();
    }
  });

  window.HaloCueProductionEmbed = { open, close, isOpen: () => app()?.classList.contains("production-mode") };
})();
