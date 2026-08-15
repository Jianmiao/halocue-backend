(() => {
  const storageKey = 'halocue-writing.focus-mode.v2';
  const root = document.getElementById('app');
  if (!root) return;

  let focusMode = false;
  try {
    focusMode = Boolean(JSON.parse(localStorage.getItem(storageKey) || 'false'));
  } catch (_) {
    // A malformed browser preference must never prevent a workbench from opening.
  }

  const save = () => localStorage.setItem(storageKey, JSON.stringify(focusMode));

  function apply() {
    root.classList.toggle('tree-collapsed', focusMode);
    root.classList.toggle('inspector-collapsed', focusMode);
    root.classList.toggle('focus-mode', focusMode);

    const focusButton = document.querySelector('[data-focus-toggle]');
    if (focusButton) {
      focusButton.setAttribute('aria-pressed', String(focusMode));
      focusButton.textContent = focusMode ? '退出专注' : '专注';
      focusButton.title = focusMode ? '恢复完整写作工作台' : '收起两侧栏，专注查看正文';
    }
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.focusToggle !== undefined) {
      event.preventDefault();
      focusMode = !focusMode;
      save();
      apply();
    }
  }, true);

  apply();
})();
