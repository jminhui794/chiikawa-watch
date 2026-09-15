self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch {}
  event.waitUntil(self.registration.showNotification(data.title || '치이카와 알림', {
    body: data.body || '알림 내용을 확인해 주세요.',
    tag: data.tag || 'chiikawa',
    data: {url: data.url || 'https://cgv.co.kr/cnm/movieBook/movie'}
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  let url = 'https://cgv.co.kr/cnm/movieBook/movie';
  try {
    const target = new URL(event.notification.data.url);
    if (target.protocol === 'https:' && ['cgv.co.kr', 'www.megabox.co.kr', 'www.lottecinema.co.kr', 'www.cineq.co.kr'].includes(target.hostname)) url = target.href;
  } catch {}
  event.waitUntil(clients.openWindow(url));
});
