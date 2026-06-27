#!/usr/bin/env python3
"""
실거래가 자동 수집 스크립트
매일 GitHub Actions에서 실행 — 국토부 API → prices.json 업데이트
"""
import json, re, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

SERVICE_KEY = "b7f3b1b7a845ff366c079f48081a3732b4b3f9174e1df76a999e4350637bd3e7"
ENDPOINT    = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
PRICES_FILE = Path("prices.json")
PLACES_FILE = Path("data.js")
MAX_PER_RUN = 150   # 1회 실행당 최대 수집 개수 (API 부하 방지)
MONTHS      = 12    # 최근 N개월치 조회
DELAY_SEC   = 0.5   # 요청 사이 대기 (초)

# ─── 유틸 ───────────────────────────────────────────────
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
        def g(tag):
            m = re.search(rf'<{tag}>(.*?)</{tag}>', item)
            return m.group(1).strip() if m else ''
        amt   = re.sub(r'[,\s]', '', g('dealAmount'))
        area  = g('excluUseAr')
        manwon = int(amt) if amt.isdigit() else 0
        y, mo, dd = g('dealYear'), g('dealMonth'), g('dealDay')
        pyeong = round(float(area) / 3.3058) if area else 0
        eok = manwon / 10000
        if eok >= 10:
            amt_text = str(round(eok * 10) / 10).rstrip('0').rstrip('.') + '억'
        else:
            amt_text = str(round(eok * 10) / 10) + '억'
        deals.append({
            'apt':      g('aptNm') or g('aptName'),
            'dong':     g('umdNm'),
            'manwon':   manwon,
            'text':     amt_text,
            'pyeong':   pyeong,
            'roadNm':   g('roadNm'),
            'roadBon':  re.sub(r'^0+', '', g('roadNmBonbun') or '0'),
            'jibun':    g('jibun'),
            'date':     f"{y}.{mo.zfill(2)}.{dd.zfill(2)}",
            'dateNum':  int(y + mo.zfill(2) + dd.zfill(2)) if y else 0,
        })
    return deals

def match_price(deals, name, road, bun, jibun):
    """Code.gs와 동일한 매칭 로직 (road → jibun → name 순)"""
    if road and bun:
        r = road.replace(' ', '')
        b = re.sub(r'^0+', '', bun)
        matched = [d for d in deals
                   if d['roadNm'].replace(' ', '') == r and d['roadBon'] == b]
        if matched:
            return matched

    if jibun:
        j = jibun.replace(' ', '')
        matched = [d for d in deals if d['jibun'].replace(' ', '') == j]
        if matched:
            return matched

    if name:
        qn = norm(name)
        # 1차: 정규화 포함
        matched = [d for d in deals if qn in norm(d['apt'])]
        if matched: return matched
        # 1.5차: 역방향
        matched = [d for d in deals if len(norm(d['apt'])) >= 3 and norm(d['apt']) in qn]
        if matched: return matched

    return []

# ─── places 파싱 ─────────────────────────────────────────
def load_places():
    if not PLACES_FILE.exists():
        print(f"[ERROR] {PLACES_FILE} 없음")
        return []
    text = PLACES_FILE.read_text(encoding='utf-8')
    m = re.search(r'window\.PLACES\s*=\s*(\[.*?\]);', text, re.DOTALL)
    if not m:
        print("[ERROR] window.PLACES 파싱 실패")
        return []
    try:
        places = json.loads(m.group(1))
        print(f"[INFO] places: {len(places)}개 로드")
        return places
    except Exception as e:
        print(f"[ERROR] JSON 파싱 실패: {e}")
        return []

def price_key(p):
    name = str(p.get('name') or '')
    lat  = round(float(p['lat']), 4) if p.get('lat') is not None else 0
    lng  = round(float(p['lng']), 4) if p.get('lng') is not None else 0
    return f"{name}@{lat},{lng}"

# ─── 메인 ────────────────────────────────────────────────
def main():
    places = load_places()
    # 아파트만 (kind='apt')
    apts = [p for p in places
            if p.get('kind') == 'apt'
            and p.get('code')
            and p.get('lat') is not None
            and p.get('lng') is not None]
    print(f"[INFO] 대상 아파트: {len(apts)}개")

    # 기존 캐시 로드
    if PRICES_FILE.exists():
        try:
            cache = json.loads(PRICES_FILE.read_text(encoding='utf-8'))
        except:
            cache = {}
    else:
        cache = {}

    TTL = 30 * 24 * 3600  # 30일
    now_ts = time.time()

    # 만료/미수집 대상만 추리기
    todo = []
    for p in apts:
        k = price_key(p)
        entry = cache.get(k)
        if entry and isinstance(entry, dict) and entry.get('ts'):
            if now_ts - entry['ts'] < TTL:
                continue  # 유효한 캐시 있음 → 건너뜀
        todo.append(p)

    print(f"[INFO] 수집 필요: {len(todo)}개 / 이번 실행: 최대 {MAX_PER_RUN}개")
    todo = todo[:MAX_PER_RUN]

    # lawd별로 월별 데이터를 한 번씩만 가져오기 (중복 API 호출 방지)
    month_cache = {}  # (lawd, ym) → deals

    done = 0
    for p in todo:
        lawd = str(p.get('code', ''))[:5]
        if not lawd:
            continue

        name  = str(p.get('name') or '')
        addr  = str(p.get('addr') or '')
        # 주소 파싱
        road_m = re.search(r'([가-힣\w]+로|[가-힣\w]+길)\s+(\d+)', addr)
        road = road_m.group(1) if road_m else ''
        bun  = road_m.group(2) if road_m else ''
        jibun_m = re.search(r'(\d+(?:-\d+)?)', addr)
        jibun = jibun_m.group(1) if jibun_m and not road else ''

        # 월별 데이터 수집
        all_deals = []
        for ym in recent_yms(MONTHS):
            key_ym = (lawd, ym)
            if key_ym not in month_cache:
                month_cache[key_ym] = fetch_month(lawd, ym)
                time.sleep(DELAY_SEC)
            all_deals.extend(month_cache[key_ym])

        # 매칭
        matched = match_price(all_deals, name, road, bun, jibun)
        matched.sort(key=lambda d: d['dateNum'], reverse=True)

        pk = price_key(p)
        if matched:
            d = matched[0]
            cache[pk] = {
                'val': {
                    'amountText': d['text'],
                    'pyeong':     d['pyeong'],
                    'date':       d['date'],
                },
                'ts': now_ts
            }
            print(f"  ✓ {name}: {d['text']} {d['pyeong']}평 ({d['date']})")
        else:
            cache[pk] = {'val': {'none': True}, 'ts': now_ts}
            print(f"  - {name}: 거래 없음")

        done += 1

    # 저장
    PRICES_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"\n[INFO] 완료: {done}개 수집, 캐시 총 {len(cache)}개 → {PRICES_FILE}")

if __name__ == '__main__':
    main()
