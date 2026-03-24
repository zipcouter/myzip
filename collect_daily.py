"""
집카우터 일일 실거래가 수집 스크립트 (최적화 버전)
- 병렬 처리 (10개 동시) → 30분 이내 완료 목표
- 이번 달 + 전달 데이터, 신규 건만 Supabase 삽입
"""
import os
import sys
import time
import datetime
import requests
import xml.etree.ElementTree as ET
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from supabase import create_client

urllib3.disable_warnings()

# ── 환경변수 ──────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get('API_KEY', '')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

if not all([API_KEY, SUPABASE_URL, SUPABASE_KEY]):
    print("❌ 환경변수 누락: API_KEY, SUPABASE_URL, SUPABASE_KEY 필요")
    sys.exit(1)

supabase  = create_client(SUPABASE_URL, SUPABASE_KEY)
import pytz
today = datetime.datetime.now(pytz.timezone('Asia/Seoul')).date()
today_str = today.isoformat()
cur_ym    = today.strftime('%Y%m')
prev_ym   = (today.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y%m')
DEAL_YMDS = [cur_ym, prev_ym]

# ── 전국 시군구 코드 (하드코딩) ───────────────────────────────────────────────
SIGUNGU_LIST = [
    ("11110","서울 종로구"),("11140","서울 중구"),("11170","서울 용산구"),
    ("11200","서울 성동구"),("11215","서울 광진구"),("11230","서울 동대문구"),
    ("11260","서울 중랑구"),("11290","서울 성북구"),("11305","서울 강북구"),
    ("11320","서울 도봉구"),("11350","서울 노원구"),("11380","서울 은평구"),
    ("11410","서울 서대문구"),("11440","서울 마포구"),("11470","서울 양천구"),
    ("11500","서울 강서구"),("11530","서울 구로구"),("11545","서울 금천구"),
    ("11560","서울 영등포구"),("11590","서울 동작구"),("11620","서울 관악구"),
    ("11650","서울 서초구"),("11680","서울 강남구"),("11710","서울 송파구"),
    ("11740","서울 강동구"),
    ("26110","부산 중구"),("26140","부산 서구"),("26170","부산 동구"),
    ("26200","부산 영도구"),("26230","부산 부산진구"),("26260","부산 동래구"),
    ("26290","부산 남구"),("26320","부산 북구"),("26350","부산 해운대구"),
    ("26380","부산 사하구"),("26410","부산 금정구"),("26440","부산 강서구"),
    ("26470","부산 연제구"),("26500","부산 수영구"),("26530","부산 사상구"),
    ("26710","부산 기장군"),
    ("27110","대구 중구"),("27140","대구 동구"),("27170","대구 서구"),
    ("27200","대구 남구"),("27230","대구 북구"),("27260","대구 수성구"),
    ("27290","대구 달서구"),("27710","대구 달성군"),("27720","대구 군위군"),
    ("28110","인천 중구"),("28140","인천 동구"),("28177","인천 미추홀구"),
    ("28185","인천 연수구"),("28200","인천 남동구"),("28237","인천 부평구"),
    ("28245","인천 계양구"),("28260","인천 서구"),("28710","인천 강화군"),
    ("28720","인천 옹진군"),
    ("29110","광주 동구"),("29140","광주 서구"),("29155","광주 남구"),
    ("29170","광주 북구"),("29200","광주 광산구"),
    ("30110","대전 동구"),("30140","대전 중구"),("30170","대전 서구"),
    ("30200","대전 유성구"),("30230","대전 대덕구"),
    ("31110","울산 중구"),("31140","울산 남구"),("31170","울산 동구"),
    ("31200","울산 북구"),("31710","울산 울주군"),
    ("36110","세종 세종시"),
    ("41111","경기 수원시 장안구"),("41113","경기 수원시 권선구"),
    ("41115","경기 수원시 팔달구"),("41117","경기 수원시 영통구"),
    ("41131","경기 성남시 수정구"),("41133","경기 성남시 중원구"),
    ("41135","경기 성남시 분당구"),("41150","경기 의정부시"),
    ("41171","경기 안양시 만안구"),("41173","경기 안양시 동안구"),
    ("41190","경기 부천시"),("41210","경기 광명시"),("41220","경기 평택시"),
    ("41250","경기 동두천시"),("41271","경기 안산시 상록구"),
    ("41273","경기 안산시 단원구"),("41281","경기 고양시 덕양구"),
    ("41285","경기 고양시 일산동구"),("41287","경기 고양시 일산서구"),
    ("41290","경기 과천시"),("41310","경기 구리시"),("41360","경기 남양주시"),
    ("41370","경기 오산시"),("41390","경기 시흥시"),("41410","경기 군포시"),
    ("41430","경기 의왕시"),("41450","경기 하남시"),("41461","경기 용인시 처인구"),
    ("41463","경기 용인시 기흥구"),("41465","경기 용인시 수지구"),
    ("41480","경기 파주시"),("41500","경기 이천시"),("41550","경기 안성시"),
    ("41570","경기 김포시"),("41590","경기 화성시"),("41610","경기 광주시"),
    ("41630","경기 양주시"),("41650","경기 포천시"),("41670","경기 여주시"),
    ("41800","경기 연천군"),("41820","경기 가평군"),("41830","경기 양평군"),
    ("42110","강원 춘천시"),("42130","강원 원주시"),("42150","강원 강릉시"),
    ("42170","강원 동해시"),("42190","강원 태백시"),("42210","강원 속초시"),
    ("42230","강원 삼척시"),("42720","강원 홍천군"),("42730","강원 횡성군"),
    ("42750","강원 영월군"),("42760","강원 평창군"),("42770","강원 정선군"),
    ("42780","강원 철원군"),("42790","강원 화천군"),("42800","강원 양구군"),
    ("42810","강원 인제군"),("42820","강원 고성군"),("42830","강원 양양군"),
    ("43111","충북 청주시 상당구"),("43112","충북 청주시 서원구"),
    ("43113","충북 청주시 흥덕구"),("43114","충북 청주시 청원구"),
    ("43130","충북 충주시"),("43150","충북 제천시"),("43720","충북 보은군"),
    ("43730","충북 옥천군"),("43740","충북 영동군"),("43745","충북 증평군"),
    ("43750","충북 진천군"),("43760","충북 괴산군"),("43770","충북 음성군"),
    ("43800","충북 단양군"),
    ("44131","충남 천안시 동남구"),("44133","충남 천안시 서북구"),
    ("44150","충남 공주시"),("44180","충남 보령시"),("44200","충남 아산시"),
    ("44210","충남 서산시"),("44230","충남 논산시"),("44250","충남 계룡시"),
    ("44270","충남 당진시"),("44710","충남 금산군"),("44760","충남 부여군"),
    ("44770","충남 서천군"),("44790","충남 청양군"),("44800","충남 홍성군"),
    ("44810","충남 예산군"),("44825","충남 태안군"),
    ("45111","전북 전주시 완산구"),("45113","전북 전주시 덕진구"),
    ("45130","전북 군산시"),("45140","전북 익산시"),("45180","전북 정읍시"),
    ("45190","전북 남원시"),("45210","전북 김제시"),("45710","전북 완주군"),
    ("45720","전북 진안군"),("45730","전북 무주군"),("45740","전북 장수군"),
    ("45750","전북 임실군"),("45770","전북 순창군"),("45790","전북 고창군"),
    ("45800","전북 부안군"),
    ("46110","전남 목포시"),("46130","전남 여수시"),("46150","전남 순천시"),
    ("46170","전남 나주시"),("46230","전남 광양시"),("46710","전남 담양군"),
    ("46720","전남 곡성군"),("46730","전남 구례군"),("46770","전남 고흥군"),
    ("46780","전남 보성군"),("46790","전남 화순군"),("46800","전남 장흥군"),
    ("46810","전남 강진군"),("46820","전남 해남군"),("46830","전남 영암군"),
    ("46840","전남 무안군"),("46860","전남 함평군"),("46870","전남 영광군"),
    ("46880","전남 장성군"),("46890","전남 완도군"),("46900","전남 진도군"),
    ("46910","전남 신안군"),
    ("47111","경북 포항시 남구"),("47113","경북 포항시 북구"),
    ("47130","경북 경주시"),("47150","경북 김천시"),("47170","경북 안동시"),
    ("47190","경북 구미시"),("47210","경북 영주시"),("47230","경북 영천시"),
    ("47250","경북 상주시"),("47280","경북 문경시"),("47290","경북 경산시"),
    ("47720","경북 의성군"),("47730","경북 청송군"),("47740","경북 영양군"),
    ("47750","경북 영덕군"),("47760","경북 청도군"),("47770","경북 고령군"),
    ("47780","경북 성주군"),("47790","경북 칠곡군"),("47820","경북 예천군"),
    ("47830","경북 봉화군"),("47840","경북 울진군"),("47850","경북 울릉군"),
    ("48121","경남 창원시 의창구"),("48123","경남 창원시 성산구"),
    ("48125","경남 창원시 마산합포구"),("48127","경남 창원시 마산회원구"),
    ("48129","경남 창원시 진해구"),("48170","경남 진주시"),
    ("48220","경남 통영시"),("48240","경남 사천시"),("48250","경남 김해시"),
    ("48270","경남 밀양시"),("48310","경남 거제시"),("48330","경남 양산시"),
    ("48720","경남 의령군"),("48730","경남 함안군"),("48740","경남 창녕군"),
    ("48820","경남 고성군"),("48840","경남 남해군"),("48850","경남 하동군"),
    ("48860","경남 산청군"),("48870","경남 함양군"),("48880","경남 거창군"),
    ("48890","경남 합천군"),
    ("50110","제주 제주시"),("50130","제주 서귀포시"),
]

# ── API 호출 ──────────────────────────────────────────────────────────────────
def fetch_api(lawd_cd, deal_ymd, is_rent=False):
    path = ('RTMSDataSvcAptRent/getRTMSDataSvcAptRent' if is_rent
            else 'RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev')
    base  = f'http://apis.data.go.kr/1613000/{path}'
    items = []
    page  = 1
    while True:
        try:
            res = requests.get(base, params={
                'serviceKey': API_KEY, 'LAWD_CD': lawd_cd,
                'DEAL_YMD': deal_ymd, 'pageNo': page, 'numOfRows': 100
            }, timeout=20, verify=False)
            root  = ET.fromstring(res.text)
            batch = root.findall('.//item')
            if not batch:
                break
            items.extend(batch)
            total = int(root.findtext('.//totalCount', '0'))
            if page * 100 >= total:
                break
            page += 1
        except Exception:
            break
    return items

# ── 파싱 ─────────────────────────────────────────────────────────────────────
def parse_item(item, lawd_cd, sigungu_name, is_rent):
    def g(tag):
        el = item.find(tag)
        return (el.text or '').strip() if el is not None else ''
    y = g('dealYear') or g('년')
    m = (g('dealMonth') or g('월')).zfill(2)
    d = (g('dealDay')   or g('일')).zfill(2)
    if not (y and m and d):
        return None
    if not is_rent:
        price, monthly, ttype = int(g('dealAmount').replace(',','') or '0'), 0, '매매'
    else:
        price   = int(g('deposit').replace(',','') or '0')
        monthly = int(g('monthlyRent').replace(',','') or '0')
        ttype   = '월세' if monthly > 0 else '전세'
    return {
        'first_seen_date': today_str,
        'deal_date':    f'{y}-{m}-{d}',
        'lawd_cd':      lawd_cd,
        'sigungu':      sigungu_name,
        'dong':         g('umdNm'),
        'apt_name':     g('aptNm'),
        'jibun':        g('jibun'),
        'area':         g('excluUseAr') or g('exclUseAr'),
        'floor':        g('floor'),
        'price':        price,
        'monthly_rent': monthly,
        'trade_type':   ttype,
        'build_year':   g('buildYear'),
        'trade_gbn':    g('dealingGbn') or g('reqGbn'),
    }

def record_key(r):
    return (f"{r['deal_date']}|{r['lawd_cd']}|{r['apt_name']}"
            f"|{r['area']}|{r['floor']}|{r['price']}|{r['trade_type']}")

# ── Supabase 기존 키 일괄 로딩 ───────────────────────────────────────────────
def get_existing_keys_bulk():
    keys = set()
    prev_start = f'{prev_ym[:4]}-{prev_ym[4:]}-01'
    offset = 0
    while True:
        try:
            res = supabase.table('apt_trades')\
                .select('deal_date,lawd_cd,apt_name,area,floor,price,trade_type')\
                .gte('deal_date', prev_start)\
                .range(offset, offset + 999)\
                .execute()
            batch = res.data
            if not batch:
                break
            for row in batch:
                keys.add(
                    f"{row['deal_date']}|{row['lawd_cd']}|{row['apt_name']}"
                    f"|{row['area']}|{row['floor']}|{row['price']}|{row['trade_type']}"
                )
            if len(batch) < 1000:
                break
            offset += 1000
        except Exception as e:
            print(f"  기존 키 조회 오류: {e}")
            break
    print(f"  기존 DB: {len(keys):,}건")
    return keys

# ── 시군구 단위 수집 (스레드용) ───────────────────────────────────────────────
def collect_sigungu(args):
    lawd_cd, name, existing_keys = args
    new_records = []
    seen = set()
    for deal_ymd in DEAL_YMDS:
        for is_rent in [False, True]:
            for item in fetch_api(lawd_cd, deal_ymd, is_rent):
                try:
                    r = parse_item(item, lawd_cd, name, is_rent)
                    if r is None:
                        continue
                    k = record_key(r)
                    if k not in existing_keys and k not in seen:
                        new_records.append(r)
                        seen.add(k)
                except Exception:
                    pass
    return lawd_cd, name, new_records

# ── 배치 삽입 ─────────────────────────────────────────────────────────────────
def batch_insert(records):
    if not records:
        return 0
    inserted = 0
    for i in range(0, len(records), 200):
        try:
            supabase.table('apt_trades').insert(records[i:i+200]).execute()
            inserted += len(records[i:i+200])
        except Exception as e:
            print(f"  삽입 오류: {e}")
    return inserted

def cleanup_old_data():
    cutoff = (today.replace(day=1) - datetime.timedelta(days=32)).replace(day=1).isoformat()
    try:
        supabase.table('apt_trades').delete().lt('deal_date', cutoff).execute()
        print(f"✅ 오래된 데이터 삭제 (cutoff: {cutoff})")
    except Exception as e:
        print(f"  삭제 실패: {e}")

# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    print(f"=== 집카우터 일일 수집 시작: {today} ===")
    print(f"대상 월: {DEAL_YMDS} / 총 {len(SIGUNGU_LIST)}개 시군구\n")

    cleanup_old_data()

    print("기존 DB 로딩 중...")
    existing_keys = get_existing_keys_bulk()

    total_new = 0
    done = 0
    args_list = [(cd, name, existing_keys) for cd, name in SIGUNGU_LIST]

    print(f"병렬 수집 시작 (10개 동시)...\n")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(collect_sigungu, a): a for a in args_list}
        for future in as_completed(futures):
            try:
                lawd_cd, name, new_records = future.result()
                done += 1
                if new_records:
                    cnt = batch_insert(new_records)
                    total_new += cnt
                    print(f"[{done}/{len(SIGUNGU_LIST)}] {name}: +{cnt}건")
                else:
                    print(f"[{done}/{len(SIGUNGU_LIST)}] {name}: 신규없음")
            except Exception as e:
                done += 1
                print(f"[{done}/{len(SIGUNGU_LIST)}] 오류: {e}")

    print(f"\n=== 완료: {today} 신규 {total_new:,}건 ===")

if __name__ == '__main__':
    main()
