'use strict';
const dialog = document.querySelector('#contact-dialog');
const form = document.querySelector('#demo-form');
const result = document.querySelector('#demo-result');
const reset = document.querySelector('.reset');
let opener;
function resetDemo() {
  form.reset();
  form.hidden = false;
  result.hidden = true;
  result.textContent = '';
  reset.hidden = true;
}
document.querySelectorAll('[data-open]').forEach(button => {
  button.addEventListener('click', () => {
    if (dialog.open) return;
    opener = button;
    resetDemo();
    if (button.dataset.topic) document.querySelector('#topic').value = button.dataset.topic;
    dialog.showModal();
    document.body.classList.add('modal-open');
  });
});
document.querySelector('.close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => { if (event.target === dialog) {
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
}});
dialog.addEventListener('close', () => {
  document.body.classList.remove('modal-open');
  resetDemo();
  opener?.focus({preventScroll:true});
});
form.addEventListener('submit', event => {
  event.preventDefault();
  result.textContent = `ご相談内容：${document.querySelector('#topic').value} ／ ${document.querySelector('#prefecture').value}。デモの確認が完了しました。内容は送信・保存されていません。`;
  form.hidden = true;
  result.hidden = false;
  reset.hidden = false;
  reset.focus();
});
reset.addEventListener('click', () => { resetDemo(); document.querySelector('#topic').focus(); });
dialog.addEventListener('keydown', event => {
  if (event.key !== 'Tab') return;
  const controls = [...dialog.querySelectorAll('button, select, [href], [tabindex="0"]')]
    .filter(element => !element.disabled && element.getClientRects().length);
  const first = controls[0];
  const last = controls[controls.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
});
