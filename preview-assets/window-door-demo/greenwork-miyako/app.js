'use strict';
const dialog = document.querySelector('dialog');
const form = dialog.querySelector('form');
const result = document.querySelector('#result');
let trigger;
document.querySelectorAll('[data-open]').forEach(button => {
  button.addEventListener('click', () => {
    trigger = button;
    form.reset();
    result.hidden = true;
    result.textContent = '';
    dialog.showModal();
    dialog.querySelector('.close').focus();
  });
});
dialog.querySelector('.close').addEventListener('click', () => dialog.close());
dialog.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const controls = [...dialog.querySelectorAll('button, select, textarea')];
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});
dialog.addEventListener('close', () => {
  form.reset();
  result.textContent = '';
  result.hidden = true;
  trigger?.focus();
});
form.addEventListener('submit', event => {
  event.preventDefault();
  result.textContent = `${document.querySelector('#city').value}での「${document.querySelector('#work').value}」のご相談を体験しました。これはデモです。内容は送信・保存されていません。`;
  result.hidden = false;
});
