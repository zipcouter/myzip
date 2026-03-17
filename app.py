import streamlit as st
import pandas as pd
import datetime
import os
import plotly.graph_objects as go
import requests
import xml.etree.ElementTree as ET
import uuid
import urllib3
import time
from geopy.geocoders import Nominatim
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------
# ⚙️ 페이지 설정
# ---------------------------------------------------------
st.set_page_config(page_title="집카우터 | 실거래가 실시간 조회", layout="wide")

# API 키: st.secrets → 환경변수 → 하드코딩 순서로 fallback
_HARDCODED_KEY = "bba046226cfdba339da5237b76bfaff8d43c90ab08d4efda3a30f6bb87ab2486"
try:
    MOLIT_API_KEY = st.secrets["MOLIT_API_KEY"]
except Exception:
    MOLIT_API_KEY = os.environ.get("MOLIT_API_KEY", _HARDCODED_KEY)

ITEMS_PER_PAGE = 50  # 페이지당 표시 건수

if "table_key" not in st.session_state:
    st.session_state.table_key = str(uuid.uuid4())

with st.sidebar:
    st.title("🚀 집카우터 메뉴")
    st.write("원하시는 시장을 선택하세요.")
    page = st.radio("조회 메뉴", ["🏢 아파트 실거래가", "🏘️ 비아파트 (오피스텔/빌라 등)"])
    st.write("---")
    st.caption("v3.5 - 안정화 배포버전")


# ---------------------------------------------------------
# 🌟 실시간 전국 지역코드 연동 엔진
# ---------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=86400)
def get_sido_list():
    try:
        res = requests.get(
            "https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern=*00000000",
            timeout=5, verify=False
        ).json()
        return {item["name"]: item["code"][:2] for item in res.get("regcodes", [])}
    except Exception:
        return {"서울특별시": "11", "경기도": "41"}


@st.cache_data(show_spinner=False, ttl=86400)
def get_sigungu_list(sido_code):
    try:
        res = requests.get(
            f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"
            f"?regcode_pattern={sido_code}*00000&is_ignore_zero=true",
            timeout=5, verify=False
        ).json()
        sigungu_dict = {}
        for item in res.get("regcodes", []):
            code = item["code"]
            if code[2:5] == "000":
                continue
            name = item["name"].replace(item["name"].split()[0], "").strip()
            sigungu_dict[name] = code[:5]
        return sigungu_dict
    except Exception:
        return {"강남구": "11680"}


@st.cache_data(show_spinner=False, ttl=86400)
def get_dong_list(sigungu_code):
    try:
        res = requests.get(
            f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"
            f"?regcode_pattern={sigungu_code}*&is_ignore_zero=true",
            timeout=5, verify=False
        ).json()
        dongs = []
        for item in res.get("regcodes", []):
            if item["code"][5:] == "00000":
                continue
            dong = item["name"].split()[-1]
            if dong not in dongs:
                dongs.append(dong)
        return dongs
    except Exception:
        return []


# ---------------------------------------------------------
# 🚨 무료 지도 엔진 (Geopy) - rate limit 대응
# ---------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=86400)
def get_lat_lng_free(sido, sigungu, dong, apt_name):
    try:
        geolocator = Nominatim(user_agent="zip_counter_app_v3")
        # ✅ FIX 7: Nominatim 1초 제한 준수
        location = geolocator.geocode(f"{sido} {sigungu} {dong} {apt_name}")
        if location:
            return location.latitude, location.longitude
        time.sleep(1)
        location_dong = geolocator.geocode(f"{sido} {sigungu} {dong}")
        if location_dong:
            return location_dong.latitude, location_dong.longitude
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------
# 🌟 유틸리티 함수들
# ---------------------------------------------------------
PERIOD_OPTIONS = ["오늘", "이번 달", "최근 3개월", "최근 6개월", "최근 1년", "직접 설정"]


def format_to_korean_currency(price_manwon):
    try:
        val = int(price_manwon)
    except Exception:
        return str(price_manwon)
    eok = val // 10000
    remainder = val % 10000
    result = ""
    if eok > 0:
        result += f"{eok}억"
    if remainder > 0:
        cheon = remainder // 1000
        baek = (remainder % 1000) // 100
        parts = []
        if cheon > 0:
            parts.append(f"{cheon}천")
        if baek > 0:
            parts.append(f"{baek}백")
        rem_str = " ".join(parts) + "만원"
        result = f"{result} {rem_str}" if result else rem_str
    return result if result else "0원"


def get_xml_text(item, tags, default=""):
    lower_tags = {t.strip().lower() for t in tags}
    for child in item.iter():
        tag_name = child.tag.split("}")[-1].strip().lower()
        if tag_name in lower_tags:
            if child.text is not None and child.text.strip():
                return child.text.strip()
    return default


def find_related_apt_names(df, apt_name, jibun):
    """같은 지번의 모든 단지명 반환 (상세 페이지용)"""
    if df.empty:
        return [apt_name], apt_name
    related_names = [apt_name]
    representative = apt_name
    if jibun and str(jibun).strip():
        same_jibun = df[df["지번"] == str(jibun).strip()]
        if not same_jibun.empty:
            related_names = same_jibun["단지명"].dropna().unique().tolist()
            counts = same_jibun["단지명"].value_counts()
            representative = counts.index[0] if not counts.empty else apt_name
    return related_names, representative


def get_recent_months(n):
    today = datetime.date.today()
    months = []
    for i in range(n):
        month = today.month - i
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        months.append(f"{year}{month:02d}")
    return months


def get_months_from_dates(start_d, end_d):
    months = []
    y, m = start_d.year, start_d.month
    while (y < end_d.year) or (y == end_d.year and m <= end_d.month):
        months.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


# ✅ FIX 8: 기간 계산 중복 로직 함수화
def resolve_months(period, custom_dates=None):
    """기간 설정값을 (months_list, start_date, end_date) 튜플로 반환"""
    if period == "오늘":
        today = datetime.date.today()
        return [today.strftime("%Y%m")], today, today
    elif period == "직접 설정":
        if custom_dates and len(custom_dates) == 2:
            start_date, end_date = custom_dates
            return get_months_from_dates(start_date, end_date), start_date, end_date
        return None, None, None
    else:
        n_map = {"최근 1년": 12, "최근 6개월": 6, "최근 3개월": 3, "이번 달": 1}
        n = n_map.get(period, 1)
        return get_recent_months(n), None, None


def apply_date_filter(df, period, start_date, end_date):
    """계약일 기준 날짜 필터 적용"""
    if period == "오늘":
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        return df[df["계약일"] == today_str]
    elif period == "직접 설정" and start_date and end_date:
        return df[
            (df["계약일"] >= start_date.strftime("%Y-%m-%d")) &
            (df["계약일"] <= end_date.strftime("%Y-%m-%d"))
        ]
    return df


