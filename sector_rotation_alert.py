import FinanceDataReader as fdr
import pandas as pd
import datetime
import requests
from bs4 import BeautifulSoup
import yaml

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

# 데이터 불러오기
end_date = datetime.date.today().strftime('%Y-%m-%d')
start_date = (datetime.date.today() - datetime.timedelta(days=period_days)).strftime('%Y-%m-%d')
print(f"데이터 기간: {start_date} ~ {end_date}")

try:
    data = fdr.DataReader(etf_list + [market_ticker], start=start_date, end=end_date)
except Exception as e:
    print(f"데이터 불러오기 실패: {e}")
    exit()

close_data = data['Close']
volume_data = data['Volume']
trade_value = volume_data * close_data

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
print(f"\n[분석 기준일: {end_date}]")
print("=" * 80)

summary = []
for symbol, name in sectors.items():
    if symbol not in close_data.columns:
        summary.append({'섹터': name, '수급증가율': None, '상대강도': None, '뉴스': ["데이터 없음"]})
        continue

    # 수급 증가율
    recent_tv = trade_value[symbol].tail(volume_recent).mean()
    prev_tv = trade_value[symbol].iloc[-(volume_recent + volume_prev):-volume_recent].mean()
    vol_inc = ((recent_tv - prev_tv) / prev_tv * 100) if prev_tv > 0 and pd.notna(prev_tv) else 0

    # 상대강도
    market_ret = close_data[market_ticker].pct_change(rs_window).iloc[-1] * 100
    sector_ret = close_data[symbol].pct_change(rs_window).iloc[-1] * 100
    rs_score = sector_ret - market_ret

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
    print(f"   수급 증가율: {vol:6.1f}%   |   RS: {row['상대강도']:6.2f}%")
    print("   뉴스:")
    for i, title in enumerate(row['뉴스'], 1):
        print(f"     {i}. {title}")
    print("-" * 80)
