// Numeric companion to the existing blank-template drawing tool. The advanced
// JSON remains the serialized input, so multi-page mappings keep one contract.
export function mountRegions(form, h) {
  const source = form.elements.regions;
  const host = document.createElement("div");
  source.closest("details").before(host);
  function draw() {
    let regions;
    try { regions = JSON.parse(source.value); } catch { return; }
    if (!Array.isArray(regions)) return;
    host.innerHTML = `<h4>Answer areas</h4><p class="supporting-copy">Measure percentages from the top-left of the page, or use a saved blank-template drawing.</p>${regions.map((area, index) => `<fieldset data-region="${index}"><legend>Area ${index + 1}</legend><div class="grading-columns">${h.field("Page", `region-page-${index}`, area.page, "number", 'min="1" step="1"')}${["x", "y", "width", "height"].map(key => h.field(`${{x:"Left", y:"Top", width:"Width", height:"Height"}[key]} (%)`, `region-${key}-${index}`, Math.round(10000 * area.region[key]) / 100, "number", 'min="0" max="100" step="0.1"')).join("")}</div>${h.button("remove-region", "Remove area", `data-index="${index}"`)}<small>${area.privacy_masks?.length || 0} privacy masks saved; inspect the exact masked crop before cloud processing.</small></fieldset>`).join("")}${h.button("add-region", "Add answer area")}`;
    host.querySelectorAll("input").forEach(input => input.oninput = () => {
      const updated = regions.map((area, index) => ({...area, page:Number(form.elements[`region-page-${index}`].value),
        region:Object.fromEntries(["x","y","width","height"].map(key => [key, Number(form.elements[`region-${key}-${index}`].value)/100]))}));
      source.value = JSON.stringify(updated, null, 2);
    });
    host.querySelectorAll('[data-grading-action="remove-region"]').forEach(button => button.onclick = () => {
      const current = JSON.parse(source.value); current.splice(Number(button.dataset.index), 1);
      source.value = JSON.stringify(current, null, 2); source.dispatchEvent(new Event("change"));
    });
    host.querySelector('[data-grading-action="add-region"]').onclick = () => {
      const current = JSON.parse(source.value); current.push({page:1,region:{x:.1,y:.2,width:.8,height:.6},privacy_masks:[]});
      source.value = JSON.stringify(current, null, 2); source.dispatchEvent(new Event("change"));
    };
  }
  source.addEventListener("change", draw);
  draw();
}
