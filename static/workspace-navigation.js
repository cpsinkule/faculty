/* Dress the persistent sidebar without replacing its live links or counts: each entry's words get their own box (.nav-face,
   .nav-label-text) beside the counts and badges, which keep their own nodes. */
export function mountWorkspaceNavigation() {
  const sidebar = document.querySelector(".sidebar");
  if (!sidebar || sidebar.classList.contains("graphic-navigation")) return;

  sidebar.querySelectorAll(".nav-item").forEach((item) => {
    const face = document.createElement("span");
    face.className = "nav-face";
    face.append(...item.childNodes);
    // The page name gets its own box, so the rail styles the words apart from the
    // counts and badges beside them. The whole row remains a link (or the original settings button).
    face.childNodes.forEach((node) => {
      if (node.nodeType !== Node.TEXT_NODE || !node.textContent.trim()) return;
      const text = document.createElement("span");
      text.className = "nav-label-text";
      node.replaceWith(text);
      text.append(node);
    });
    item.append(face);
  });

  sidebar.classList.add("graphic-navigation");
}
