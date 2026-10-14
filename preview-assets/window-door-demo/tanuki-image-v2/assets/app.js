(function () {
  'use strict';

  // Pure formatter, also exported to the local Node test. No storage or network.
  function buildSummary(data) {
    const category = data.category === 'grave' ? 'お墓のこと' : '家・荷物のこと';
    const area = String(data.area || '').replace(/[\r\n\t\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 40);
    const needs = Array.isArray(data.needs) && data.needs.length ? data.needs.join('、') : 'これから整理したい';
    return [
      '【相談メモ・操作見本／未送信】',
      '相談の種類：' + category,
      '気になっていること：' + needs,
      '地域：' + (area || 'まだ記入していない'),
      '希望時期：' + (data.timing || 'まだ決まっていない'),
      '',
      '※ 事業者未確認の提案用メモです。',
      '※ 料金・対応可否・作業条件は別途確認が必要です。'
    ].join('\n');
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { buildSummary };
  if (typeof document === 'undefined') return;

  const controls = document.getElementById('organizer-controls');
  const categories = Array.from(document.querySelectorAll('input[name="category"]'));
  const allNeeds = Array.from(document.querySelectorAll('input[name="home-need"], input[name="grave-need"]'));
  const homeDetails = document.getElementById('home-details');
  const graveDetails = document.getElementById('grave-details');
  const area = document.getElementById('area-input');
  const timing = document.getElementById('timing');
  const summary = document.getElementById('summary');
  const status = document.getElementById('copy-status');
  const copyButton = document.getElementById('copy-button');

  function updateSummary() {
    const category = categories.find(function (item) { return item.checked; }).value;
    homeDetails.hidden = category !== 'home';
    graveDetails.hidden = category !== 'grave';
    summary.value = buildSummary({
      category: category,
      needs: allNeeds.filter(function (item) { return item.name === category + '-need' && item.checked; }).map(function (item) { return item.value; }),
      area: area.value,
      timing: timing.value
    });
    status.textContent = '';
  }

  categories.concat(allNeeds).forEach(function (input) { input.addEventListener('change', updateSummary); });
  area.addEventListener('input', updateSummary);
  timing.addEventListener('change', updateSummary);

  document.querySelectorAll('.category-link').forEach(function (link) {
    link.addEventListener('click', function () {
      categories.forEach(function (radio) { radio.checked = radio.value === link.dataset.category; });
      updateSummary();
      // A normal anchor remains functional; focus moves to the selected native radio.
      categories.find(function (radio) { return radio.checked; }).focus({ preventScroll: true });
    });
  });

  copyButton.addEventListener('click', async function () {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(summary.value);
        status.textContent = 'メモをコピーしました。相談は送信されていません。';
        return;
      } catch (error) {
        // Clipboard may be unavailable or denied on file://. Offer manual copy.
      }
    }
    summary.focus();
    summary.select();
    summary.setSelectionRange(0, summary.value.length);
    status.textContent = '自動コピーが使えないため、メモを選択しました。端末のコピー操作でコピーしてください。相談は送信されていません。';
  });

  document.getElementById('reset-button').addEventListener('click', function () {
    categories.forEach(function (radio) { radio.checked = radio.value === 'home'; });
    allNeeds.forEach(function (input) { input.checked = false; });
    area.value = '';
    timing.selectedIndex = 0;
    updateSummary();
    status.textContent = '入力をリセットしました。';
  });

  updateSummary();
  controls.hidden = false;
}());
