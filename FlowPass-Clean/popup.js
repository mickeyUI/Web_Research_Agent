document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('open-flow-btn');
  if (btn) {
    btn.addEventListener('click', () => {
      window.open('https://flow.google.com/', '_blank');
    });
  }
});
