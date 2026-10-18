// Gentle page motion for customer pages: fade-in on load, scroll reveals, soft fade-out when a link is clicked.
(() => {
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const items = document.querySelectorAll('.welcome-page > *, .detail-page > *, .welcome-option, .flavour-card, .menu-card, .order-item-row, .owner-page .page-heading, .owner-page .metric-card, .owner-page .panel, .owner-page .login-card');
  items.forEach((el, i) => { el.classList.add('reveal'); el.style.setProperty('--d', Math.min(i, 8) * 70 + 'ms'); });
  if (calm || !('IntersectionObserver' in window)) { items.forEach(el => el.classList.add('in')); return; }
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }), { threshold: .12 });
  items.forEach(el => io.observe(el));
  document.addEventListener('click', e => {
    const a = e.target.closest('a[href]');
    if (!a || a.target === '_blank' || e.metaKey || e.ctrlKey || a.origin !== location.origin || a.hash) return;
    e.preventDefault(); document.body.classList.add('leaving');
    setTimeout(() => { location.href = a.href; }, 220);
  });
  addEventListener('pageshow', e => { if (e.persisted) document.body.classList.remove('leaving'); });
})();
