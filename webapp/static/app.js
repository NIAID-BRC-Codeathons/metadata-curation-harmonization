document.querySelectorAll('[data-open]').forEach(button => {
  button.addEventListener('click', () => document.getElementById(button.dataset.open).showModal());
});
document.querySelectorAll('[data-close]').forEach(button => {
  button.addEventListener('click', () => button.closest('dialog').close());
});
document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    }
  });
});
const filterPanel = document.getElementById('filter-panel');
const filterRows = document.getElementById('filter-rows');
function updateFilter(row) {
  const noValue = ['null', 'missing', 'exists'].includes(row.querySelector('[name=op]').value);
  const input = row.querySelector('[name=v]');
  input.readOnly = noValue;
  input.placeholder = noValue ? 'No value needed' : 'Filter value';
  if (noValue) input.value = '';
}
function addFilter() {
  if (filterRows.children.length >= 20) return;
  filterRows.append(document.getElementById('filter-template').content.cloneNode(true));
}
document.getElementById('toggle-filters')?.addEventListener('click', event => {
  filterPanel.hidden = !filterPanel.hidden;
  event.currentTarget.setAttribute('aria-expanded', String(!filterPanel.hidden));
});
document.getElementById('add-filter')?.addEventListener('click', addFilter);
filterRows?.addEventListener('click', event => {
  if (event.target.closest('.remove-filter')) event.target.closest('.filter-row').remove();
});
filterRows?.addEventListener('change', event => {
  if (event.target.name === 'op') updateFilter(event.target.closest('.filter-row'));
});
document.querySelectorAll('.filter-row').forEach(updateFilter);
document.getElementById('page-size')?.addEventListener('change', () => document.getElementById('view-form').requestSubmit());
document.getElementById('import-form')?.addEventListener('submit', event => {
  const button = event.target.querySelector('[type=submit]');
  button.disabled = true;
  button.textContent = 'Importing…';
});
document.querySelectorAll('[data-tab]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('[data-tab]').forEach(tab => {
      const selected = tab === button;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      document.getElementById(tab.dataset.tab).hidden = !selected;
    });
  });
});
document.getElementById('copy-json')?.addEventListener('click', async event => {
  try {
    await navigator.clipboard.writeText(document.getElementById('raw-json').textContent);
    event.target.textContent = 'Copied!';
  } catch {
    event.target.textContent = 'Select and copy the JSON below';
  }
});