def apply_area_filter(df, pyeong_type, is_apt=True):
    """평형대 필터 적용"""
    if pyeong_type == "전체보기":
        return df

    def get_area_num(area_str):
        try:
            return float(str(area_str).replace("㎡", "").strip())
        except Exception:
            return 0.0

    area_series = df["전용면적"].apply(get_area_num)

    if is_apt:
        if "10평대" in pyeong_type:
            return df[area_series < 59.0]
        elif "20평대" in pyeong_type:
            return df[(area_series >= 59.0) & (area_series < 84.0)]
        elif "30평대" in pyeong_type:
            return df[area_series >= 84.0]
    else:
        if "원룸형" in pyeong_type:
            return df[area_series < 30.0]
        elif "투룸형" in pyeong_type:
            return df[(area_series >= 30.0) & (area_series < 59.0)]
        elif "쓰리룸형" in pyeong_type:
            return df[area_series >= 59.0]
    return df


# ---------------------------------------------------------
# 🏗️ 건축물대장 — 완전 재작성 v4
# ---------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_building_ledger_v4(sigungu_cd, dong_name, jibun):
    """
    건축물대장 총괄표제부 조회 (v4 완전 재작성)
    반환: (주차대수, 세대수, 용적률_str, 건폐율_str, 디버그_msg)
    """
    # ── STEP 1. 입력값 검증 ────────────────────────────────────────
    if not jibun or str(jibun).strip() == "":
        return None, None, None, None, "지번 정보 없음"

    # ── STEP 2. 법정동 5자리 코드 획득 ────────────────────────────
    bjdong_cd = ""
    try:
        r = requests.get(
            "https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes",
            params={"regcode_pattern": f"{sigungu_cd}*", "is_ignore_zero": "true"},
            timeout=6, verify=False
        )
        for row in r.json().get("regcodes", []):
            if dong_name in row["name"]:
                bjdong_cd = row["code"][5:10]
                break
    except Exception as e:
        return None, None, None, None, f"법정동코드 API 오류: {e}"

    if not bjdong_cd:
        return None, None, None, None, f"법정동코드 변환 실패 (dong={dong_name}, sigungu={sigungu_cd})"

    # ── STEP 3. 지번 파싱 ─────────────────────────────────────────
    jibun_str   = str(jibun).strip()
    plat_gb_cd  = "1" if "산" in jibun_str else "0"
    clean       = jibun_str.replace("산", "").strip()
    parts       = clean.split("-")
    bun         = parts[0].strip().zfill(4)
    ji          = parts[1].strip().zfill(4) if len(parts) > 1 else "0000"

    # ── STEP 4. API 호출 (https → http fallback) ──────────────────
    base_params = (
        f"serviceKey={MOLIT_API_KEY}"
        f"&sigunguCd={sigungu_cd}&bjdongCd={bjdong_cd}"
        f"&platGbCd={plat_gb_cd}&bun={bun}&ji={ji}"
        f"&numOfRows=50&_type=xml"   # 50개로 확대 (같은 지번에 여러 건물 대응)
    )
    urls = [
        f"https://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo?{base_params}",
        f"http://apis.data.go.kr/1613000/BldRgstHubService/getBrRecapTitleInfo?{base_params}",
    ]

    raw_xml = ""
    for url in urls:
        try:
            resp = requests.get(url, timeout=12, verify=False)
            raw_xml = resp.text
            if resp.status_code != 200:
                continue

            # ── STEP 5. XML 파싱 ──────────────────────────────────
            root = ET.fromstring(raw_xml)

            # API 오류 코드 체크
            rc = root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "00"
            if rc.strip() not in ("00", "0", ""):
                rm = root.findtext(".//resultMsg") or root.findtext(".//returnReasonMsg") or rc
                continue  # 다음 URL 시도

            # ── STEP 6. item 추출 → 공동주택 우선 선택 ──────────
            xml_items = root.findall(".//item")
            if not xml_items:
                return None, None, None, None, (
                    f"데이터 없음 (sigungu={sigungu_cd}, bjdong={bjdong_cd}, "
                    f"platGb={plat_gb_cd}, bun={bun}, ji={ji})\n\n{raw_xml[:1000]}"
                )

            # 공동주택 용도코드 목록
            RESIDENTIAL_CODES = {
                "02000",  # 공동주택
                "02100",  # 아파트
                "02200",  # 연립주택
                "02300",  # 다세대주택
                "02400",  # 기숙사
            }

            def item_to_dict(item_el):
                d = {}
                for child in item_el.iter():
                    k = child.tag.split("}")[-1].strip().lower()
                    v = (child.text or "").strip()
                    d[k] = v
                return d

            # 모든 item을 dict로 변환
            all_item_dicts = [item_to_dict(it) for it in xml_items]

            # 공동주택인 item 우선 선택, 없으면 첫 번째 사용
            tag_dict = None
            for d in all_item_dicts:
                purps_cd = d.get("mainpurpscd", "")
                purps_nm = d.get("mainpurpscdnm", "")
                if purps_cd in RESIDENTIAL_CODES or "공동주택" in purps_nm or "아파트" in purps_nm:
                    tag_dict = d
                    break

            if tag_dict is None:
                # 공동주택 없으면: platArea가 0이 아닌 item 우선
                for d in all_item_dicts:
                    try:
                        if float(d.get("platarea", "0") or "0") > 0:
                            tag_dict = d
                            break
                    except:
                        pass

            if tag_dict is None:
                tag_dict = all_item_dicts[0]  # 최후 fallback

            def g(key):
                """태그값 조회, 없으면 '0'"""
                return tag_dict.get(key.lower(), "0") or "0"

            def gf(key):
                """float 변환, 실패시 0.0"""
                try: return float(g(key))
                except: return 0.0

            def gi(key):
                """int 변환, 실패시 0"""
                try: return int(float(g(key)))
                except: return 0

            # ── STEP 7. 세대수 ────────────────────────────────────
            # hhldCnt: 세대수 (집합건물 총괄표제부 핵심 필드)
            # hoCnt  : 호수   (일반건축물)
            # hhCnt  : 구버전 호환
            tot_hh = 0
            for hk in ["hhldcnt", "hocnt", "hhcnt"]:
                n = gi(hk)
                if n > 0:
                    tot_hh = n
                    break

            # ── STEP 8. 주차대수 ──────────────────────────────────
            tot_pkng = gi("totpkngcnt")
            if tot_pkng == 0:
                # 세부 항목 합산
                for pk in ["oudrmechutcnt", "indrmechutcnt", "oudrautoutcnt", "indrautoutcnt"]:
                    tot_pkng += gi(pk)

            # ── STEP 9. 용적률 / 건폐율 ──────────────────────────
            vl_rat = gf("vlrat")
            bc_rat = gf("bcrat")

            # API가 0으로 내려보낼 때 면적값으로 직접 계산
            plat_area = gf("platarea")
            vl_estm   = gf("vlratestmtotarea")
            arch_area = gf("archarea")

            if vl_rat <= 0 and plat_area > 0 and vl_estm > 0:
                vl_rat = round(vl_estm / plat_area * 100, 2)

            if bc_rat <= 0 and plat_area > 0 and arch_area > 0:
                bc_rat = round(arch_area / plat_area * 100, 2)

            vl_str = f"{vl_rat}%" if vl_rat > 0 else "정보없음"
            bc_str = f"{bc_rat}%" if bc_rat > 0 else "정보없음"

            # ── STEP 10. 도로명 주소 추출 ────────────────────────
            road_addr = g("newplatplc")   # 예: 서울특별시 노원구 마들로 111 (월계동)

            # ── STEP 11. 디버그 메시지 ────────────────────────────
            all_tag_lines = "\n".join(
                f"  {k} = {v}" for k, v in tag_dict.items()
            )
            debug_msg = (
                f"[v4 파싱 결과]\n"
                f"  선택건물  : {g('mainpurpscdnm')} (총 {len(all_item_dicts)}개 건물 중 선택)\n"
                f"  도로명주소: {road_addr}\n"
                f"  세대수    : {tot_hh}  (hhldCnt={g('hhldcnt')}, hoCnt={g('hocnt')})\n"
                f"  주차대수  : {tot_pkng}  (totPkngCnt={g('totpkngcnt')})\n"
                f"  용적률    : {vl_str}  (vlRat={g('vlrat')}, estm={vl_estm}, plat={plat_area})\n"
                f"  건폐율    : {bc_str}  (bcRat={g('bcrat')}, arch={arch_area})\n\n"
                f"[선택된 item 전체 태그]\n{all_tag_lines}"
            )

            # 반환: (주차, 세대수, 용적률, 건폐율, 도로명주소, 디버그)
            return tot_pkng, tot_hh, vl_str, bc_str, road_addr, debug_msg

        except ET.ParseError as e:
            return None, None, None, None, "", f"XML 파싱 오류: {e}\n\n{raw_xml[:500]}"
        except Exception as e:
            continue

    return None, None, None, None, "", (
        f"API 호출 실패 (sigungu={sigungu_cd}, bjdong={bjdong_cd}, bun={bun}, ji={ji})\n\n"
        f"{raw_xml[:800]}"
    )


