// Aion Chat Service Worker
// 目标：减少切页时图标/头像闪烁，避免每次都重新拉取图片

const SW_VERSION = "aion-sw-v2";
const IMAGE_CACHE = `${SW_VERSION}-images`;
const STATIC_CACHE = `${SW_VERSION}-static`;

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    // 预热最常用的小图标，减少首次切页闪烁
    const cache = await caches.open(IMAGE_CACHE);
    await cache.addAll([
      "/public/icon-192.png",
      "/public/funIcon_0005_聊天.png",
      "/public/funIcon_0004_记忆库.png"
    ]);
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys
        .filter(k => !k.startsWith(SW_VERSION))
        .map(k => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // HTML 与 API 始终走网络，避免拿到旧数据
  if (req.mode === "navigate" || url.pathname.startsWith("/api/")) return;

  // 图片（public/uploads）缓存优先，解决切页闪烁
  if (req.destination === "image" || url.pathname.startsWith("/public/") || url.pathname.startsWith("/uploads/")) {
    event.respondWith(cacheFirst(req, IMAGE_CACHE));
    return;
  }

  // 其他静态文件（js/css/manifest）使用 stale-while-revalidate
  if (req.destination === "script" || req.destination === "style" || url.pathname.endsWith(".js") || url.pathname.endsWith(".css") || url.pathname.endsWith(".json")) {
    event.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
  }
});

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request, { ignoreVary: true });
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) cache.put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request, { ignoreVary: true });
  const networkPromise = fetch(request)
    .then(response => {
      if (response && response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => null);

  if (cached) return cached;
  const network = await networkPromise;
  if (network) return network;
  return Response.error();
}
