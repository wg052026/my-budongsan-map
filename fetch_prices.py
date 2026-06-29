#!/usr/bin/env python3
"""
실거래가 자동 수집 스크립트 (5년치)
매일 GitHub Actions에서 실행 — 국토부 API → prices.json 업데이트
"""
import json, re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

SERVICE_KEY = "b7f3b1b7a845ff366c079f48081a3732b4b3f9174e1df76a999e4350637bd3e7"
ENDPOINT    = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
PRICES_FILE = Path("prices.json")
PLACES_FILE = Path("data.js")
MAX_PER_RUN = 50      # 5년치(60개월)라 요청이 많아서 줄임
MONTHS      = 60      # 5년치
DELAY_SEC   = 0.4
TTL_DAYS    = 15

def norm(s):
    s = re.sub(r'[\s().·\-_,]', '', str(s or ''))
    return re.sub(r'아파트', '', s)

def recent_yms(n):
    yms = []
    d = datetime.now()
    for _ in range(n):
        yms.append(d.strftime('%Y%m'))
        d = d.replace(day=1) - timedelta(days=1)
    return yms

def fetch_month(lawd, ym):
    url = (ENDPOINT
           + "?serviceKey=" + urllib.parse.quote(SERVICE_KEY, safe='')
           + "&LAWD_CD=" + urllib.parse.quote(lawd)
           + "&DEAL_YMD=" + urllib.parse.quote(ym)
           + "&numOfRows=1000&pageNo=1")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"  [WARN] {lawd}/{ym} 요청 실패: {e}")
        return []
    deals = []
    for item in re.findall(r'<item>(.*?)</item>', text, re.DOTALL):
        def g(tag, _i=item):
            m = re.search(rf'<{tag}>(.*?)</{tag}>', _i)
            return m.group(1).strip() if m else ''
        amt    = re.sub(r'[,\s]', '', g('dealAmount'))
        area   = g('excluUseAr')
        manwon = int(amt) if amt.isdigit() else 0
        y, mo, dd = g('dealYear'), g('dealMonth'), g('dealDay')
        pyeong = round(float(area) / 3.3058) if area else 0
        eok = manwon / 10000
        amt_text = (str(round(eok*10)/10).rstrip('0').rstrip('.')+'억') if eok >= 10 else (str(round(eok*10)/10)+'억')
        deals.append({
            'apt':    g('aptNm') or g('aptName'),
            'manwon': manwon, 'text': amt_text, 'pyeong': pyeong,
            'roadNm': g('roadNm'),
            'roadBon': re.sub(r'^0+', '', g('roadNmBonbun') or '0'),
            'jibun':  g('jibun'),
            'date':   f"{y}.{mo.zfill(2)}.{dd.zfill(2)}",
            'dateNum': int(y+mo.zfill(2)+dd.zfill(2)) if y else 0,
            'amountText': amt_text,
        })
    return deals

def match_deals(deals, name, road, bun, jibun):
    if road and bun:
        r, b = road.replace(' ',''), re.sub(r'^0+','',bun)
        m = [d for d in deals if d['roadNm'].replace(' ','') == r and d['roadBon'] == b]
        if m: return m
    if jibun:
        m = [d for d in deals if d['jibun'].replace(' ','') == jibun.replace(' ','')]
        if m: return m
    if name:
        qn = norm(name)
        m = [d for d in deals if qn in norm(d['apt'])]
        if m: return m
        m = [d for d in deals if len(norm(d['apt'])) >= 3 and norm(d['apt']) in qn]
        if m: return m
    return []

