(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const isProduction = location.pathname.startsWith("/production/");
  const sleep = (delay) => new Promise(resolve => setTimeout(resolve, delay));

  async function waitFor(find, attempts = 60) {
    for (let index = 0; index < attempts; index += 1) {
      const value = find();
      if (value) return value;
      await sleep(100);
    }
    return null;
  }

  function productionUrl(runId, workId, releaseId) {
    const url = new URL("/production/", location.origin);
    if (runId) url.searchParams.set("run_id", runId);
    if (workId) url.searchParams.set("work_id", workId);
    if (releaseId) url.searchParams.set("release_id", releaseId);
    return url;
  }

  if (!isProduction) {
    document.addEventListener("click", async event => {
      const leavingSection = event.target.closest('[data-section]:not([data-section="production"])')?.dataset.section;
      const leavingMobile = event.target.closest("[data-mobile]")?.dataset.mobile;
      if (window.HaloCueProductionEmbed?.isOpen?.() && (leavingSection || leavingMobile)) {
        const destination = leavingSection || (leavingMobile === "works" || leavingMobile === "references" || leavingMobile === "tasks" ? leavingMobile : "writing");
        window.HaloCueProductionEmbed.close({ section: destination });
        if (destination === "writing" && params.get("release_id")) {
          setTimeout(() => document.querySelector('[data-stage="release"]:not([disabled])')?.click(), 0);
        }
      }
      const productionNav = event.target.closest('[data-section="production"]');
      if (productionNav) {
        if (productionNav.matches('.locked-nav,[aria-disabled="true"]')) {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        const linkedProduction = document.querySelector("[data-open-production]");
        const openProduction = await waitFor(() => window.HaloCueProductionEmbed?.open, 30);
        if (!openProduction) {
          window.alert("AA 制作工作面没有载入，请刷新当前页面后重试。");
          return;
        }
        openProduction({
          trigger: linkedProduction || productionNav,
          runId: linkedProduction?.dataset.openProduction || params.get("run_id") || "",
          workId: linkedProduction?.dataset.workId || params.get("work_id") || "",
          releaseId: linkedProduction?.dataset.releaseId || params.get("release_id") || "",
        });
        return;
      }

      const existing = event.target.closest("[data-open-production]");
      if (existing) {
        event.preventDefault();
        event.stopImmediatePropagation();
        const openProduction = await waitFor(() => window.HaloCueProductionEmbed?.open, 30);
        if (openProduction) openProduction({
          trigger: existing,
          runId: existing.dataset.openProduction,
          workId: existing.dataset.workId,
          releaseId: existing.dataset.releaseId,
        });
        return;
      }

      const handoff = event.target.closest("[data-handoff]");
      if (!handoff || handoff.disabled) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const releaseId = handoff.dataset.handoff;
      handoff.disabled = true;
      handoff.textContent = "正在建立制作任务...";
      try {
        const response = await fetch(`/api/v1/releases/${encodeURIComponent(releaseId)}/handoff`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const payload = await response.json();
        if (!response.ok || payload.ok === false) throw new Error(payload.error?.message || "交接失败");
        const result = payload.data || payload;
        const openProduction = await waitFor(() => window.HaloCueProductionEmbed?.open, 30);
        if (!openProduction) throw new Error("AA 制作工作面没有载入");
        openProduction({
          runId: result.production_run_id,
          workId: handoff.dataset.workId,
          releaseId,
        });
      } catch (error) {
        handoff.disabled = false;
        handoff.textContent = "交给 AA 制作";
        window.alert(error.message || "AA 制作后端当前不可用，发布版本仍安全保留。");
      }
    }, true);

    window.addEventListener("DOMContentLoaded", async () => {
      const workId = params.get("work_id");
      if (workId) {
        const workButton = await waitFor(() => [...document.querySelectorAll("[data-select-work]")].find(button => button.dataset.selectWork === workId));
        workButton?.click();
        await waitFor(() => [...document.querySelectorAll("[data-select-work]")].find(button => button.dataset.selectWork === workId && button.classList.contains("active")));
      }
      const section = params.get("section");
      if (section === "production") {
        const openProduction = await waitFor(() => window.HaloCueProductionEmbed?.open, 40);
        if (openProduction) {
          openProduction({
            runId: params.get("run_id") || "",
            workId: params.get("work_id") || "",
            releaseId: params.get("release_id") || "",
          });
        }
      } else if (section) {
        const sectionButton = await waitFor(() => document.querySelector(`[data-section="${section}"]:not([disabled])`));
        sectionButton?.click();
      }
      if (params.get("stage") === "release") {
        const writingButton = await waitFor(() => document.querySelector('[data-section="writing"]:not([disabled])'));
        writingButton?.click();
        const releaseButton = await waitFor(() => document.querySelector('[data-stage="release"]:not([disabled])'));
        releaseButton?.click();
      }
    });
    return;
  }

  async function initializeProductionShell() {
    document.body.classList.add("halocue-integrated-production");
    const appShell = document.querySelector(".app-shell");
    const rail = document.querySelector(".app-rail");
    const workspace = document.querySelector(".workspace");
    const topbar = document.querySelector(".topbar");
    const assetLibrary = document.querySelector("#openAssetLibrary");
    const tasks = document.querySelector("#openTasks");
    const settings = document.querySelector("#openSettings");
    if (rail && tasks && settings) {
      const linkedWorkId = params.get("work_id");
      const linkedReleaseId = params.get("release_id");
      const writingSectionUrl = (section) => {
        const url = new URL("/", location.origin);
        url.searchParams.set("section", section);
        if (linkedWorkId) url.searchParams.set("work_id", linkedWorkId);
        if (section === "writing" && linkedReleaseId) {
          url.searchParams.set("stage", "release");
          url.searchParams.set("release_id", linkedReleaseId);
        }
        return `${url.pathname}${url.search}`;
      };
      const navLink = (label, mark, href, extra = "") => `<a class="integrated-nav-item ${extra}" href="${href}" title="${label}"><span class="integrated-nav-mark">${mark}</span><span>${label}</span></a>`;
      rail.innerHTML = `<a class="integrated-brand" href="/" title="HaloCue">HC</a>${navLink("作品", "作", writingSectionUrl("works"))}${navLink("写作", "写", writingSectionUrl("writing"))}${navLink("AA 制作", "制", `${location.pathname}${location.search}`, "active")}${navLink("资料", "资", writingSectionUrl("references"), "integrated-reference-link")}<span class="integrated-nav-task"></span><span class="integrated-nav-spacer"></span><button type="button" class="integrated-nav-item" id="openFeedbackInProduction" title="反馈使用体验或问题"><span class="integrated-nav-mark">馈</span><span>反馈</span></button><span class="integrated-nav-settings"></span>`;
      tasks.className = "integrated-nav-item";
      tasks.innerHTML = '<span class="integrated-nav-mark">任</span><span>任务</span>';
      tasks.title = "制作任务";
      settings.className = "integrated-nav-item";
      settings.innerHTML = '<span class="integrated-nav-mark">设</span><span>设置</span>';
      rail.querySelector(".integrated-nav-task")?.replaceWith(tasks);
      rail.querySelector(".integrated-nav-settings")?.replaceWith(settings);

      document.querySelector("#openFeedbackInProduction")?.addEventListener("click", () => {
        location.href = writingSectionUrl("writing") + "&open_feedback=1";
      });
    }
    if (appShell && workspace && topbar) {
      appShell.prepend(topbar);
      const brand = rail?.querySelector(".integrated-brand");
      if (brand) topbar.prepend(brand);
    }
    if (assetLibrary) {
      assetLibrary.className = "integrated-top-action";
      assetLibrary.textContent = "制作素材";
      assetLibrary.title = "打开当前制作任务可用素材";
      document.querySelector(".top-actions")?.prepend(assetLibrary);
    }
    const runId = params.get("run_id");
    if (!runId) return;
    const runButton = await waitFor(() => [...document.querySelectorAll("[data-run-id]")].find(button => button.dataset.runId === runId));
    runButton?.click();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeProductionShell, { once: true });
  } else {
    initializeProductionShell();
  }
})();
