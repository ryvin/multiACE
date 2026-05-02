// multiACE Web Console — Hardware tab
// Vanilla JS, no framework, no build step.
//
// Public API on window.HardwareTwin:
//   mount(rootEl)                          — once, builds static skeleton
//   render(state, printState, workflow)    — every state push; mutates only

window.HardwareTwin = (function () {
  function mount(rootEl) {
    if (rootEl.dataset.htwMounted === "1") return;
    rootEl.dataset.htwMounted = "1";
    // Skeleton built in Task 3.
  }

  function render(state, printState, workflow) {
    // Implemented incrementally across Tasks 3-9.
  }

  return { mount, render };
})();
