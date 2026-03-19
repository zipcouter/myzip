"""
집카우터 일일 실거래가 수집 스크립트
- 매일 오전 6시 KST (GitHub Actions)
- 이번달 + 전달 데이터 수집
- 어제까지 없던 새 데이터 = 오늘 신고분 → first_seen_date = 오늘
"""
import os
import sys
import time
import datetime
import requests
import xml.etree.ElementTree as ET
import urllib3
urllib3.disable_warnings()

from supabase import create_client

# ── 환경변수 ──────────────────────────────────────────────────────────────────
API_KEY      = os.environ.get('API_KEY', 'bba046226cfdba339da5237b76bfaff8d43c90ab08d4efda3a30f6bb87ab2486')
SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ SUPABASE_URL, SUPABASE_KEY 환경변수 필요")
    sys.exit(1)

supabase   = create_client(SUPABASE_URL, SUPABASE_KEY)
today      = datetime.date.today()
today_str  = today.isoformat()
cur_ym     = today.strftime('%Y%m')
prev_ym    = (today.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y%m')
DEAL_YMDS  = list({cur_ym, prev_ym})

# ── 전국 시군구 목록 ──────────────────────────────────────────────────────────
def get_all_sigungu():
    sido_codes = ['11','26','27','28','29','30','31','36',
                  '41','42','43','44','45','46','47','48','50']
    result = []
    for sido in sido_codes:
        url = (f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"
               f"?regcode_pattern={sido}*000&is_ignore_zero=true")
        try:
            res = requests.get(url, timeout=10)
            for item in res.json().get('regcodes', []):
                code = item['code'][:5]
                name = item['name']
                result.append((code, name))
        except Exception as e:
            print(f"  시군구 조회 실패 {sido}: {e}")
        time.sleep(0.1)
    return result

# ── API 호출 ──────────────────────────────────────────────────────────────────
def fetch_api(lawd_cd, deal_ymd, is_rent=False):
    path = ('RTMSDataSvcAptRent/getRTMSDataSvcAptRent' if is_rent
            else 'RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev')
    base  = f'http://apis.data.go.kr/1613000/{path}'
    items = []
    page  = 1
    while True:
        try:
            res  = requests.get(base, params={
                'serviceKey': API_KEY, 'LAWD_CD': lawd_cd,
                'DEAL_YMD': deal_ymd, 'pageNo': page, 'numOfRows': 100
            }, timeout=15, verify=False)
            root  = ET.fromstring(res.text)
            batch = root.findall('.//item')
            if not batch:
                break
            items.extend(batch)
            total = int(root.findtext('.//totalCount', '0'))
            if page * 100 >= total:
                break
            page += 1
            time.sleep(0.05)
        except Exception as e:
            print(f"  API 오류({lawd_cd},{deal_ymd}): {e}")
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
    if not is_rent:
        price   = int(g('dealAmount').replace(',', '') or '0')
        monthly = 0
        ttype   = '매매'
    else:
        price   = int(g('deposit').replace(',', '') or '0')
        monthly = int(g('monthlyRent').replace(',', '') or '0')
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

# ── Supabase 조회/삽입 ────────────────────────────────────────────────────────
def get_existing_keys(lawd_cd):
    """해당 lawd_cd의 기존 레코드 유니크 키 집합"""
    keys = set()
    try:
        prev_start = f'{prev_ym[:4]}-{prev_ym[4:]}-01'
        res = supabase.table('apt_trades')\
            .select('deal_date,apt_name,area,floor,price,trade_type')\
            .eq('lawd_cd', lawd_cd)\
            .gte('deal_date', prev_start)\
            .execute()
        for row in res.data:
            keys.add(
                f"{row['deal_date']}|{lawd_cd}|{row['apt_name']}"
                f"|{row['area']}|{row['floor']}|{row['price']}|{row['trade_type']}"
            )
    except Exception as e:
        print(f"  기존 키 조회 실패({lawd_cd}): {e}")
    return keys

def batch_insert(records):
    if not records:
        return 0
    inserted = 0
    for i in range(0, len(records), 100):
        batch = records[i:i+100]
        try:
            supabase.table('apt_trades').insert(batch).execute()
            inserted += len(batch)
        except Exception as e:
            print(f"  배치 삽입 오류: {e}")
            for r in batch:
                try:
                    supabase.table('apt_trades').insert(r).execute()
                    inserted += 1
                except:
                    pass
    return inserted

def cleanup_old_data():
    """2개월 이전 데이터 삭제 (DB 용량 관리)"""
    cutoff = (today.replace(day=1) - datetime.timedelta(days=32)).replace(day=1).isoformat()
    try:
        supabase.table('apt_trades').delete().lt('deal_date', cutoff).execute()
        print(f"✅ 오래된 데이터 삭제 (cutoff: {cutoff})")
    except Exception as e:
        print(f"  삭제 실패: {e}")

# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    print(f"=== 집카우터 일일 수집 시작: {today} ===")
    cleanup_old_data()

    print("시군구 목록 조회 중...")
    sigungu_list = get_all_sigungu()
    print(f"총 {len(sigungu_list)}개 시군구 대상\n")

    total_new = 0

    for i, (lawd_cd, name) in enumerate(sigungu_list):
        print(f"[{i+1}/{len(sigungu_list)}] {name} ({lawd_cd})")
        existing_keys = get_existing_keys(lawd_cd)
        new_records   = []

        for deal_ymd in DEAL_YMDS:
            for is_rent in [False, True]:
                for item in fetch_api(lawd_cd, deal_ymd, is_rent):
                    try:
                        r = parse_item(item, lawd_cd, name, is_rent)
                        if record_key(r) not in existing_keys:
                            new_records.append(r)
                            existing_keys.add(record_key(r))  # 중복 방지
                    except:
                        pass

        cnt = batch_insert(new_records)
        total_new += cnt
        if cnt > 0:
            print(f"  → 신규 {cnt}건")
        time.sleep(0.2)

    print(f"\n=== 완료: {today} 신규 등록 총 {total_new}건 ===")

if __name__ == '__main__':
    main()
