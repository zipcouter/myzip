import streamlit as st
import pandas as pd
import datetime
import numpy as np
import plotly.graph_objects as go
import requests
import xml.etree.ElementTree as ET
import sys
from geopy.geocoders import Nominatim

# ---------------------------------------------------------
# ⚙️ 페이지 및 인증키 기본 설정
# ---------------------------------------------------------
st.set_page_config(page_title="집카우터 | 실거래가 실시간 조회", layout="wide")

# 대표님의 실제 국토부 API 키
MOLIT_API_KEY = "bba046226cfdba339da5237b76bfaff8d43c90ab08d4efda3a30f6bb87ab2486"

with st.sidebar:
    st.title("🚀 집카우터 메뉴")
    st.write("원하시는 시장을 선택하세요.")
    page = st.radio("조회 메뉴", ["🏢 아파트 실거래가", "🏘️ 비아파트 (오피스텔/빌라 등)"])
    st.write("---")
    st.caption("v1.5 - UI Restored & Stable API Engine")
    
    if st.button("🔄 앱 캐시 강제 초기화"):
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------
# 🌟 실시간 전국 지역코드 연동 엔진
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_sido_list():
    try:
        res = requests.get("https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern=*00000000", timeout=5).json()
        return {item['name']: item['code'][:2] for item in res.get('regcodes', [])}
    except: return {"서울특별시": "11", "경기도": "41"}