# ---------------------------------------------------------
# 📡 실거래가 API 공통 파서
# ---------------------------------------------------------
def _parse_trade_items(root, is_rent, sido_name, sigungu_name, lawd_cd, is_apt, bldg_type="오피스텔"):
    """XML 루트에서 거래 아이템 파싱 → dict list 반환"""
    results = []
    item_list = [
        elem for elem in root.iter()
        if elem.tag.split("}")[-1].strip().lower() == "item"
    ]

    for item in item_list:
        if is_apt:
            apt_name = get_xml_text(item, ["aptNm", "아파트", "단지", "단지명"], "이름없음")
        elif bldg_type == "오피스텔":
            apt_name = get_xml_text(item, ["danji", "단지"], "이름없음")
        else:
            apt_name = get_xml_text(item, ["mhouseNm", "연립단지명", "연립명"], "이름없음")

        dong_name = get_xml_text(item, ["umdNm", "법정동", "법정동명", "dong"], "")
        jibun = get_xml_text(item, ["jibun", "지번"], "")
        area = get_xml_text(item, ["excluUseAr", "exclUseAr", "전용면적"], "0")
        floor = get_xml_text(item, ["floor", "층"], "0")
        y = get_xml_text(item, ["dealYear", "년"], "2026")
        m = get_xml_text(item, ["dealMonth", "월"], "01").zfill(2)
        d = get_xml_text(item, ["dealDay", "일"], "01").zfill(2)
        build_y = get_xml_text(item, ["buildYear", "건축년도"], "0")

        req_gbn = get_xml_text(item, ["reqGbn", "신고구분"], "")
        broker = get_xml_text(item, ["estateAgncyNm", "중개사소재지"], "")
        trade_type_str = "⚠️ 개인거래" if req_gbn == "직거래" else "🤝 중개거래"

        monthly_val = 0
        if is_rent:
            deposit_str = get_xml_text(item, ["deposit", "보증금액", "보증금", "전세금"], "0").replace(",", "").strip()
            monthly_str = get_xml_text(item, ["monthlyRent", "월세금액", "월세"], "0").replace(",", "").strip()
            try:
                price = int(deposit_str)
            except Exception:
                price = 0
            try:
                monthly_val = int(monthly_str)
            except Exception:
                monthly_val = 0
            actual_trade_type = "월세" if monthly_val > 0 else "전세"
        else:
            price_str = get_xml_text(item, ["dealAmount", "거래금액"], "0").replace(",", "").strip()
            try:
                price = int(price_str)
            except Exception:
                price = 0
            actual_trade_type = "매매"

        try:
            area_fmt = f"{float(area):.2f}㎡"
        except Exception:
            area_fmt = "0㎡"

        results.append({
            "계약일": f"{y}-{m}-{d}",
            "시도": sido_name,
            "시군구": sigungu_name,
            "법정동코드": lawd_cd,
            "법정동": dong_name,
            "지번": jibun,
            "단지명": apt_name,
            "전용면적": area_fmt,
            "층": f"{floor}층",
            "건축년도": build_y,
            "거래유형": actual_trade_type,
            "중개거래여부": trade_type_str,
            "거래금액(만 원)": price,
            "월세(만 원)": monthly_val,
        })
    return results


def _fetch_one_month(url_list, is_rent, sido_name, sigungu_name, lawd_cd, is_apt, bldg_type):
    """단일 월 데이터 fetch (HTTP/HTTPS fallback 포함)"""
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in url_list:
        try:
            res = requests.get(url, headers=headers, timeout=15, verify=False)
            if res.status_code != 200:
                continue
            root = ET.fromstring(res.text)

            err_reason = root.find(".//returnReasonCode")
            if err_reason is not None and err_reason.text.strip() != "00":
                return [], "공공데이터포털 미승인 키 오류", False

            err_msg_el = root.find(".//errMsg")
            if err_msg_el is not None and "SERVICE ERROR" in err_msg_el.text.upper():
                return [], "공공데이터포털 미승인 키 오류", False

            items = _parse_trade_items(root, is_rent, sido_name, sigungu_name, lawd_cd, is_apt, bldg_type)
            return items, "SUCCESS", True
        except Exception as e:
            continue
    return [], f"통신 에러", False


