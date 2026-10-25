import { app } from "../../scripts/app.js";
import { t } from "./i18n.js";
import { ensureStyles } from "./styles.js";
import { BTN_ID, GROUP_CLASS, buttonLabel, hooks } from "./shared.js";

export function applyButtonState() {
  const S = t();
  const nodes = [
    document.getElementById(BTN_ID),
    ...document.querySelectorAll("." + GROUP_CLASS + " button, ." + GROUP_CLASS + " .comfyui-button"),
  ].filter(Boolean);

  for (const el of new Set(nodes)) {
    el.textContent = buttonLabel();
    el.classList.remove('has-issues','has-updates-only');
    el.title = S.menuTooltip;
    el.setAttribute('aria-label', 'NodeOps');
  }
}
export function mountGroupOutermost(groupEl) {
  // Put our button group at the far right of the top menu, not between other groups.
  const settingsGroup = app.menu?.settingsGroup;
  const parent = settingsGroup?.element?.parentElement;
  if (parent) {
    parent.appendChild(groupEl);
    return true;
  }
  if (settingsGroup?.element) {
    settingsGroup.element.after(groupEl);
    return true;
  }
  return false;
}
export function existingMenuButton() {
  const buttons = [...document.querySelectorAll('#' + BTN_ID)];
  const keep = buttons.shift();
  for (const duplicate of buttons) {
    const group = duplicate.closest('.' + GROUP_CLASS);
    duplicate.remove();
    if (group && !group.querySelector('button, .comfyui-button')) group.remove();
  }
  return keep;
}
export async function attachToTopMenu(attempt = 0) {
  ensureStyles();
  const existingButton = existingMenuButton();
  if (existingButton) {
    const group = existingButton.closest('.' + GROUP_CLASS);
    if (group) mountGroupOutermost(group);
    hooks.applyButtonState();
    return true;
  }
  const existingGroup = document.querySelector("." + GROUP_CLASS);
  if (existingGroup) {
    mountGroupOutermost(existingGroup);
    hooks.applyButtonState();
    return true;
  }

  try {
    const settingsGroup = app.menu?.settingsGroup;
    if (settingsGroup?.element?.parentElement) {
      let ComfyButton;
      let ComfyButtonGroup;
      try {
        ({ ComfyButton } = await import("../../scripts/ui/components/button.js"));
        ({ ComfyButtonGroup } = await import("../../scripts/ui/components/buttonGroup.js"));
      } catch {
        ComfyButton = null;
        ComfyButtonGroup = null;
      }

      // Another attach call may have finished during the dynamic imports.
      if (existingMenuButton()) { hooks.applyButtonState(); return true; }
      if (ComfyButton && ComfyButtonGroup) {
        const button = new ComfyButton({
          content: buttonLabel(),
          tooltip: t().menuTooltip,
          classList: "comfyui-button",
          action: () => hooks.openReport(),
        });
        button.element.id = BTN_ID;
        const group = new ComfyButtonGroup(button);
        group.element.classList.add(GROUP_CLASS);
        mountGroupOutermost(group.element);
        hooks.applyButtonState();
        return true;
      }

      const btn = document.createElement("button");
      btn.id = BTN_ID;
      btn.type = "button";
      btn.textContent = buttonLabel();
      btn.addEventListener("click", () => hooks.openReport());
      const wrap = document.createElement("div");
      wrap.className = "comfyui-button-group " + GROUP_CLASS;
      wrap.appendChild(btn);
      mountGroupOutermost(wrap);
      hooks.applyButtonState();
      return true;
    }
  } catch (err) {
    console.warn("[QBB NodeOps] settingsGroup attach failed", err);
    // A rendering error after mounting is not permission to add another button.
    if (existingMenuButton()) return true;
  }

  const host =
    document.querySelector(".comfyui-menu") ||
    document.querySelector(".comfyui-menu-right") ||
    document.querySelector(".actionbar-right") ||
    document.querySelector(".p-toolbar-group-end") ||
    document.querySelector(".p-menubar") ||
    document.querySelector("header") ||
    document.querySelector("#comfyui-body-top");

  if (host) {
    const btn = document.createElement("button");
    btn.id = BTN_ID;
    btn.type = "button";
    btn.textContent = buttonLabel();
    btn.addEventListener("click", () => hooks.openReport());
    const wrap = document.createElement("div");
    wrap.className = GROUP_CLASS;
    wrap.style.display = "inline-flex";
    wrap.style.alignItems = "center";
    wrap.style.marginLeft = "8px";
    wrap.appendChild(btn);
    host.appendChild(wrap);
    hooks.applyButtonState();
    return true;
  }

  if (attempt < 40) {
    setTimeout(() => attachToTopMenu(attempt + 1), 400);
  } else {
    // Last resort: fixed floating button so UI is never missing
    if (!document.getElementById(BTN_ID)) {
      const btn = document.createElement("button");
      btn.id = BTN_ID;
      btn.type = "button";
      btn.textContent = buttonLabel();
      btn.className = "qbb-nodeops-fallback";
      btn.style.cssText =
        "position:fixed;top:12px;right:72px;z-index:100001;font-weight:800;font-size:12px;padding:6px 12px;border-radius:6px;cursor:pointer;";
      btn.addEventListener("click", () => hooks.openReport());
      document.body.appendChild(btn);
      hooks.applyButtonState();
      console.warn("[QBB NodeOps] attached floating fallback button");
      return true;
    }
  }
  return false;
}

hooks.applyButtonState = applyButtonState;
