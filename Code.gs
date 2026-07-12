/**********************************************************************
 *  부동산 지도 — 실거래가 중계 서버 (Google Apps Script) [캐시+스마트매칭+병렬fetch판]
 *  - 같은 동(lawd)+달(ym) 데이터를 캐시(6시간)에 저장.
 *  - 여러 달을 UrlFetchApp.fetchAll()로 한번에 병렬 요청 (기존 순차 요청 대비 대폭 빨라짐).
 *  - 아파트 이름 매칭을 토큰 기반으로 개선:
 *    국토부 등록명은 '호수마을(럭키)'처럼 건설사명, 우리 이름은
 *    '호수마을4단지럭키롯데'처럼 단지번호+건설사. 괄호·공백·단지번호를
 *    정규화해서 핵심 토큰(마을명/건설사/브랜드)으로 매칭한다.
 **********************************************************************/

var SERVICE_KEY = "b7f3b1b7a845ff366c079f48081a3732b4b3f9174e1df76a999e4350637bd3e7";
var ENDPOINT    = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev";
var CACHE_SEC   = 21600;   // 캐시 보관 시간(초). 21600 = 6시간
var BATCH_SIZE  = 20;      // UrlFetchApp.fetchAll 한 번에 보낼 최대 요청 수 (너무 크면 GAS 자체 제한에 걸릴 수 있어 나눠서 처리)

function doGet(e) {
  var p = (e && e.parameter) || {};
  var lawd = (p.lawd || "").trim();
  var aptQuery = (p.apt || "").trim();
  var road = (p.road || "").trim();      // 도로명 (예: 용현로)
  var bun  = (p.bun || "").trim();        // 건물번호 (예: 10)
  var jibun = (p.jibun || "").trim();     // 지번 (예: 6887-3)
  var months = parseInt(p.months || "3", 10);
  if (isNaN(months) || months < 1) months = 3;

  var out = { ok: false, ver: "smart-match-v6-parallel", query: { lawd: lawd, apt: aptQuery, road: road, bun: bun, jibun: jibun, months: months }, deals: [], latest: null, error: null, matchBy: null };
  if (!lawd) { out.error = "lawd(법정동코드 5자리)가 필요합니다."; return json_(out); }

  try {
    var ymList = recentYearMonths_(months);
    var deals = fetchMonthsBatch_(lawd, ymList);   // ★ 병렬 배치 fetch (기존 순차 for문 대체)
    var matched = null;
    // 1순위: 주소 매칭 (도로명+건물번호)
    if (road && bun) {
      var byRoad = matchByRoad_(deals, road, bun);
      if (byRoad.length) { matched = byRoad; out.matchBy = "road"; }
    }
    // 2순위: 지번 매칭
    if (!matched && jibun) {
      var byJibun = matchByJibun_(deals, jibun);
      if (byJibun.length) { matched = byJibun; out.matchBy = "jibun"; }
    }
    // 3순위: 이름 매칭 (폴백)
    if (!matched && aptQuery) {
      matched = matchDeals_(deals, aptQuery);
      out.matchBy = matched.length ? "name" : null;
    }
    // 매칭 단서(주소/이름)가 하나도 없으면 빈 결과 (동 전체 반환 방지)
    if (matched == null) matched = [];
    matched.sort(function (a, b) { return (b.dateNum - a.dateNum); });
    out.ok = true; out.deals = matched; out.latest = matched.length ? matched[0] : null;
  } catch (err) { out.error = String(err); }
  return json_(out);
}

