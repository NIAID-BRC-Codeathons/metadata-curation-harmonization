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
document.getElementById('select-all-columns')?.addEventListener('click', () => {
  document.querySelectorAll('#columns-dialog input[name=col]').forEach(checkbox => {
    checkbox.checked = true;
  });
});
document.getElementById('deselect-all-columns')?.addEventListener('click', () => {
  document.querySelectorAll('#columns-dialog input[name=col]').forEach(checkbox => {
    checkbox.checked = false;
  });
});
const columnSearch = document.getElementById('column-search');
const clearColumnSearch = document.getElementById('clear-column-search');
const columnOptions = document.querySelectorAll('#column-options [data-column-name]');
columnSearch?.addEventListener('input', () => {
  clearColumnSearch.hidden = columnSearch.value.length === 0;
  const query = columnSearch.value.trim().toLowerCase();
  let matches = 0;
  columnOptions.forEach(option => {
    option.hidden = !option.dataset.columnName.toLowerCase().includes(query);
    if (!option.hidden) matches += 1;
  });
  document.getElementById('column-search-empty').hidden = matches > 0;
});
clearColumnSearch?.addEventListener('click', () => {
  columnSearch.value = '';
  columnSearch.dispatchEvent(new Event('input'));
  columnSearch.focus();
});
columnSearch?.addEventListener('keydown', event => {
  if (event.key === 'Enter') event.preventDefault();
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
document.querySelectorAll('.ncbi-import-form').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('[type=submit]');
    button.disabled = true;
    button.textContent = 'Starting import…';
  });
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

const dirtyComments = new Set();
const savingComments = new Set();
document.querySelectorAll('.comment-form').forEach(form => {
  const input = form.elements.namedItem('comment');
  const button = Array.from(form.elements).find(element => element.type === 'submit');
  const status = document.getElementById(input.getAttribute('aria-describedby'));
  let savedValue = input.value;
  input.addEventListener('input', () => {
    const dirty = input.value !== savedValue;
    if (dirty) dirtyComments.add(form); else dirtyComments.delete(form);
    status.textContent = dirty ? 'Unsaved changes' : 'Saved';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (savingComments.has(form)) return;
    savingComments.add(form);
    const value = input.value;
    button.disabled = true;
    status.textContent = 'Saving…';
    try {
      const response = await fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' } });
      if (!response.ok || !(await response.json()).saved) throw new Error('Save failed');
      savedValue = value;
      if (input.value === savedValue) dirtyComments.delete(form);
      else dirtyComments.add(form);
      status.textContent = dirtyComments.has(form) ? 'Unsaved changes' : 'Saved';
    } catch {
      dirtyComments.add(form);
      status.textContent = 'Could not save. Please retry; if this persists, copy your comment before reloading.';
    } finally {
      savingComments.delete(form);
      button.disabled = false;
    }
  });
});
window.addEventListener('beforeunload', event => {
  if (dirtyComments.size || savingComments.size) {
    event.preventDefault();
    event.returnValue = '';
  }
});
document.querySelector('[data-export]')?.addEventListener('click', event => {
  if (dirtyComments.size || savingComments.size) {
    event.preventDefault();
    window.alert('Save your edited comments and wait for saving to finish before exporting.');
  }
});
