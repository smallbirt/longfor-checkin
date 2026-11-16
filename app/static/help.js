const guidePrint = document.getElementById('print-guide');
if (guidePrint) {
  guidePrint.hidden = false;
  guidePrint.addEventListener('click', () => window.print());
}
let guideOpened = [];
window.addEventListener('beforeprint', () => {
  guideOpened = [...document.querySelectorAll('.guide-section details')].filter(item => !item.open);
  guideOpened.forEach(item => item.open = true);
});
window.addEventListener('afterprint', () => {
  guideOpened.forEach(item => item.open = false);
  guideOpened = [];
});