def _build_url_list(lawd_cd, ymd, is_rent, is_apt, bldg_type):
    """API URL 리스트 생성 (https/http 이중)"""
    base_https = "https://apis.data.go.kr/1613000"
    base_http = "http://apis.data.go.kr/1613000"
    key = MOLIT_API_KEY
    params = f"serviceKey={key}&LAWD_CD={lawd_cd}&DEAL_YMD={ymd}&numOfRows=1000"

    if is_apt:
        path = "RTMSDataSvcAptRent/getRTMSDataSvcAptRent" if is_rent else "RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
    elif bldg_type == "오피스텔":
        path = "RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent" if is_rent else "RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade"
    else:
        path = "RTMSDataSvcRHRent/getRTMSDataSvcRHRent" if is_rent else "RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade"

    return [f"{base_https}/{path}?{params}", f"{base_http}/{path}?{params}"]


# ✅ FIX 5: 병렬 fetch 엔진
def fetch_real_data(sido_name, sigungu_name, lawd_cd, target_months, api_type, is_apt=True, bldg_type="오피스텔"):
    """실거래가 병렬 조회 (아파트/비아파트 공용)"""
    if not lawd_cd:
        return None, "지역 코드를 찾을 수 없습니다."

    is_rent = (api_type == "전월세")
    all_data = []
    last_error_msg = ""
    is_api_success = False

    def fetch_month(ymd):
        urls = _build_url_list(lawd_cd, ymd, is_rent, is_apt, bldg_type)
        return _fetch_one_month(urls, is_rent, sido_name, sigungu_name, lawd_cd, is_apt, bldg_type)

    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(fetch_month, ymd): ymd for ymd in target_months}
        for future in as_completed(future_map):
            items, msg, success = future.result()
            if success:
                is_api_success = True
                all_data.extend(items)
            elif msg and msg != "SUCCESS":
                last_error_msg = msg

    if all_data:
        return pd.DataFrame(all_data), "SUCCESS"
    elif not is_api_success and last_error_msg:
        return None, last_error_msg
    else:
        return pd.DataFrame(), "NODATA"


# ✅ FIX 5: 복수 시군구 병렬 fetch
def fetch_all_targets(targets, target_months, api_type, sido_name, is_apt=True, bldg_type="오피스텔"):
    """여러 시군구 병렬 조회"""
    result_dfs = []
    error_msgs = []

    def fetch_one_target(sgg_name, l_cd):
        return fetch_real_data(sido_name, sgg_name, l_cd, target_months, api_type, is_apt, bldg_type)

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {
            executor.submit(fetch_one_target, sgg_name, l_cd): sgg_name
            for sgg_name, l_cd in targets
        }
        for future in as_completed(future_map):
            df, msg = future.result()
            if df is not None and not df.empty:
                result_dfs.append(df)
            elif df is None:
                error_msgs.append(msg)

    return result_dfs, error_msgs


# ---------------------------------------------------------
# 📋 단지명 버튼 목록 UI (페이지네이션 포함)
# ---------------------------------------------------------
# ✅ FIX 6: 페이지네이션 도입
def render_clickable_list(df, is_apt=True, page_key="list_page"):
    total = len(df)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)

    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    current_page = st.session_state[page_key]
    if current_page >= total_pages:
        st.session_state[page_key] = 0
        current_page = 0

    # 페이지 컨트롤
    if total_pages > 1:
        pg_col1, pg_col2, pg_col3 = st.columns([1, 2, 1])
        with pg_col1:
            if st.button("◀ 이전", key=f"{page_key}_prev", disabled=(current_page == 0)):
                st.session_state[page_key] -= 1
                st.rerun()
        with pg_col2:
            st.markdown(
                f"<div style='text-align:center; padding-top:0.5em;'>"
                f"{current_page + 1} / {total_pages} 페이지 &nbsp;|&nbsp; 총 {total:,}건</div>",
                unsafe_allow_html=True,
            )
        with pg_col3:
            if st.button("다음 ▶", key=f"{page_key}_next", disabled=(current_page >= total_pages - 1)):
                st.session_state[page_key] += 1
                st.rerun()

    start_idx = current_page * ITEMS_PER_PAGE
    end_idx = min(start_idx + ITEMS_PER_PAGE, total)
    display_df = df.iloc[start_idx:end_idx].reset_index(drop=True)

    # 표시용 컬럼 가공
    view = display_df.copy()
    def make_price(row):
        p = format_to_korean_currency(row["거래금액(만 원)"])
        if row.get("월세(만 원)", 0) > 0:
            return f"{p} / {row['월세(만 원)']}만원"
        return p

    view["실거래가"] = view.apply(make_price, axis=1)
    view["면적/층"] = view["전용면적"].astype(str) + "  " + view["층"].astype(str)
    show_df = view[["계약일", "단지명", "면적/층", "거래유형", "실거래가"]]

    st.caption("👆 행을 클릭하면 단지 상세 정보로 이동합니다")

    event = st.dataframe(
        show_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "계약일":  st.column_config.TextColumn("계약일",  width="small"),
            "단지명":  st.column_config.TextColumn("단지명",  width="medium"),
            "면적/층": st.column_config.TextColumn("면적/층", width="small"),
            "거래유형":st.column_config.TextColumn("거래유형",width="small"),
            "실거래가":st.column_config.TextColumn("실거래가",width="medium"),
        },
    )

    # 행 선택 시 상세 페이지로 이동
    if event.selection and event.selection.rows:
        selected_idx = event.selection.rows[0]
        row = display_df.iloc[selected_idx]

        st.session_state.show_detail = True
        st.session_state.detail_is_apt = is_apt
        st.session_state.detail_bldg_type = st.session_state.get("nonapt_bldg_type", "오피스텔")
        st.session_state.detail_sido = row.get("시도", "")
        st.session_state.detail_sigungu = row.get("시군구", "")
        st.session_state.detail_lawd_cd = row.get("법정동코드", "")
        st.session_state.detail_apt_name = row["단지명"]
        st.session_state.detail_dong = row.get("법정동", "")
        st.session_state.detail_build_year = row.get("건축년도", "0")
        st.session_state.detail_jibun = row.get("지번", "")
        st.session_state.detail_full_df = pd.DataFrame()
        st.session_state.detail_searched = False
        st.rerun()


