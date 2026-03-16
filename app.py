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
    st.caption("v3.4 - 전면 안정화 버전")

    if st.button("🔄 앱 캐시 강제 초기화"):
        st.cache_data.clear()
        st.rerun()


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
    # ✅ FIX 9: 태그 매핑 딕셔너리로 개선
    lower_tags = {t.strip().lower() for t in tags}
    for child in item.iter():
        tag_name = child.tag.split("}")[-1].strip().lower()
        if tag_name in lower_tags:
            if child.text is not None and child.text.strip():
                return child.text.strip()
    return default


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
# 🏗️ 건축물대장 듀얼-엔진
# ---------------------------------------------------------
# ✅ FIX 4: ttl 추가로 캐시 충돌 방지
@st.cache_data(show_spinner=False, ttl=3600)
def fetch_building_ledger(sigungu_cd, dong_name, jibun):
    if not jibun:
        return None, None, None, None, "실거래가 데이터에 지번 정보가 누락됨"

    bjdong_cd = ""
    try:
        res = requests.get(
            f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"
            f"?regcode_pattern={sigungu_cd}*&is_ignore_zero=true",
            timeout=5, verify=False
        ).json()
        for item in res.get("regcodes", []):
            if dong_name in item["name"]:
                bjdong_cd = item["code"][5:10]
                break
    except Exception:
        return None, None, None, None, "법정동코드 변환 서버 통신 오류"

    if not bjdong_cd:
        return None, None, None, None, f"'{dong_name}'의 5자리 법정동코드 변환 실패"

    plat_gb_cd = "0"
    clean_jibun = str(jibun).replace("산", "").strip()
    if "산" in str(jibun):
        plat_gb_cd = "1"

    parts = clean_jibun.split("-")
    bun = parts[0].zfill(4) if parts else "0000"
    ji = parts[1].zfill(4) if len(parts) > 1 else "0000"

    urls_to_try = [
        f"https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
        f"?serviceKey={MOLIT_API_KEY}&sigunguCd={sigungu_cd}&bjdongCd={bjdong_cd}"
        f"&platGbCd={plat_gb_cd}&bun={bun}&ji={ji}&numOfRows=10",
        f"http://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
        f"?serviceKey={MOLIT_API_KEY}&sigunguCd={sigungu_cd}&bjdongCd={bjdong_cd}"
        f"&platGbCd={plat_gb_cd}&bun={bun}&ji={ji}&numOfRows=10",
    ]

    last_err = "알 수 없는 에러"
    for url in urls_to_try:
        try:
            res = requests.get(url, timeout=10, verify=False)
            if res.status_code != 200:
                last_err = f"서버 거절 (HTTP {res.status_code})"
                continue

            root = ET.fromstring(res.text)

            err_reason = root.find(".//returnReasonCode")
            if err_reason is not None and err_reason.text.strip() != "00":
                last_err = "API 키 미승인 (동기화 지연 또는 키 오류)"
                continue

            err_msg = root.find(".//errMsg")
            if err_msg is not None and "SERVICE ERROR" in err_msg.text.upper():
                last_err = "API 키 미승인 (동기화 지연 또는 키 오류)"
                continue

            result_code = root.find(".//resultCode")
            if result_code is not None and result_code.text.strip() not in ["00", "0"]:
                result_msg = root.find(".//resultMsg")
                last_err = f"국토부 응답 에러: {result_msg.text if result_msg is not None else '알 수 없음'}"
                continue

            items = root.findall(".//item")
            if not items:
                last_err = f"대장 데이터 없음 (지번: {bjdong_cd}-{plat_gb_cd}-{bun}-{ji})"
                return None, None, None, None, last_err

            tot_pkng = 0
            tot_hh = 0
            vl_rat = 0.0
            bc_rat = 0.0

            for item in items:
                main_atch_gb_cd = get_xml_text(item, ["mainAtchGbCd"], "")
                if main_atch_gb_cd in ["0", "1", ""]:
                    try:
                        tot_pkng += int(get_xml_text(item, ["totPkngCnt"], "0"))
                    except Exception:
                        pass
                    try:
                        tot_hh += int(get_xml_text(item, ["hhCnt"], "0"))
                    except Exception:
                        pass
                    try:
                        vl_rat = max(vl_rat, float(get_xml_text(item, ["vlRat"], "0")))
                        bc_rat = max(bc_rat, float(get_xml_text(item, ["bcRat"], "0")))
                    except Exception:
                        pass

            # 모든 값이 0이면 첫 번째 아이템에서 직접 추출
            if tot_pkng == 0 and vl_rat == 0.0 and tot_hh == 0:
                try:
                    tot_pkng = int(get_xml_text(items[0], ["totPkngCnt"], "0"))
                except Exception:
                    pass
                try:
                    tot_hh = int(get_xml_text(items[0], ["hhCnt"], "0"))
                except Exception:
                    pass
                try:
                    vl_rat = float(get_xml_text(items[0], ["vlRat"], "0"))
                except Exception:
                    pass
                try:
                    bc_rat = float(get_xml_text(items[0], ["bcRat"], "0"))
                except Exception:
                    pass

            return tot_pkng, tot_hh, f"{vl_rat}%", f"{bc_rat}%", "SUCCESS"

        except Exception as e:
            last_err = f"통신 에러: {str(e)[:50]}"

    return None, None, None, None, last_err


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

    # 페이지 상태 초기화
    if page_key not in st.session_state:
        st.session_state[page_key] = 0
    current_page = st.session_state[page_key]
    # 범위 보정
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

    col_ratios = [1.2, 2.5, 1.5, 0.8, 1.5, 1.5]
    headers = ["계약일", "단지명(클릭 시 이동) 👆", "전용면적", "층", "거래유형", "실거래가(보증금)"]

    h_cols = st.columns(col_ratios)
    for i, header in enumerate(headers):
        h_cols[i].markdown(
            f"<div style='text-align: center; color: gray; font-size: 0.9em;'><b>{header}</b></div>",
            unsafe_allow_html=True,
        )
    st.markdown("<hr style='margin: 0.5em 0px; border-top: 2px solid #ddd;'>", unsafe_allow_html=True)

    for idx, row in display_df.iterrows():
        cols = st.columns(col_ratios)
        cols[0].markdown(
            f"<div style='text-align: center; line-height: 2.5;'>{row['계약일']}</div>",
            unsafe_allow_html=True,
        )

        btn_key = f"{'apt' if is_apt else 'non'}_btn_{page_key}_{start_idx + idx}"
        if cols[1].button(row["단지명"], key=btn_key, type="tertiary", use_container_width=True):
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

        cols[2].markdown(
            f"<div style='text-align: center; line-height: 2.5;'>{row['전용면적']}</div>",
            unsafe_allow_html=True,
        )
        cols[3].markdown(
            f"<div style='text-align: center; line-height: 2.5;'>{row['층']}</div>",
            unsafe_allow_html=True,
        )
        cols[4].markdown(
            f"<div style='text-align: center; line-height: 2.5;'>{row['거래유형']}</div>",
            unsafe_allow_html=True,
        )

        price_str = format_to_korean_currency(row["거래금액(만 원)"])
        if row.get("월세(만 원)", 0) > 0:
            price_str = f"{price_str} / {row['월세(만 원)']}만원"

        cols[5].markdown(
            f"<div style='text-align: center; line-height: 2.5; font-weight: bold; color: #E74C3C;'>{price_str}</div>",
            unsafe_allow_html=True,
        )
        st.markdown("<hr style='margin: 0px; border-top: 1px solid #eee;'>", unsafe_allow_html=True)


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
        st.write(f"**📍 법정동 주소:** {sido} {sigungu} {dong_name} {jibun}")
        st.write(f"**📅 준공일:** {build_str}")

        with st.spinner("📡 건축물대장 스펙 조회 중..."):
            pkng, hh_cnt, vl, bc, debug_msg = fetch_building_ledger(lawd_cd, dong_name, jibun)

        if pkng is not None:
            st.write(f"**🏘️ 세대수:** {hh_cnt}세대")
            if hh_cnt and hh_cnt > 0:
                pkng_per_hh = round(pkng / hh_cnt, 2)
                st.write(f"**🚗 세대당 주차대수:** {pkng_per_hh}대 (총 {pkng}대)")
            else:
                st.write(f"**🚗 세대당 주차대수:** 계산 불가 (총 {pkng}대)")
            st.write(f"**🏢 용적률:** {vl} / **🏗️ 건폐율:** {bc}")
        else:
            st.write("**🏘️ 세대수:** 조회 불가")
            st.write(f"**🚗 세대당 주차대수:** 조회 불가 🚨({debug_msg})")
            st.write("**🏢 용적률:** 조회 불가 / **🏗️ 건폐율:** 조회 불가")

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
                if df_sale is not None and not df_sale.empty:
                    detail_dfs.append(df_sale[df_sale["단지명"] == apt_name])

                df_rent, _ = fetch_real_data(sido, sigungu, lawd_cd, months_to_fetch, "전월세", is_apt=True)
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
                    df_detail = df_detail[df_detail["단지명"] == apt_name]
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
                real_df = real_df[real_df["법정동"].str.contains(dong_name, na=False)]
            if selected_apt.strip():
                real_df = real_df[
                    real_df["단지명"].str.contains(selected_apt, na=False) |
                    real_df["법정동"].str.contains(selected_apt, na=False)
                ]

            real_df = real_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)
            st.session_state.res_df = real_df
            # ✅ 페이지 번호 초기화
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
                real_df = real_df[real_df["법정동"].str.contains(dong_name, na=False)]
            if selected_nonapt.strip():
                real_df = real_df[
                    real_df["단지명"].str.contains(selected_nonapt, na=False) |
                    real_df["법정동"].str.contains(selected_nonapt, na=False)
                ]

            real_df = real_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)
            st.session_state.res_nonapt_df = real_df
            # ✅ 페이지 번호 초기화
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
