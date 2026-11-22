/* MangaShelf Service Worker
 *
 * 策略刻意选「网络优先、缓存兜底」：
 *   本地服务响应只有几毫秒，没必要为了省这点时间去吃缓存的亏；
 *   而且改了 web/ 里的文件后刷新就能看到，不会出现「改了没生效」的假故障。
 * API（漫画数据、图片、进度）一律不拦，永远走网络，避免看到过期内容。
 */
const CACHE = "mangashelf-v1";

const PRECACHE = [
  "/index.html",
  "/css/style.css",
  "/js/api.js",
  "/js/tags.js",
  "/js/cats.js",
  "/js/reader.js",
  "/js/detail.js",
  "/js/app.js",
  "/manifest.json",
  "/icon.svg",
  "/icons/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // 数据、封面、页面图、缩略图都不缓存
  if (url.pathname.startsWith("/api/")) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response && response.ok && response.type === "basic") {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(async () => {
        const hit = await caches.match(request);
        if (hit) return hit;
        // 导航请求断网时退回书架首页，至少能进得去
        if (request.mode === "navigate") {
          const shell = await caches.match("/index.html");
          if (shell) return shell;
        }
        return Response.error();
      })
  );
});
