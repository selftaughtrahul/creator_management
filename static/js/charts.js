document.querySelectorAll("script[id$='-spec']").forEach((node) => {
  const canvas = document.getElementById(node.id.replace(/-spec$/, ""));
  if (!canvas || typeof Chart === "undefined") return;
  const spec = JSON.parse(node.textContent);
  if (!spec || !spec.data || !spec.data.datasets || !spec.data.datasets.length) return;
  new Chart(canvas, spec);
});
