'use strict';
const examples = {
  install: '別途購入したエアコンの取付けを相談したいです。新品か中古か、型番、設置場所と希望日をお伝えします。',
  move: '引越しに伴うエアコンの移設を相談したいです。型番と設置状況、希望日をお伝えします。',
  remove: 'エアコンの取外しを相談したいです。型番と室内機・室外機の設置状況、希望日をお伝えします。'
};
const buttons = document.querySelectorAll('[data-plan]');
buttons.forEach(button => button.addEventListener('click', () => {
  document.querySelector('#example').textContent = examples[button.dataset.plan];
  buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
}));