# ---------------------------------------------------------
# 🔍 상세 페이지
# ---------------------------------------------------------
def show_detail_page():
    apt_name = st.session_state.get("detail_apt_name", "이름없음")
    dong_name = st.session_state.get("detail_dong", "")
    build_year = st.session_state.get("detail_build_year", "0")
    sido = st.session_state.get("detail_sido", "")
    sigungu = st.session_state.get("detail_sigungu", "")
    lawd_cd = st.session_state.get("detail_lawd_cd", "")
    jibun = st.session_state.get("detail_jibun", "")
    is_apt = st.session_state.get("detail_is_apt", True)

    if st.button("⬅️ 이전 목록으로 돌아가기"):
        st.session_state.show_detail = False
        st.rerun()

    title_icon = "🏢" if is_apt else "🏘️"
    st.title(f"{title_icon} {apt_name} 상세 분석")
    st.write("---")

    try:
        age = datetime.date.today().year - int(build_year) + 1
        build_str = f"{build_year}년 ({age}년차)" if build_year != "0" else "정보 없음"
    except Exception:
        build_str = "정보 없음"

    col_info, col_map = st.columns([1, 1])
    with col_info:
        st.subheader("📌 단지 기본 정보")
        st.write(f"**📅 준공일:** {build_str}")

        with st.spinner("📡 건축물대장 스펙 조회 중..."):
            pkng, hh_cnt, vl, bc, road_addr, debug_msg = fetch_building_ledger_v4(lawd_cd, dong_name, jibun)

        # 주소: 도로명 우선, 없으면 지번
        display_addr = road_addr if road_addr and road_addr != "0" else f"{sido} {sigungu} {dong_name} {jibun}"
        st.write(f"**📍 주소:** {display_addr}")

        if pkng is not None:
            st.write(f"**🏘️ 세대수:** {hh_cnt}세대")
            if hh_cnt and hh_cnt > 0:
                pkng_per_hh = round(pkng / hh_cnt, 2)
                st.write(f"**🚗 세대당 주차대수:** {pkng_per_hh}대 (총 {pkng}대)")
            else:
                st.write(f"**🚗 세대당 주차대수:** 계산 불가 (총 {pkng}대)")
            st.write(f"**🏢 용적률:** {vl} / **🏗️ 건폐율:** {bc}")
            # 디버그: SUCCESS여도 실제 태그 확인 가능
            with st.expander("🔧 건축물대장 API 디버그 (개발자용)", expanded=False):
                st.code(debug_msg, language="text")
        else:
            st.write("**🏘️ 세대수:** 조회 불가")
            st.write("**🚗 세대당 주차대수:** 조회 불가")
            st.write("**🏢 용적률:** 조회 불가 / **🏗️ 건폐율:** 조회 불가")
            with st.expander("🚨 건축물대장 오류 상세 (클릭하여 확인)", expanded=True):
                st.code(debug_msg, language="text")

    with col_map:
        lat, lng = get_lat_lng_free(sido, sigungu, dong_name, apt_name)
        if lat and lng:
            map_data = pd.DataFrame({"lat": [lat], "lon": [lng]})
            st.map(map_data, zoom=15, height=200)
        else:
            st.info("🗺️ 주소 정보가 부족하여 지도에 위치를 표시할 수 없습니다.")

    st.write("---")

    # ─── 아파트 상세 ───────────────────────────────────────────────────
    if is_apt:
        st.subheader("🔍 단지 상세 조회 및 GAP 차트 설정")
        cond_col1, cond_col2, cond_col3 = st.columns([1.5, 1, 1.5])
        with cond_col1:
            chart_view_type = st.radio("조회 항목 (차트)", ["매매", "전세", "매매+전세 통합"], horizontal=True)
        with cond_col2:
            chart_period = st.selectbox("📅 조회 기간", PERIOD_OPTIONS, index=1, key="detail_period")
        with cond_col3:
            custom_dates_dt = None
            if chart_period == "직접 설정":
                custom_dates_dt = st.date_input(
                    "조회 시작/종료일",
                    [datetime.date.today() - datetime.timedelta(days=30), datetime.date.today()],
                )

        if st.button("📊 시세 및 실거래가 조회", type="primary", use_container_width=True):
            months_to_fetch, start_date, end_date = resolve_months(chart_period, custom_dates_dt)
            if months_to_fetch is None:
                st.warning("종료일을 정확히 선택해주세요.")
                st.stop()

            with st.spinner(f"📡 {apt_name}의 실제 데이터를 분석 중입니다..."):
                detail_dfs = []
                df_sale, _ = fetch_real_data(sido, sigungu, lawd_cd, months_to_fetch, "매매", is_apt=True)
                df_rent, _ = fetch_real_data(sido, sigungu, lawd_cd, months_to_fetch, "전월세", is_apt=True)

                if df_sale is not None and not df_sale.empty:
                    detail_dfs.append(df_sale[df_sale["단지명"] == apt_name])
                if df_rent is not None and not df_rent.empty:
                    detail_dfs.append(df_rent[df_rent["단지명"] == apt_name])

                if detail_dfs:
                    full_df = pd.concat(detail_dfs, ignore_index=True)
                    full_df = apply_date_filter(full_df, chart_period, start_date, end_date)
                    st.session_state.detail_full_df = full_df
                else:
                    st.session_state.detail_full_df = pd.DataFrame()

                st.session_state.detail_chart_view = chart_view_type
                st.session_state.detail_searched = True

        if st.session_state.get("detail_searched", False):
            detail_full_df = st.session_state.get("detail_full_df", pd.DataFrame())
            current_view_type = st.session_state.get("detail_chart_view", "매매")

            if not detail_full_df.empty:
                st.write("---")
                st.subheader("📈 시세 흐름 및 GAP 분석")

                fig = go.Figure()
                df_for_chart = detail_full_df.copy()
                df_for_chart["계약월_한글"] = (
                    df_for_chart["계약일"].str[:4] + "년 " + df_for_chart["계약일"].str[5:7] + "월"
                )
                df_for_chart["계약월"] = df_for_chart["계약일"].str[:7]

                sale_agg = pd.DataFrame()
                rent_agg = pd.DataFrame()

                if current_view_type in ["매매", "매매+전세 통합"]:
                    df_sale_c = df_for_chart[df_for_chart["거래유형"] == "매매"]
                    if not df_sale_c.empty:
                        sale_agg = df_sale_c.groupby(["계약월", "계약월_한글"])["거래금액(만 원)"].mean().reset_index()
                        sale_agg["거래금액(억 원)"] = sale_agg["거래금액(만 원)"] / 10000

                if current_view_type in ["전세", "매매+전세 통합"]:
                    df_rent_c = df_for_chart[df_for_chart["거래유형"] == "전세"]
                    if not df_rent_c.empty:
                        rent_agg = df_rent_c.groupby(["계약월", "계약월_한글"])["거래금액(만 원)"].mean().reset_index()
                        rent_agg["거래금액(억 원)"] = rent_agg["거래금액(만 원)"] / 10000

                if current_view_type == "매매+전세 통합" and not sale_agg.empty and not rent_agg.empty:
                    merged = pd.merge(
                        sale_agg, rent_agg, on=["계약월", "계약월_한글"], how="outer", suffixes=("_매매", "_전세")
                    ).sort_values("계약월")
                    merged["거래금액(억 원)_매매"] = merged["거래금액(억 원)_매매"].interpolate().ffill().bfill()
                    merged["거래금액(억 원)_전세"] = merged["거래금액(억 원)_전세"].interpolate().ffill().bfill()
                    merged["GAP(억 원)"] = merged["거래금액(억 원)_매매"] - merged["거래금액(억 원)_전세"]

                    fig.add_trace(go.Scatter(
                        x=merged["계약월_한글"].tolist(),
                        y=merged["거래금액(억 원)_매매"].tolist(),
                        mode="lines+markers", name="평균 매매가",
                        line=dict(color="#FF4B4B", width=2),
                        customdata=merged["GAP(억 원)"].tolist(),
                        hovertemplate="계약일: %{x}<br>평균 매매가: %{y:,.2f} 억원<br><b>🔥 GAP: %{customdata:,.2f} 억원</b><extra></extra>",
                    ))
                    fig.add_trace(go.Scatter(
                        x=merged["계약월_한글"].tolist(),
                        y=merged["거래금액(억 원)_전세"].tolist(),
                        mode="lines+markers", name="평균 전세가",
                        line=dict(color="#1f77b4", width=2),
                        hovertemplate="계약일: %{x}<br>평균 전세가: %{y:,.2f} 억원<extra></extra>",
                    ))
                else:
                    if not sale_agg.empty:
                        fig.add_trace(go.Scatter(
                            x=sale_agg["계약월_한글"].tolist(),
                            y=sale_agg["거래금액(억 원)"].tolist(),
                            mode="lines+markers", name="평균 매매가",
                            line=dict(color="#FF4B4B", width=2),
                            hovertemplate="계약일: %{x}<br>매매가: %{y:,.2f} 억원<extra></extra>",
                        ))
                    if not rent_agg.empty:
                        fig.add_trace(go.Scatter(
                            x=rent_agg["계약월_한글"].tolist(),
                            y=rent_agg["거래금액(억 원)"].tolist(),
                            mode="lines+markers", name="평균 전세가",
                            line=dict(color="#1f77b4", width=2),
                            hovertemplate="계약일: %{x}<br>전세가: %{y:,.2f} 억원<extra></extra>",
                        ))

                fig.update_layout(
                    margin=dict(l=0, r=0, t=20, b=0),
                    xaxis_title="계약 기간", yaxis_title="평균 금액 (억원)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    hovermode="x unified",
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

                st.write("---")
                st.subheader("📋 실거래가 상세 내역 필터")

                list_col1, list_col2 = st.columns(2)
                trade_opts = ["전체보기"] + sorted(detail_full_df["거래유형"].unique().tolist())
                pyeong_opts = ["전체보기"] + sorted(detail_full_df["전용면적"].unique().tolist())

                with list_col1:
                    list_trade = st.selectbox("거래 유형 필터", trade_opts, key="detail_list_trade")
                with list_col2:
                    list_pyeong = st.selectbox("평형대 필터", pyeong_opts, key="detail_list_pyeong")

                filtered_data = detail_full_df.copy()
                if list_trade != "전체보기":
                    filtered_data = filtered_data[filtered_data["거래유형"] == list_trade]
                if list_pyeong != "전체보기":
                    filtered_data = filtered_data[filtered_data["전용면적"] == list_pyeong]

                if filtered_data.empty:
                    st.warning("선택하신 조건의 최근 거래 내역이 없습니다.")
                else:
                    filtered_data = filtered_data.sort_values(by="계약일", ascending=False)

                    def make_price_str(row):
                        p = format_to_korean_currency(row["거래금액(만 원)"])
                        if row.get("월세(만 원)", 0) > 0:
                            return f"{p} / {row['월세(만 원)']}만원"
                        return p

                    filtered_data["실거래가(보증금)"] = filtered_data.apply(make_price_str, axis=1)
                    display_list = filtered_data[["계약일", "전용면적", "층", "거래유형", "실거래가(보증금)"]]
                    st.dataframe(
                        display_list, use_container_width=True, hide_index=True,
                        column_config={
                            "실거래가(보증금)": st.column_config.TextColumn("실거래가(보증금/월세)", width="medium"),
                            "거래유형": st.column_config.TextColumn("거래유형", width="small"),
                        },
                    )
            else:
                st.warning("선택하신 기간/항목 내 실거래 데이터가 없습니다.")

    # ─── 비아파트 상세 ──────────────────────────────────────────────────
    else:
        st.subheader("🔍 비아파트 상세 조회")
        cond_col1, cond_col2, cond_col3 = st.columns(3)
        with cond_col1:
            trade_type_det = st.radio("🔄 거래 유형", ["매매", "전세", "월세"], horizontal=True, key="det_nonapt_trade")
        with cond_col2:
            period_det = st.selectbox("📅 조회 기간", PERIOD_OPTIONS, index=1, key="det_nonapt_period")
        with cond_col3:
            pyeong_type_det = st.selectbox(
                "📐 평형대",
                ["전체보기", "원룸형(30미만)", "투룸형(30~59)", "쓰리룸형(59이상)"],
                key="det_nonapt_pyeong",
            )

        custom_dates_dt = None
        if period_det == "직접 설정":
            date_col1, _ = st.columns([1, 2])
            with date_col1:
                custom_dates_dt = st.date_input(
                    "조회 시작/종료일",
                    [datetime.date.today() - datetime.timedelta(days=30), datetime.date.today()],
                    key="det_nonapt_custom",
                )

        if st.button("📊 실거래가 상세 조회", type="primary", use_container_width=True):
            months_to_fetch, start_date, end_date = resolve_months(period_det, custom_dates_dt)
            if months_to_fetch is None:
                st.warning("종료일을 정확히 선택해주세요.")
                st.stop()

            api_target = "전월세" if trade_type_det in ["전세", "월세"] else "매매"
            bldg_type = st.session_state.get("detail_bldg_type", "오피스텔")

            with st.spinner(f"📡 {apt_name}의 데이터를 연동 중입니다..."):
                df_detail, msg = fetch_real_data(
                    sido, sigungu, lawd_cd, months_to_fetch, api_target,
                    is_apt=False, bldg_type=bldg_type,
                )

                if df_detail is not None and not df_detail.empty:
                    # ✅ 이슈1,2: 분리 단지 통합
                    rel_names, _ = find_related_apt_names(df_detail, apt_name, jibun)
                    df_detail = df_detail[df_detail["단지명"].isin(rel_names)]
                    df_detail = df_detail[df_detail["거래유형"] == trade_type_det]
                    df_detail = apply_date_filter(df_detail, period_det, start_date, end_date)
                    df_detail = apply_area_filter(df_detail, pyeong_type_det, is_apt=False)
                    st.session_state.detail_full_df = df_detail
                else:
                    st.session_state.detail_full_df = pd.DataFrame()

                st.session_state.detail_searched = True

        if st.session_state.get("detail_searched", False):
            detail_full_df = st.session_state.get("detail_full_df", pd.DataFrame())
            if not detail_full_df.empty:
                st.write("---")
                st.subheader("📋 실거래가 상세 내역")
                detail_full_df = detail_full_df.sort_values(by="계약일", ascending=False)

                def make_price_str(row):
                    p = format_to_korean_currency(row["거래금액(만 원)"])
                    if row.get("월세(만 원)", 0) > 0:
                        return f"{p} / {row['월세(만 원)']}만원"
                    return p

                detail_full_df["실거래가(보증금)"] = detail_full_df.apply(make_price_str, axis=1)
                display_list = detail_full_df[["계약일", "전용면적", "층", "거래유형", "실거래가(보증금)"]]
                st.dataframe(
                    display_list, use_container_width=True, hide_index=True,
                    column_config={
                        "실거래가(보증금)": st.column_config.TextColumn("실거래가(보증금/월세)", width="medium"),
                        "거래유형": st.column_config.TextColumn("거래유형", width="small"),
                    },
                )
            else:
                st.warning("선택하신 조건에 해당하는 상세 실거래 데이터가 없습니다.")


# ---------------------------------------------------------
# 📌 PAGE 컨트롤러
# ---------------------------------------------------------
if "show_detail" not in st.session_state:
    st.session_state.show_detail = False

if st.session_state.show_detail:
    show_detail_page()

# ─── 아파트 실거래가 페이지 ───────────────────────────────────────────
elif page == "🏢 아파트 실거래가":
    st.title("🏢 아파트 실거래가 조회")
    st.write("---")
    st.subheader("1. 아파트 조회 조건")

    loc_col1, loc_col2, loc_col3, loc_col4 = st.columns(4)
    sido_dict = get_sido_list()

    with loc_col1:
        sido_name = st.selectbox("📍 시/도", list(sido_dict.keys()), key="apt_sido")
        sido_code = sido_dict.get(sido_name, "11")

    sigungu_dict = get_sigungu_list(sido_code)

    with loc_col2:
        sigungu_options = ["전체 (시/도 단위)"] + list(sigungu_dict.keys())
        sigungu_name = st.selectbox("📍 시/군/구", sigungu_options, key="apt_sigungu")

    with loc_col3:
        if sigungu_name == "전체 (시/도 단위)":
            dong_opts = ["전체 (시/도 단위)"]
        else:
            sigungu_code = sigungu_dict.get(sigungu_name)
            dong_opts = ["전체 (구 단위)"] + get_dong_list(sigungu_code)
        dong_name = st.selectbox("📍 읍/면/동", dong_opts, key="apt_dong")

    with loc_col4:
        selected_apt = st.text_input("🔍 동/단지명 검색 (선택)", placeholder="예: 대치동 또는 은마")

    st.write("")
    cond_col1, cond_col2, cond_col3 = st.columns(3)
    with cond_col1:
        trade_type = st.radio("🔄 거래 유형", ["매매", "전세", "월세"], horizontal=True, key="apt_trade")
    with cond_col2:
        period = st.selectbox("📅 조회 기간", PERIOD_OPTIONS, index=1, key="apt_period")
    with cond_col3:
        pyeong_type = st.selectbox(
            "📐 평형대",
            ["전체보기", "10평대(59미만)", "20평대(59~84)", "30평대(84이상)"],
            key="apt_pyeong",
        )

    custom_dates = None
    if period == "직접 설정":
        date_col1, _ = st.columns([1, 2])
        with date_col1:
            custom_dates = st.date_input(
                "조회 시작/종료일 (직접 설정)",
                [datetime.date.today() - datetime.timedelta(days=30), datetime.date.today()],
            )

    st.write("")

    if st.button(f"🔍 아파트 {trade_type} 실시간 조회", type="primary", use_container_width=True, key="btn_apt"):
        months_to_fetch, start_date, end_date = resolve_months(period, custom_dates)
        if months_to_fetch is None:
            st.warning("종료일을 정확히 선택해주세요.")
            st.stop()

        st.session_state.apt_searched = True
        st.session_state.apt_display_loc = (
            f"{sido_name} "
            f"{'전체' if sigungu_name == '전체 (시/도 단위)' else sigungu_name} "
            f"{'' if dong_name in ['전체 (구 단위)', '전체 (시/도 단위)'] else dong_name}"
        ).strip()
        st.session_state.apt_trade_type = trade_type
        st.session_state.apt_period_val = period
        st.session_state.apt_start_date = start_date
        st.session_state.apt_end_date = end_date

        if sigungu_name == "전체 (시/도 단위)":
            targets = list(sigungu_dict.items())
        else:
            targets = [(sigungu_name, sigungu_dict.get(sigungu_name))]

        api_target = "전월세" if trade_type in ["전세", "월세"] else "매매"

        with st.spinner("📡 국토교통부 서버에서 데이터를 가져오는 중입니다..."):
            result_dfs, error_msgs = fetch_all_targets(
                targets, months_to_fetch, api_target, sido_name, is_apt=True
            )

        if result_dfs:
            real_df = pd.concat(result_dfs, ignore_index=True)
            real_df = real_df[real_df["거래유형"] == trade_type]
            real_df = apply_date_filter(real_df, period, start_date, end_date)
            real_df = apply_area_filter(real_df, pyeong_type, is_apt=True)

            if dong_name not in ["전체 (구 단위)", "전체 (시/도 단위)"]:
                # ✅ 이슈4 수정: 정확 매칭 (신월계동이 월계동에 포함되는 오류 방지)
                real_df = real_df[real_df["법정동"] == dong_name]
            if selected_apt.strip():
                real_df = real_df[
                    real_df["단지명"].str.contains(selected_apt, na=False) |
                    real_df["법정동"].str.contains(selected_apt, na=False)
                ]

            real_df = real_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)
            st.session_state.res_df = real_df
            st.session_state["apt_list_page"] = 0

            if real_df.empty:
                st.info("해당 기간/조건에 신고된 실거래 데이터가 없습니다.")
            else:
                st.success(f"✅ 100% 국토교통부 실제 {trade_type} 데이터 연동 완료! (총 {len(real_df):,}건)")
        else:
            st.session_state.res_df = pd.DataFrame()
            # ✅ FIX 2: error_msg 변수 버그 수정 (msg → error_msgs)
            if error_msgs:
                st.error(f"⚠️ {error_msgs[0]}")
            else:
                st.info("해당 기간/조건에 신고된 실거래 데이터가 없습니다.")

    if st.session_state.get("apt_searched", False):
        st.write("---")
        loc_str = st.session_state.apt_display_loc
        trade_str = st.session_state.apt_trade_type
        df = st.session_state.get("res_df", pd.DataFrame()).copy()

        st.subheader(f"📊 {loc_str} 아파트 {trade_str} 리스트")
        if not df.empty:
            render_clickable_list(df, is_apt=True, page_key="apt_list_page")