/** 여러 달을 병렬로 가져오기: 캐시에 있는 건 바로, 없는 건 fetchAll로 한번에 */
function fetchMonthsBatch_(lawd, ymList) {
  var cache = CacheService.getScriptCache();
  var keys = ymList.map(function (ym) { return "deal_" + lawd + "_" + ym; });
  var hits = cache.getAll(keys);   // 여러 키를 한 번에 조회 (캐시 조회도 배치)

  var deals = [];
  var missYm = [];
  for (var i = 0; i < ymList.length; i++) {
    var hit = hits[keys[i]];
    if (hit != null) {
      try { deals = deals.concat(JSON.parse(hit)); continue; } catch (e) {}
    }
    missYm.push(ymList[i]);
  }
  if (!missYm.length) return deals;

  // 캐시에 없는 달들만 배치로 나눠서 병렬 요청
  for (var b = 0; b < missYm.length; b += BATCH_SIZE) {
    var chunk = missYm.slice(b, b + BATCH_SIZE);
    var requests = chunk.map(function (ym) {
      return { url: buildMonthUrl_(lawd, ym), muteHttpExceptions: true };
    });
    var responses;
    try {
      responses = UrlFetchApp.fetchAll(requests);
    } catch (e) {
      // 배치 자체가 실패하면 이 청크는 건너뜀 (다음 요청 때 재시도됨)
      continue;
    }
    var toCache = {};
    for (var j = 0; j < chunk.length; j++) {
      var ym = chunk[j];
      var monthDeals = [];
      try { monthDeals = parseMonthXml_(responses[j].getContentText()); } catch (e) {}
      deals = deals.concat(monthDeals);
      var str = JSON.stringify(monthDeals);
      toCache["deal_" + lawd + "_" + ym] = (str.length < 95000) ? str : JSON.stringify(monthDeals.slice(0, 200));
    }
    try { cache.putAll(toCache, CACHE_SEC); } catch (e) {}   // 캐시 저장도 배치로
  }
  return deals;
}

function buildMonthUrl_(lawd, ym) {
  return ENDPOINT
    + "?serviceKey=" + encodeURIComponent(SERVICE_KEY)
    + "&LAWD_CD=" + encodeURIComponent(lawd)
    + "&DEAL_YMD=" + encodeURIComponent(ym)
    + "&numOfRows=1000&pageNo=1";
}

/** XML 응답 텍스트 → deals 배열 파싱 (fetchMonth_에서 분리) */
function parseMonthXml_(text) {
  if (text.indexOf("<") < 0 || (/Unauthorized|SERVICE_KEY|ERROR/i.test(text) && text.indexOf("<item>") < 0)) {
    throw new Error("API 응답 이상(인증/키 확인 필요): " + text.substring(0, 200));
  }
  var doc = XmlService.parse(text);
  var root = doc.getRootElement();
  var body = root.getChild("body"); if (!body) return [];
  var items = body.getChild("items"); if (!items) return [];
  var list = items.getChildren("item");
  var deals = [];
  for (var i = 0; i < list.length; i++) {
    var it = list[i];
    var amt = txt_(it, "dealAmount").replace(/[,\s]/g, "");
    var area = txt_(it, "excluUseAr");
    var floor = txt_(it, "floor");
    var apt = txt_(it, "aptNm") || txt_(it, "aptName") || txt_(it, "apt");
    var y = txt_(it, "dealYear"), m = txt_(it, "dealMonth"), dd = txt_(it, "dealDay");
    var built = txt_(it, "buildYear");
    var dong = txt_(it, "umdNm");
    var roadNm = txt_(it, "roadNm");
    var roadBon = (txt_(it, "roadNmBonbun") || "").replace(/^0+/, "");   // '00010' → '10'
    var jibun = txt_(it, "jibun");
    var manwon = parseInt(amt, 10) || 0;
    var dateNum = parseInt(y + pad2_(m) + pad2_(dd), 10) || 0;
    deals.push({
      apt: apt, dong: dong, amountManwon: manwon, amountText: toEokText_(manwon),
      areaM2: parseFloat(area) || 0,
      pyeong: area ? Math.round((parseFloat(area) / 3.3058)) : 0,
      floor: parseInt(floor, 10) || null, buildYear: parseInt(built, 10) || null,
      roadNm: roadNm, roadBon: roadBon, jibun: jibun,
      date: y + "." + pad2_(m) + "." + pad2_(dd), dateNum: dateNum
    });
  }
  return deals;
}

/** (구버전 호환용) 한 달치 — 캐시 먼저, 없으면 국토부 호출 후 캐시 저장 */
function fetchMonthCached_(lawd, ym) {
  var cache = CacheService.getScriptCache();
  var key = "deal_" + lawd + "_" + ym;
  var hit = cache.get(key);
  if (hit != null) { try { return JSON.parse(hit); } catch (e) {} }
  var deals = parseMonthXml_(UrlFetchApp.fetch(buildMonthUrl_(lawd, ym), { muteHttpExceptions: true }).getContentText());
  try {
    var str = JSON.stringify(deals);
    if (str.length < 95000) cache.put(key, str, CACHE_SEC);
    else cache.put(key, JSON.stringify(deals.slice(0, 200)), CACHE_SEC);
  } catch (e) {}
  return deals;
}

