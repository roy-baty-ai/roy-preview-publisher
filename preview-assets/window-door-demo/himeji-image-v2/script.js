'use strict';
const examples = {
  once: '対応地域内のお墓について、1回のお墓参り・清掃を相談したいです。お墓の場所とおおよその広さ、希望する内容をお伝えします。',
  year: '対応地域内のお墓について、年間4回プランを相談したいです。献花・供物の希望と、実費を含めた条件を確認したいです。',
  month: '対応地域内のお墓について、毎月プランを相談したいです。作業内容、費用、完了の連絡方法を確認したいです。'
};
const buttons = document.querySelectorAll('[data-plan]');
buttons.forEach(button => button.addEventListener('click', () => {
  document.querySelector('#example').textContent = examples[button.dataset.plan];
  buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
}));