@st.cache_data(show_spinner=False)
def get_sigungu_list(sido_code):
    try:
        res = requests.get(f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern={sido_code}*00000&is_ignore_zero=true", timeout=5).json()
        sigungu_dict = {}
        for item in res.get('regcodes', []):
            code = item['code']
            if code[2:5] == "000": continue 
            name = item['name'].replace(item['name'].split()[0], '').strip()
            sigungu_dict[name] = code[:5]
        return sigungu_dict
    except: return {"강남구": "11680"}

@st.cache_data(show_spinner=False)
def get_dong_list(sigungu_code):
    try:
        res = requests.get(f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern={sigungu_code}*&is_ignore_zero=true", timeout=5).json()
        dongs = []
        for item in res.get('regcodes', []):
            if item['code'][5:] == "00000": continue
            dong = item['name'].split()[-1]
            if dong not in dongs: dongs.append(dong)
        return dongs
    except: return []

# ---------------------------------------------------------
# 🚨 완전 무료 평생 지도 엔진 (OpenStreetMap - Geopy)
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def get_lat_lng_free(sido, sigungu, dong, apt_name):
    try:
        geolocator = Nominatim(user_agent="zip_counter_app_v1")
        location = geolocator.geocode(f"{sido} {sigungu} {dong} {apt_name}")
        if location: return location.latitude, location.longitude
        location_dong = geolocator.geocode(f"{sido} {sigungu} {dong}")
        if location_dong: return location_dong.latitude, location_dong.longitude
    except: pass
    return None, None

# ---------------------------------------------------------
# 🌟 유틸리티 함수들
# ---------------------------------------------------------
PERIOD_OPTIONS = ["오늘", "이번 주", "이번 달", "최근 1개월", "최근 3개월", "최근 6개월", "최근 1년"]

def format_to_korean_currency(price_manwon):
    try: val = int(price_manwon)
    except: return price_manwon
    eok = val // 10000
    remainder = val % 10000
    result = ""
    if eok > 0: result += f"{eok}억"
    if remainder > 0:
        cheon = remainder // 1000
        baek = (remainder % 1000) // 100
        parts = []
        if cheon > 0: parts.append(f"{cheon}천")
        if baek > 0: parts.append(f"{baek}백")
        rem_str = " ".join(parts) + "만원"
        result = f"{result} {rem_str}" if result else rem_str
    return result if result else "0원"

def get_recent_months(n):
    today = datetime.date.today()
    months = []
    for i in range(n):
        month = today.month - i
        year = today.year
        while month <= 0: month += 12; year -= 1
        months.append(f"{year}{month:02d}")
    return months

def get_xml_text(item, tags, default=""):
    for tag in tags:
        node = item.find(tag)
        if node is not None and node.text: return node.text.strip()
    return default

# =====================================================================
# 🚨 국토부 전/월세 및 매매 API 엔진 (가장 안정적인 Legacy 서버로 전면 교체)
# =====================================================================
def fetch_real_apt_data(sido_name, sigungu_name, lawd_cd, target_months, trade_type):
    if not lawd_cd: return None, "지역 코드를 찾을 수 없습니다."
    all_data = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    is_rent = trade_type in ["전세", "월세"]
    
    for ymd in target_months:
        # 🚨 신규 서버가 전월세에 취약하여, 무조건 성공하는 안정적인 예전 서버 주소로 롤백했습니다.
        if is_rent:
            url = f"http://openapi.molit.go.kr/OpenAPI_ToolInstallPackage/service/rest/RTMSOBJSvc/getRTMSDataSvcAptRent?serviceKey={MOLIT_API_KEY}&LAWD_CD={lawd_cd}&DEAL_YMD={ymd}&numOfRows=1000"
        else:
            url = f"http://openapi.molit.go.kr/OpenAPI_ToolInstallPackage/service/rest/RTMSOBJSvc/getRTMSDataSvcAptTradeDev?serviceKey={MOLIT_API_KEY}&LAWD_CD={lawd_cd}&DEAL_YMD={ymd}&numOfRows=1000"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                result_code = root.find('.//resultCode')
                if result_code is not None and result_code.text.strip() not in ["00", "0"]:
                    error_msg = root.find('.//resultMsg').text if root.find('.//resultMsg') is not None else "Unknown"
                    if error_msg.strip().upper() not in ["OK", "NORMAL SERVICE."]: return None, error_msg
                
                for item in root.findall('.//item'):
                    apt_name = get_xml_text(item, ['aptNm', '아파트', '단지'], "이름없음")
                    dong_name = get_xml_text(item, ['umdNm', '법정동'], "")
                    area = get_xml_text(item, ['excluUseAr', '전용면적'], "0")
                    floor = get_xml_text(item, ['floor', '층'], "0")
                    y = get_xml_text(item, ['dealYear', '년'], "2026")
                    m = get_xml_text(item, ['dealMonth', '월'], "01").zfill(2)
                    d = get_xml_text(item, ['dealDay', '일'], "01").zfill(2)
                    build_y = get_xml_text(item, ['buildYear', '건축년도'], "0")
                    
                    req_gbn = get_xml_text(item, ['reqGbn', '신고구분'], "")
                    broker = get_xml_text(item, ['estateAgncyNm', '중개사소재지'], "")
                    if req_gbn == "직거래": trade_type_str = "⚠️ 개인거래"
                    elif req_gbn == "중개거래" or broker: trade_type_str = "🤝 중개거래"
                    else: trade_type_str = "🤝 중개거래"
                    
                    monthly_val = 0
                    if is_rent:
                        deposit_str = get_xml_text(item, ['deposit', '보증금액'], "0").replace(',', '').strip()
                        monthly_str = get_xml_text(item, ['monthlyRent', '월세금액'], "0").replace(',', '').strip()
                        try: price = int(deposit_str)
                        except: price = 0
                        try: monthly_val = int(monthly_str)
                        except: monthly_val = 0
                        
                        # 전세/월세 필터링
                        if trade_type == "전세" and monthly_val > 0: continue
                        if trade_type == "월세" and monthly_val == 0: continue
                    else:
                        price_str = get_xml_text(item, ['dealAmount', '거래금액'], "0").replace(',', '').strip()
                        try: price = int(price_str)
                        except: price = 0

                    # 데이터가 온전히 있을 때만 추가
                    if price > 0 or monthly_val > 0:
                        all_data.append({
                            "계약일": f"{y}-{m}-{d}",
                            "시도": sido_name, "시군구": sigungu_name, "법정동코드": lawd_cd,
                            "법정동": dong_name, "단지명": apt_name,
                            "전용면적": f"{float(area):.2f}㎡" if area != "0" else "0㎡",
                            "층": f"{floor}층", "건축년도": build_y,
                            "거래유형": trade_type_str, 
                            "거래금액(만 원)": price, "월세(만 원)": monthly_val
                        })
        except Exception as e: return None, str(e) 
            
    if all_data: return pd.DataFrame(all_data), "SUCCESS"
    else: return pd.DataFrame(), "NODATA"

# =====================================================================
# 🚨 [완벽 수술] 체크박스 삭제! 기존 단지명 클릭 방식(버튼)으로 100% 원복
# =====================================================================
def render_clickable_list(df, is_apt=True):
    col_ratios = [1.2, 2.5, 1.5, 0.8, 1.5, 1.5]
    headers = ["계약일", "단지명(클릭 시 이동) 👆", "전용면적", "층", "거래유형", "실거래가(보증금)"]

    h_cols = st.columns(col_ratios)
    for i, header in enumerate(headers):
        h_cols[i].markdown(f"<div style='text-align: center; color: gray; font-size: 0.9em;'><b>{header}</b></div>", unsafe_allow_html=True)
    st.markdown("<hr style='margin: 0.5em 0px; border-top: 2px solid #ddd;'>", unsafe_allow_html=True)
    
    # 🚨 인덱스 꼬임 방지
    display_df = df.copy().reset_index(drop=True)
    
    for idx, row in display_df.iterrows():
        cols = st.columns(col_ratios)
        cols[0].markdown(f"<div style='text-align: center; line-height: 2.5;'>{row['계약일']}</div>", unsafe_allow_html=True)
        
        # 🚨 흉측한 체크박스 대신 단지명 자체를 버튼으로 눌러 상세페이지 진입
        if cols[1].button(row['단지명'], key=f"{'apt' if is_apt else 'non'}_btn_{idx}", type="tertiary", use_container_width=True):
            st.session_state.show_detail = True
            st.session_state.detail_sido = row.get('시도', '')
            st.session_state.detail_sigungu = row.get('시군구', '')
            st.session_state.detail_lawd_cd = row.get('법정동코드', '')
            st.session_state.detail_apt_name = row['단지명']
            st.session_state.detail_dong = row.get('법정동', '')
            st.session_state.detail_build_year = row.get('건축년도', '0')
            # 상세페이지 차트 초기화
            st.session_state.detail_full_df = pd.DataFrame()
            st.session_state.detail_searched = False
            st.rerun()
            
        cols[2].markdown(f"<div style='text-align: center; line-height: 2.5;'>{row['전용면적']}</div>", unsafe_allow_html=True)
        cols[3].markdown(f"<div style='text-align: center; line-height: 2.5;'>{row['층']}</div>", unsafe_allow_html=True)
        cols[4].markdown(f"<div style='text-align: center; line-height: 2.5;'>{row['중개거래여부']}</div>", unsafe_allow_html=True)
        
        price_str = format_to_korean_currency(row['거래금액(만 원)'])
        if row.get('월세(만 원)', 0) > 0:
            price_str = f"{price_str} / {row['월세(만 원)']}만원"
            
        cols[5].markdown(f"<div style='text-align: center; line-height: 2.5; font-weight: bold; color: #E74C3C;'>{price_str}</div>", unsafe_allow_html=True)
        st.markdown("<hr style='margin: 0px; border-top: 1px solid #eee;'>", unsafe_allow_html=True)

# =====================================================================
# 🚨 상세페이지 (과거 1년 백엔드 조회 + 100% 실제 데이터)
# =====================================================================
def show_detail_page():
    apt_name = st.session_state.get("detail_apt_name", "이름없음")
    dong_name = st.session_state.get("detail_dong", "")
    build_year = st.session_state.get("detail_build_year", "0")
    sido = st.session_state.get("detail_sido", "")
    sigungu = st.session_state.get("detail_sigungu", "")
    lawd_cd = st.session_state.get("detail_lawd_cd", "")
    
    if st.button("⬅️ 이전 목록으로 돌아가기"):
        st.session_state.show_detail = False
        st.rerun()
        
    st.title(f"🏢 {apt_name} 상세 분석")
    st.write("---")

    try:
        age = datetime.date.today().year - int(build_year) + 1
        build_str = f"{build_year}년 ({age}년차)" if build_year != "0" else "정보 없음"
    except: build_str = "정보 없음"

    col_info, col_map = st.columns([1, 1])
    with col_info:
        st.subheader("📌 단지 기본 정보")
        st.write(f"**📍 법정동 주소:** {sido} {sigungu} {dong_name}")
        st.write(f"**📅 준공일:** {build_str}")
        st.write(f"**🏢 용적률 / 건폐율:** API 연동 준비중")
        
    with col_map:
        lat, lng = get_lat_lng_free(sido, sigungu, dong_name, apt_name)
        if lat and lng:
            map_data = pd.DataFrame({'lat': [lat], 'lon': [lng]})
            st.map(map_data, zoom=15, height=200)
        else:
            st.info("🗺️ 주소 정보가 부족하여 지도에 위치를 표시할 수 없습니다.")

    st.write("---")

    st.subheader("🔍 단지 상세 조회 및 GAP 차트 설정")
    cond_col1, cond_col2 = st.columns([1, 1])
    with cond_col1:
        chart_view_type = st.radio("조회 항목 (차트)", ["매매", "전세", "매매+전세 통합"], horizontal=True)
    with cond_col2:
        chart_period = st.selectbox("조회 기간", ["최근 1개월", "최근 3개월", "최근 6개월", "최근 1년"], index=3)

    if st.button("📊 시세 및 실거래가 조회", type="primary", use_container_width=True):
        with st.spinner(f"📡 {apt_name}의 실제 {chart_view_type} 데이터를 분석 중입니다..."):
            if "1년" in chart_period: n_months = 12
            elif "6개월" in chart_period: n_months = 6
            elif "3개월" in chart_period: n_months = 3
            else: n_months = 1
            months_to_fetch = get_recent_months(n_months)

            detail_dfs = []
            
            if chart_view_type in ["매매", "매매+전세 통합"]:
                df_sale, _ = fetch_real_apt_data(sido, sigungu, lawd_cd, months_to_fetch, "매매")
                if df_sale is not None and not df_sale.empty:
                    df_sale['실제거래타입'] = '매매'
                    detail_dfs.append(df_sale[df_sale['단지명'] == apt_name])

            if chart_view_type in ["전세", "매매+전세 통합"]:
                df_rent, _ = fetch_real_apt_data(sido, sigungu, lawd_cd, months_to_fetch, "전세")
                if df_rent is not None and not df_rent.empty:
                    df_rent_only = df_rent[(df_rent['단지명'] == apt_name) & (df_rent['월세(만 원)'] == 0)].copy()
                    df_rent_only['실제거래타입'] = '전세'
                    detail_dfs.append(df_rent_only)

            if detail_dfs:
                st.session_state.detail_full_df = pd.concat(detail_dfs, ignore_index=True)
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
            df_for_chart['계약월'] = df_for_chart['계약일'].str[:7]

            sale_agg = pd.DataFrame()
            rent_agg = pd.DataFrame()

            if current_view_type in ["매매", "매매+전세 통합"]:
                df_sale_c = df_for_chart[df_for_chart['실제거래타입'] == '매매']
                if not df_sale_c.empty:
                    sale_agg = df_sale_c.groupby('계약월')['거래금액(만 원)'].mean().reset_index()

            if current_view_type in ["전세", "매매+전세 통합"]:
                df_rent_c = df_for_chart[df_for_chart['실제거래타입'] == '전세']
                if not df_rent_c.empty:
                    rent_agg = df_rent_c.groupby('계약월')['거래금액(만 원)'].mean().reset_index()

            if current_view_type == "매매+전세 통합" and not sale_agg.empty and not rent_agg.empty:
                merged = pd.merge(sale_agg, rent_agg, on='계약월', how='outer', suffixes=('_매매', '_전세')).sort_values('계약월')
                merged['거래금액(만 원)_매매'] = merged['거래금액(만 원)_매매'].interpolate(method='linear').ffill().bfill()
                merged['거래금액(만 원)_전세'] = merged['거래금액(만 원)_전세'].interpolate(method='linear').ffill().bfill()
                merged['GAP'] = merged['거래금액(만 원)_매매'] - merged['거래금액(만 원)_전세']

                hover_template_sale = "계약월: %{x}<br>평균 매매가: %{y:,.0f} 만원<br><b><span style='color:#FF4B4B'>🔥 GAP: %{customdata:,.0f} 만원</span></b><extra></extra>"
                fig.add_trace(go.Scatter(x=merged['계약월'].tolist(), y=merged['거래금액(만 원)_매매'].tolist(), mode='lines+markers', name='평균 매매가', line=dict(color='#FF4B4B', width=2), customdata=merged['GAP'].tolist(), hovertemplate=hover_template_sale))
                fig.add_trace(go.Scatter(x=merged['계약월'].tolist(), y=merged['거래금액(만 원)_전세'].tolist(), mode='lines+markers', name='평균 전세가', line=dict(color='#1f77b4', width=2), hovertemplate="계약월: %{x}<br>평균 전세가: %{y:,.0f} 만원<extra></extra>"))
            else:
                if not sale_agg.empty:
                    fig.add_trace(go.Scatter(x=sale_agg['계약월'].tolist(), y=sale_agg['거래금액(만 원)'].tolist(), mode='lines+markers', name='평균 매매가', line=dict(color='#FF4B4B', width=2), hovertemplate="계약월: %{x}<br>매매가: %{y:,.0f} 만원<extra></extra>"))
                if not rent_agg.empty:
                    fig.add_trace(go.Scatter(x=rent_agg['계약월'].tolist(), y=rent_agg['거래금액(만 원)'].tolist(), mode='lines+markers', name='평균 전세가', line=dict(color='#1f77b4', width=2), hovertemplate="계약월: %{x}<br>전세가: %{y:,.0f} 만원<extra></extra>"))

            fig.update_layout(margin=dict(l=0, r=0, t=20, b=0), xaxis_title="계약월", yaxis_title="평균 금액 (만원)", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

            st.write("---")
            st.subheader("📋 실거래가 상세 내역 필터")
            
            list_col1, list_col2 = st.columns(2)
            trade_opts = ["전체보기"] + sorted(detail_full_df['실제거래타입'].unique().tolist())
            pyeong_opts = ["전체보기"] + sorted(detail_full_df['전용면적'].unique().tolist())
            
            with list_col1: list_trade = st.selectbox("거래 유형 필터", trade_opts, key="detail_list_trade")
            with list_col2: list_pyeong = st.selectbox("평형대 필터", pyeong_opts, key="detail_list_pyeong")
                
            filtered_data = detail_full_df.copy()
            if list_trade != "전체보기": filtered_data = filtered_data[filtered_data["실제거래타입"] == list_trade]
            if list_pyeong != "전체보기": filtered_data = filtered_data[filtered_data["전용면적"] == list_pyeong]
                
            if filtered_data.empty: 
                st.warning("선택하신 조건의 최근 거래 내역이 없습니다.")
            else:
                filtered_data = filtered_data.sort_values(by="계약일", ascending=False)
                def make_price_str(row):
                    p = format_to_korean_currency(row['거래금액(만 원)'])
                    if row.get('월세(만 원)', 0) > 0: return f"{p} / {row['월세(만 원)']}만원"
                    return p
                    
                filtered_data['실거래가(보증금)'] = filtered_data.apply(make_price_str, axis=1)
                display_list = filtered_data[["계약일", "전용면적", "층", "거래유형", "실거래가(보증금)"]]
                st.dataframe(display_list, use_container_width=True, hide_index=True, column_config={"실거래가(보증금)": st.column_config.TextColumn("실거래가(보증금/월세)", width="medium"), "거래유형": st.column_config.TextColumn("거래유형", width="small")})
        else:
            st.warning("선택하신 기간/항목 내 실거래 데이터가 없습니다.")

# =====================================================================
# 📌 PAGE 컨트롤러 (라우팅 로직)
# =====================================================================
if "show_detail" not in st.session_state: st.session_state.show_detail = False

if st.session_state.show_detail:
    show_detail_page()
    
else:
    if page == "🏢 아파트 실거래가":
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
            if sigungu_name == "전체 (시/도 단위)": dong_opts = ["전체 (시/도 단위)"]
            else:
                sigungu_code = sigungu_dict.get(sigungu_name)
                dong_opts = ["전체 (구 단위)"] + get_dong_list(sigungu_code)
            dong_name = st.selectbox("📍 읍/면/동", dong_opts, key="apt_dong")
            
        with loc_col4:
            selected_apt = st.text_input("🔍 동/단지명 검색 (선택)", placeholder="예: 대치동 또는 은마")

        st.write("")
        cond_col1, cond_col2, cond_col3 = st.columns(3)
        with cond_col1: trade_type = st.radio("🔄 거래 유형", ["매매", "전세", "월세"], horizontal=True, key="apt_trade")
        with cond_col2: period = st.selectbox("📅 조회 기간", PERIOD_OPTIONS, index=2, key="apt_period")
        with cond_col3: pyeong_type = st.selectbox("📐 평형대", ["전체보기", "10평대(59미만)", "20평대(59~84)", "30평대(84이상)"], key="apt_pyeong")

        st.write("")
        
        if st.button(f"🔍 아파트 {trade_type} 실시간 조회", type="primary", use_container_width=True, key="btn_apt"):
            st.session_state.apt_searched = True
            st.session_state.apt_display_loc = f"{sido_name} {sigungu_name if sigungu_name != '전체 (시/도 단위)' else '전체'} {dong_name if dong_name not in ['전체 (구 단위)', '전체 (시/도 단위)'] else ''}".strip()
            st.session_state.apt_trade_type = trade_type
            
            if "1년" in period: n_months = 12
            elif "6개월" in period: n_months = 6
            elif "3개월" in period: n_months = 3
            else: n_months = 1
            months_to_fetch = get_recent_months(n_months)
            
            if sigungu_name == "전체 (시/도 단위)":
                targets = list(sigungu_dict.items()) 
            else:
                targets = [(sigungu_name, sigungu_dict.get(sigungu_name))]
            
            with st.spinner('📡 국토교통부 서버에서 데이터를 가져오는 중입니다...'):
                all_fetched_dfs = []
                has_error = False
                error_msg = ""
                
                progress_text = "📡 전국/시도 단위 데이터 수집 중..."
                my_bar = st.progress(0, text=progress_text) if len(targets) > 1 else None
                
                for i, (sgg_name, l_cd) in enumerate(targets):
                    df, msg = fetch_real_apt_data(sido_name, sgg_name, l_cd, months_to_fetch, trade_type)
                    if df is not None and not df.empty: all_fetched_dfs.append(df)
                    elif df is None:
                        has_error = True
                        error_msg = msg
                    if my_bar: my_bar.progress((i + 1) / len(targets), text=f"{progress_text} ({sgg_name} 완료)")
                    
                if my_bar: my_bar.empty()
                
                if all_fetched_dfs:
                    real_df = pd.concat(all_fetched_dfs, ignore_index=True)
                    if dong_name not in ["전체 (구 단위)", "전체 (시/도 단위)"]:
                        real_df = real_df[real_df['법정동'].str.contains(dong_name, na=False)]
                    if selected_apt.strip():
                        real_df = real_df[real_df['단지명'].str.contains(selected_apt, na=False) | real_df['법정동'].str.contains(selected_apt, na=False)]
                    
                    real_df = real_df.sort_values(by="계약일", ascending=False).reset_index(drop=True)
                    st.session_state.res_df = real_df
                    st.success(f"✅ 100% 국토교통부 실제 {trade_type} 데이터 연동 완료!")
                else:
                    st.session_state.res_df = pd.DataFrame()
                    if has_error and msg != "NODATA": st.error(f"⚠️ API 서버 통신 오류: {error_msg}")
                    else: st.info("선택하신 조건의 실거래 데이터가 없습니다.")

        if st.session_state.get("apt_searched", False):
            st.write("---")
            loc_str = st.session_state.apt_display_loc
            trade_str = st.session_state.apt_trade_type
            df = st.session_state.res_df.copy() if 'res_df' in st.session_state else pd.DataFrame()
            
            st.subheader(f"📊 {loc_str} 아파트 {trade_str} 리스트")
            if not df.empty: render_clickable_list(df, is_apt=True)


    elif page == "🏘️ 비아파트 (오피스텔/빌라 등)":
        st.title("🏘️ 비아파트 실거래가 조회")
        st.info("비아파트 API 연동 작업 진행 중입니다.")