/** 도로명+건물번호 매칭 */
function matchByRoad_(deals, road, bun) {
  var r = String(road || "").replace(/\s/g, "");
  var b = String(bun || "").replace(/^0+/, "");
  return deals.filter(function (d) {
    return String(d.roadNm || "").replace(/\s/g, "") === r && String(d.roadBon || "") === b;
  });
}

/** 지번 매칭 */
function matchByJibun_(deals, jibun) {
  var j = String(jibun || "").replace(/\s/g, "");
  return deals.filter(function (d) { return String(d.jibun || "").replace(/\s/g, "") === j; });
}

/** 이름 정규화: 공백/괄호/구두점 제거, '아파트' 제거 */
function norm_(s) {
  s = String(s || "").replace(/[\s().·\-_,]/g, "");
  return s.replace(/아파트/g, "");
}

/** 단지번호 추출 ('N단지'의 N), 없으면 "" */
function danjiNo_(s) {
  var m = String(s || "").match(/(\d+)단지/);
  return m ? m[1] : "";
}

/** 이름 매칭 (기존 토큰 기반 로직 유지) */
function matchDeals_(deals, aptQuery) {
  var qn = norm_(aptQuery);
  var qDanji = danjiNo_(aptQuery);
  var exact = deals.filter(function (d) { return norm_(d.apt) === qn; });
  if (exact.length) return exact;

  var contains = deals.filter(function (d) {
    var dn = norm_(d.apt);
    return dn.length >= 2 && (qn.indexOf(dn) > -1 || dn.indexOf(qn) > -1);
  });
  if (qDanji) {
    var withDanji = contains.filter(function (d) { return danjiNo_(d.apt) === qDanji; });
    if (withDanji.length) return withDanji;
  }
  if (contains.length) return contains;

  // 토큰 기반 매칭 (마을명 등 핵심 단어 일치)
  var qTokens = qn.match(/.{2,}?(?=[A-Z가-힣]|$)/g) || [qn];
  for (var i = 1; i < qTokens.length; i++) {
    var tk = qTokens[0]; // 첫 토큰(마을/단지명) 기준
  }
  var head = qn.substring(0, Math.max(2, Math.floor(qn.length * 0.6)));
  var byHead = deals.filter(function (d) { return norm_(d.apt).indexOf(head) === 0 || head.indexOf(norm_(d.apt)) === 0; });
  return byHead;
}

function recentYearMonths_(n) {
  var arr = [], d = new Date();
  for (var i = 0; i < n; i++) {
    arr.push("" + d.getFullYear() + pad2_(d.getMonth() + 1));
    d.setMonth(d.getMonth() - 1);
  }
  return arr;
}
function toEokText_(manwon) {
  if (!manwon) return "";
  var eok = manwon / 10000;
  if (eok >= 10) return (Math.round(eok * 10) / 10).toString().replace(/\.0$/, "") + "억";
  return (Math.round(eok * 10) / 10) + "억";
}
function txt_(item, name) { var c = item.getChild(name); return c ? c.getText().trim() : ""; }
function pad2_(v) { v = "" + v; return v.length < 2 ? "0" + v : v; }
function json_(obj) { return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON); }

function 캐시비우기() { CacheService.getScriptCache().removeAll([]); Logger.log("캐시 비움(개별 키는 6시간 뒤 자동 만료)"); }
function TEST_연결확인() {
  var s = fetchMonthsBatch_("11680", recentYearMonths_(1));
  Logger.log("거래 수: " + s.length);
  if (s.length) Logger.log(JSON.stringify(s[0], null, 2));
}
function TEST_병렬속도() {
  var t0 = new Date().getTime();
  var deals = fetchMonthsBatch_("41113", recentYearMonths_(60));
  Logger.log("60개월 거래 수: " + deals.length + " / 소요(ms): " + (new Date().getTime() - t0));
}
function TEST_매칭() {
  var deals = fetchMonthsBatch_("41285", recentYearMonths_(12));
  ["호수마을5단지", "호수마을4단지럭키롯데", "강촌마을7단지"].forEach(function (q) {
    var r = matchDeals_(deals.slice(), q);
    Logger.log(q + " → " + r.length + "건 " + (r.length ? (r[0].apt + " " + r[0].amountText) : ""));
  });
}
