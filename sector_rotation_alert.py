"""
주식 테마 예측 봇 - KRX OPEN API 연동 버전
ETF 일별매매정보 API 사용 (승인 완료)
수급 분석 + 상대강도 + 뉴스 내러티브 + 텔레그램 알림 준비
"""

import requests
import pandas as pd
import datetime
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import os
import io

# .env 불러오기
load_dotenv()
KRX_API_KEY = os.getenv('KRX_API_KEY')
if not KRX_API_KEY:
    raise ValueError("KRX_API_KEY가 .env 파일에 없습니다. 파일 이름 '.env'와 내용 확인하세요.")

# config.yaml 불러오기
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

market_ticker = config['market_ticker']
sectors = config['sectors']
etf_list = list(sectors.keys())

analysis_config = config['analysis']
period_days = analysis_config['period_days']
rs_window = analysis_config['rs_window']
volume_recent = analysis_config['volume_compare_recent']
volume_prev = analysis_config['volume_compare_prev']
strong_threshold = analysis_config['volume_threshold_strong']
medium_threshold = analysis_config['volume_threshold_medium']

# 날짜 KRX 형식 (YYYYMMDD)
end_date = datetime.date.today().strftime('%Y%m%d')
start_date_dt = datetime.date.today() - datetime.timedelta(days=period_days)
start_date = start_date_dt.strftime('%Y%m%d')

print(f"데이터 기간: {start_date_dt} ~ {datetime.date.today()} (KRX 형식: {start_date} ~ {end_date})")

# KRX API OTP 생성 & 데이터 다운로드 함수
def get_krx_etf_daily(tickers, start, end):
    url_otp = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
    headers = {
        'Referer': 'http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020203',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    otp_params = {
        'locale': 'ko_KR',
        'mktId': 'ALL',
        'trdDd': end,
        'share': '1',
        'csvxls_isNo': 'false',
        'name': 'fileDown',
        'url': 'dbms/MDC/STAT/standard/MDCSTAT02201'
    }
    otp_response = requests.post(url_otp, headers=headers, data=otp_params)
    if otp_response.status_code != 200:
        raise Exception(f"OTP 생성 실패 (상태코드 {otp_response.status_code}): {otp_response.text}")

    otp = otp_response.text

    download_url = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
    download_params = {'code': otp}
    r = requests.post(download_url, headers=headers, data=download_params)

    if r.status_code != 200 or len(r.content) < 100:
        raise Exception(f"데이터 다운로드 실패 (상태코드 {r.status_code}, 크기 {len(r.content)} bytes)")

    # CSV 파싱 (euc-kr → utf-8 변환)
    df = pd.read_csv(io.StringIO(r.content.decode('euc-kr')), encoding='utf-8')

    # 디버깅용: 실제 받은 컬럼 출력 (처음 실행 시 확인용)
    print("KRX 응답 컬럼:", df.columns.tolist())

    # 필요한 컬럼 필터
    expected_cols = ['종목코드', '종가', '거래량', '거래대금(백만원)', '날짜']
    missing = [col for col in expected_cols if col not in df.columns]
    if missing:
        raise ValueError(f"KRX 응답에 필요한 컬럼 누락: {missing}")

    df = df[df['종목코드'].isin(tickers)]
    df['종가'] = pd.to_numeric(df['종가'].str.replace(',', ''), errors='coerce')
    df['거래량'] = pd.to_numeric(df['거래량'].str.replace(',', ''), errors='coerce')
    df['거래대금'] = pd.to_numeric(df['거래대금(백만원)'].str.replace(',', ''), errors='coerce') * 1_000_000
    df['날짜'] = pd.to_datetime(df['날짜'], format='%Y/%m/%d')

    close_data = df.pivot(index='날짜', columns='종목코드', values='종가').sort_index()
    volume_data = df.pivot(index='날짜', columns='종목코드', values='거래량').sort_index()
    trade_value = df.pivot(index='날짜', columns='종목코드', values='거래대금').sort_index()

    return close_data, volume_data, trade_value

# 데이터 불러오기
try:
    close_data, volume_data, trade_value = get_krx_etf_daily(etf_list + [market_ticker], start_date, end_date)
    print("KRX API 데이터 불러오기 성공! shape:", close_data.shape)
    print("컬럼 목록:", list(close_data.columns))
except Exception as e:
    print(f"KRX API 호출 실패: {e}")
    print("→ 승인 상태, API 키, 또는 기간 확인 필요")
    exit()

# 뉴스 함수
def get_news_headlines(keyword, max_headlines=3):
    headers = {'User-Agent': 'Mozilla/5.0'}
    url = f"https://search.naver.com/search.naver?where=news&query={keyword}&sm=tab_opt&sort=1"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        titles = soup.select('.news_tit')[:max_headlines]
        return [t.text.strip()[:60] + ('...' if len(t.text) > 60 else '') for t in titles] or ["뉴스 없음"]
    except Exception as e:
        return [f"뉴스 오류: {str(e)}"]

# 분석
print(f"\n[분석 기준일: {datetime.date.today().strftime('%Y-%m-%d')}]")
print("=" * 80)

summary = []
for symbol, name in sectors.items():
    if symbol not in close_data.columns:
        print(f"[{name}] 데이터 없음 (티커: {symbol})")
        summary.append({'섹터': name, '수급증가율': None, '상대강도': None, '뉴스': ["데이터 없음"]})
        continue

    recent_tv = trade_value[symbol].tail(volume_recent).mean()
    prev_tv = trade_value[symbol].iloc[-(volume_recent + volume_prev):-volume_recent].mean()
    vol_inc = ((recent_tv - prev_tv) / prev_tv * 100) if prev_tv > 0 and pd.notna(prev_tv) else 0

    try:
        market_ret = close_data[market_ticker].pct_change(rs_window).iloc[-1] * 100
        sector_ret = close_data[symbol].pct_change(rs_window).iloc[-1] * 100
        rs_score = sector_ret - market_ret
    except Exception as e:
        print(f"RS 계산 오류 ({name}): {e}")
        rs_score = 0.0

    headlines = get_news_headlines(name)

    summary.append({
        '섹터': name,
        '수급증가율': round(vol_inc, 1),
        '상대강도': round(rs_score, 2),
        '뉴스': headlines
    })

df_summary = pd.DataFrame(summary).sort_values(by='수급증가율', ascending=False)

for _, row in df_summary.iterrows():
    vol = row['수급증가율']
    if vol is None:
        print(f"[{row['섹터']}] 데이터 없음")
        continue
    status = "🔥 강한 수급" if vol > strong_threshold else "🟡 수급 증가" if vol > medium_threshold else "💤 정체"
    print(f"[{row['섹터']}] {status}")
    print(f"   수급 증가율: {vol:8.1f}%   |   RS: {row['상대강도']:7.2f}%")
    print("   뉴스:")
    for i, title in enumerate(row['뉴스'], 1):
        print(f"     {i}. {title}")
    print("-" * 80)
