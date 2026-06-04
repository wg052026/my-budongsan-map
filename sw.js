// 내 부동산 지도 — 서비스워커
// 앱 셸(html/아이콘)만 캐시. 카카오맵 SDK와 실거래가 API는 항상 네트워크 우선.
var CACHE = "budongsan-v1";
var SHELL = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", function (e) {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(function (c) {
      return c.addAll(SHELL).catch(function(){ /* 일부 실패해도 진행 */ });
    })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; })
        .map(function (k) { return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function (e) {
  var url = e.request.url;
  // 카카오맵, 구글 앱스 스크립트(실거래가)는 캐시하지 않고 네트워크로
  if (url.indexOf("dapi.kakao.com") >= 0 ||
      url.indexOf("script.google.com") >= 0 ||
      url.indexOf("apis.data.go.kr") >= 0) {
    return; // 기본 네트워크 처리
  }
  // 앱 셸: 네트워크 우선, 실패 시 캐시
  e.respondWith(
    fetch(e.request).then(function (res) {
      return res;
    }).catch(function () {
      return caches.match(e.request).then(function (m) {
        return m || caches.match("./index.html");
      });
    })
  );
});
