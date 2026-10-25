function linkStylesheet(id, href) {
  if (document.getElementById(id)) return;
  const link = document.createElement("link");
  link.id = id;
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

export function ensureStyles() {
  linkStylesheet("qbb-nodeops-tokens-css", new URL("./tokens.css", import.meta.url).href);
  linkStylesheet("qbb-nodeops-components-css", new URL("./components.css", import.meta.url).href);
  linkStylesheet("qbb-nodeops-workspace-css", new URL("./workspace.css", import.meta.url).href);
  linkStylesheet("qbb-nodeops-modal-css", new URL("./modal.css", import.meta.url).href);
  linkStylesheet("qbb-nodeops-typography-css", new URL("./typography.css", import.meta.url).href);
}

ensureStyles();