def load_places():
    if not PLACES_FILE.exists():
        print(f"[ERROR] {PLACES_FILE} 없음"); return []
    text = PLACES_FILE.read_text(encoding='utf-8')
    print(f"[INFO] data.js 크기: {len(text)} bytes")
    m = re.search(r'window\.PLACES\s*=\s*\[', text)
    if not m:
        print("[ERROR] window.PLACES 없음"); return []
    arr_start = m.end() - 1
    depth, arr_end = 0, -1
    for i in range(arr_start, len(text)):
        if text[i] == '[': depth += 1
        elif text[i] == ']':
            depth -= 1
            if depth == 0: arr_end = i; break
    if arr_end < 0:
        print("[ERROR] 배열 끝 없음"); return []
    raw = text[arr_start:arr_end+1]
    raw = re.sub(r'(?<!")(\b(?:kind|name|disp|score|py|price|land|jong|stage|sub|spottype|note|group|seq|lat|lng|code|addr)\b)\s*:', r'"\1":', raw)
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    try:
        places = json.loads(raw)
        print(f"[INFO] places: {len(places)}개")
        return places
    except Exception as e:
        print(f"[ERROR] 파싱 실패: {e}"); return []

def price_key(p):
    name = str(p.get('name') or '')
    lat  = round(float(p['lat']), 4) if p.get('lat') is not None else 0
    lng  = round(float(p['lng']), 4) if p.get('lng') is not None else 0
    return f"{name}@{lat},{lng}"

def main():
    places = load_places()
    apts = [p for p in places
            if p.get('kind') == 'apt'
            and p.get('code')
            and p.get('lat') is not None
            and p.get('lng') is not None]
    print(f"[INFO] 대상 아파트: {len(apts)}개")

    cache = {}
    if PRICES_FILE.exists():
        try: cache = json.loads(PRICES_FILE.read_text(encoding='utf-8'))
        except: cache = {}

    TTL = TTL_DAYS * 24 * 3600
    now_ts = time.time()

    todo = [p for p in apts
            if not (cache.get(price_key(p)) or {}).get('ts')
            or now_ts - cache[price_key(p)]['ts'] >= TTL]

    print(f"[INFO] 수집 필요: {len(todo)}개 / 이번 실행: 최대 {MAX_PER_RUN}개")
    todo = todo[:MAX_PER_RUN]

    month_cache = {}
    done = 0

    for p in todo:
        lawd = str(p.get('code',''))[:5]
        if not lawd: continue
        name  = str(p.get('name') or '')
        addr  = str(p.get('addr') or p.get('note') or '')
        road_m = re.search(r'([가-힣\w]+로|[가-힣\w]+길)\s+(\d+)', addr)
        road = road_m.group(1) if road_m else ''
        bun  = road_m.group(2) if road_m else ''
        jibun_m = re.search(r'(\d+(?:-\d+)?)', addr)
        jibun = jibun_m.group(1) if (jibun_m and not road) else ''

        # 5년치 월별 수집
        all_deals = []
        for ym in recent_yms(MONTHS):
            k = (lawd, ym)
            if k not in month_cache:
                month_cache[k] = fetch_month(lawd, ym)
                time.sleep(DELAY_SEC)
            all_deals.extend(month_cache[k])

        matched = match_deals(all_deals, name, road, bun, jibun)
        matched.sort(key=lambda d: d['dateNum'], reverse=True)

        pk = price_key(p)
        if matched:
            latest = matched[0]
            # val: 최신 거래 (마커 표시용)
            # deals: 전체 매칭 거래 목록 (차트용) — 최대 200건
            cache[pk] = {
                'val': {
                    'amountText': latest['text'],
                    'pyeong': latest['pyeong'],
                    'date': latest['date'],
                },
                'deals': matched[:200],  # 차트용 5년치 전체
                'ts': now_ts
            }
            print(f"  ✓ {name}: {latest['text']} {latest['pyeong']}평 ({latest['date']}) [{len(matched)}건]")
        else:
            cache[pk] = {'val': {'none': True}, 'deals': [], 'ts': now_ts}
            print(f"  - {name}: 거래 없음")
        done += 1

    PRICES_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"\n[INFO] 완료: {done}개 수집, 캐시 총 {len(cache)}개")

if __name__ == '__main__':
    main()