# ─── 비아파트 실거래가 페이지 ─────────────────────────────────────────
elif page == "🏘️ 비아파트 (오피스텔/빌라 등)":
    st.title("🏘️ 비아파트 실거래가 조회")
    st.write("---")
    st.subheader("1. 비아파트 조회 조건")

    bldg_type = st.radio(
        "🏢 건물 유형 선택", ["오피스텔", "연립다세대"], horizontal=True, key="nonapt_bldg_type"
    )
    st.write("")

    loc_col1, loc_col2, loc_col3, loc_col4 = st.columns(4)
    sido_dict = get_sido_list()

    with loc_col1:
        sido_name = st.selectbox("📍 시/도", list(sido_dict.keys()), key="nonapt_sido")
        sido_code = sido_dict.get(sido_name, "11")

    sigungu_dict = get_sigungu_list(sido_code)

    with loc_col2:
        sigungu_options = ["전체 (시/도 단위)"] + list(sigungu_dict.keys())
        sigungu_name = st.selectbox("📍 시/군/구", sigungu_options, key="nonapt_sigungu")

    with loc_col3:
        if sigungu_name == "전체 (시/도 단위)":
            dong_opts = ["전체 (시/도 단위)"]
        else:
            sigungu_code = sigungu_dict.get(sigungu_name)
            dong_opts = ["전체 (구 단위)"] + get_dong_list(sigungu_code)
        dong_name = st.selectbox("📍 읍/면/동", dong_opts, key="nonapt_dong")

    with loc_col4:
        selected_nonapt = st.text_input(
            "🔍 동/건물명 검색 (선택)", placeholder="예: 역삼동 또는 타워", key="nonapt_search"
        )

    st.write("")
    cond_col1, cond_col2, cond_col3 = st.columns(3)
    with cond_col1:
        trade_type = st.radio("🔄 거래 유형", ["매매", "전세", "월세"], horizontal=True, key="nonapt_trade")
    with cond_col2:
        period = st.selectbox("📅 조회 기간", PERIOD_OPTIONS, index=1, key="nonapt_period")
    with cond_col3:
        pyeong_type = st.selectbox(
            "📐 평형대",
            ["전체보기", "원룸형(30미만)", "투룸형(30~59)", "쓰리룸형(59이상)"],
            key="nonapt_pyeong",
        )

    custom_dates = None
    if period == "직접 설정":
        date_col1, _ = st.columns([1, 2])
        with date_col1:
            custom_dates = st.date_input(
                "조회 시작/종료일 (직접 설정)",
                [datetime.date.today() - datetime.timedelta(days=30), datetime.date.today()],
                key="nonapt_custom_date",
            )

    st.write("")

    if st.button(f"🔍 비아파트 {trade_type} 실시간 조회", type="primary", use_container_width=True, key="btn_nonapt"):
        months_to_fetch, start_date, end_date = resolve_months(period, custom_dates)
        if months_to_fetch is None:
            st.warning("종료일을 정확히 선택해주세요.")
            st.stop()

        st.session_state.nonapt_searched = True
        st.session_state.nonapt_display_loc = (
            f"{sido_name} "
            f"{'전체' if sigungu_name == '전체 (시/도 단위)' else sigungu_name} "
            f"{'' if dong_name in ['전체 (구 단위)', '전체 (시/도 단위)'] else dong_name}"
        ).strip()
        st.session_state.nonapt_trade_type = trade_type

        if sigungu_name == "전체 (시/도 단위)":
            targets = list(sigungu_dict.items())
        else:
            targets = [(sigungu_name, sigungu_dict.get(sigungu_name))]

        api_target = "전월세" if trade_type in ["전세", "월세"] else "매매"

        with st.spinner(f"📡 국토교통부 서버에서 {bldg_type} 데이터를 가져오는 중입니다..."):
            result_dfs, error_msgs = fetch_all_targets(
                targets, months_to_fetch, api_target, sido_name, is_apt=False, bldg_type=bldg_type
            )

        if result_dfs:
            real_df = pd.concat(result_dfs, ignore_index=True)
            real_df = real_df[real_df["거래유형"] == trade_type]
            real_df = apply_date_filter(real_df, period, start_date, end_date)
            real_df = apply_area_filter(real_df, pyeong_type, is_apt=False)

            if dong_name not in ["전체 (구 단위)", "전체 (시/도 단위)"]:
                # ✅ 이슈4 수정: 정확 매칭
                real_df = real_df[real_df["법정동"] == dong_name]
            if selected_nonapt.strip():
                real_df = real_df[
                    real_df["단지명"].str.contains(selected_nonapt, na=False) |
                    real_df["법정동"].str.contains(selected_nonapt, na=False)
                ]

            real_df = real_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)
            st.session_state.res_nonapt_df = real_df
            st.session_state["nonapt_list_page"] = 0

            if real_df.empty:
                st.info("해당 기간/조건에 신고된 실거래 데이터가 없습니다.")
            else:
                st.success(f"✅ 100% 국토교통부 실제 {bldg_type} {trade_type} 데이터 연동 완료! (총 {len(real_df):,}건)")
        else:
            st.session_state.res_nonapt_df = pd.DataFrame()
            # ✅ FIX 2: error_msg 변수 버그 수정
            if error_msgs:
                st.error(f"⚠️ {error_msgs[0]}")
            else:
                st.info("해당 기간/조건에 신고된 실거래 데이터가 없습니다.")

    if st.session_state.get("nonapt_searched", False):
        st.write("---")
        loc_str = st.session_state.nonapt_display_loc
        trade_str = st.session_state.nonapt_trade_type
        df = st.session_state.get("res_nonapt_df", pd.DataFrame()).copy()

        st.subheader(f"📊 {loc_str} 비아파트 {trade_str} 리스트")
        if not df.empty:
            render_clickable_list(df, is_apt=False, page_key="nonapt_list_page")
