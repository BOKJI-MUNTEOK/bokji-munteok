/* Native theme selection owns persistence, OS preference and widget/chart colors.
 * This component only localizes/restyles the menu and reads the actual theme.
 * No server callbacks, polling, network requests or page reloads are used.
 */
export default function ({ parentElement }) {
  const root = document.documentElement;
  const labels = { Light: "라이트 모드", Dark: "다크 모드", System: "시스템 설정" };
  const routes = ["/", "/find", "/all", "/analysis", "/guide"];
  let frame = 0;
  let closed = false;

  function updateColors() {
    const color = getComputedStyle(parentElement).getPropertyValue("--st-background-color").trim()
      || getComputedStyle(document.body).backgroundColor;
    let rgb;
    if (/^#[0-9a-f]{6}$/i.test(color)) {
      rgb = [1, 3, 5].map((start) => parseInt(color.slice(start, start + 2), 16));
    } else {
      rgb = color.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    }
    if (rgb?.length === 3) {
      const brightness = rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
      const mode = brightness < 128 ? "dark" : "light";
      if (root.dataset.bokjiTheme !== mode) root.dataset.bokjiTheme = mode;
    }
  }

  function localize() {
    const button = document.querySelector('[data-testid="stMainMenuButton"]');
    if (button && button.getAttribute("aria-label") !== "화면 스타일 설정") {
      button.setAttribute("aria-label", "화면 스타일 설정");
      button.setAttribute("title", "화면 스타일 설정");
    }
    const menu = document.querySelector('[data-testid="stMainMenuList"]');
    if (menu) menu.setAttribute("aria-label", "화면 스타일");
    const group = document.querySelector('[data-testid="stThemeSwitcher"]');
    if (group) group.setAttribute("aria-label", "화면 스타일");
    for (const [mode, label] of Object.entries(labels)) {
      const item = document.querySelector(`[data-testid="stMainMenuItem-theme-${mode}"]`);
      if (item && item.getAttribute("aria-label") !== label) {
        item.setAttribute("aria-label", label);
        item.setAttribute("title", label);
      }
    }
    if (menu && !menu.dataset.bokjiInitialized) {
      menu.dataset.bokjiInitialized = "true";
      menu.querySelector('[role="menuitemradio"][aria-checked="true"]')?.focus();
    }
    updateColors();
  }

  function rememberChoice(event) {
    const item = event.target instanceof Element
      ? event.target.closest('[data-testid^="stMainMenuItem-theme-"]') : null;
    const mode = item?.dataset.testid?.replace("stMainMenuItem-theme-", "");
    if (!Object.prototype.hasOwnProperty.call(labels, mode)) return;
    // Streamlit's native v2 theme cache is pathname-scoped. Mirror only our
    // routes so a reload after page navigation keeps the same display choice.
    try {
      for (const route of routes) localStorage.setItem(`stActiveTheme-${route}-v2`, JSON.stringify(mode));
    } catch (_) { /* Browser storage can be disabled; native in-tab switching still works. */ }
  }

  function moveFocus(event) {
    if (!(event.target instanceof Element)
      || !event.target.closest('[data-testid="stMainMenuList"]')) return;
    if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = Object.keys(labels).map((mode) =>
      document.querySelector(`[data-testid="stMainMenuItem-theme-${mode}"]`)).filter(Boolean);
    if (items.length !== 3) return;
    const current = Math.max(0, items.indexOf(document.activeElement));
    const index = event.key === "Home" ? 0 : event.key === "End" ? 2
      : (current + (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1) + items.length) % items.length;
    event.preventDefault();
    event.stopPropagation();
    items[index].focus();
  }

  function schedule() {
    if (frame || closed) return;
    frame = requestAnimationFrame(() => { frame = 0; localize(); });
  }

  // Native theme changes replace the component wrapper's theme class. Observe
  // only its ancestors' class/style attributes, not every input or chart update.
  const themeObserver = new MutationObserver(schedule);
  for (let node = parentElement; node; node = node.parentElement) {
    themeObserver.observe(node, { attributes: true, attributeFilter: ["class", "style"] });
  }

  // The menu is mounted in a portal only while open. Ignore unrelated fragments.
  const menuSelector = '[data-testid="stMainMenu"], [data-testid="stMainMenuPopover"]';
  const menuObserver = new MutationObserver((records) => {
    if (records.some((record) => record.target instanceof Element
      && (record.target.closest('[data-testid="stMainMenuPopover"]')
        || [...record.addedNodes].some((node) => node instanceof Element
          && (node.matches(menuSelector) || node.querySelector(menuSelector)))))) schedule();
  });
  menuObserver.observe(document.body, { childList: true, subtree: true });
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", schedule);
  document.addEventListener("click", rememberChoice, true);
  document.addEventListener("keydown", moveFocus, true);
  localize();
  return () => {
    closed = true;
    if (frame) cancelAnimationFrame(frame);
    themeObserver.disconnect();
    menuObserver.disconnect();
    media.removeEventListener("change", schedule);
    document.removeEventListener("click", rememberChoice, true);
    document.removeEventListener("keydown", moveFocus, true);
  };
}
