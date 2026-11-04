import datetime
import json
import os
import re
import pandas as pd
import requests


def clean_num(val):
  if val is None or pd.isna(val):
    return 0.0
  s = str(val).replace(',', '').strip()
  try:
    return float(s)
  except ValueError:
    return 0.0


def is_excluded(code):
  c = str(code).strip()
  # 排除 ETF (00開頭)、金融保險 (28開頭)、特別股或權證 (非4碼)
  return len(c) != 4 or c.startswith('00') or c.startswith('28')


def get_daily_data(date_str):
  headers = {'User-Agent': 'Mozilla/5.0'}
  dt = datetime.datetime.strptime(date_str, '%Y%m%d')
  roc_date = f'{dt.year - 1911}/{dt.strftime("%m/%d")}'

  # 1. 上市收盤價與法人
  p_twse = requests.get(
      f'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={date_str}&response=json',
      headers=headers,
  ).json()
  i_twse = requests.get(
      f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=json',
      headers=headers,
  ).json()

  # 2. 上市融資融券
  m_twse = requests.get(
      f'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=ALL&response=json',
      headers=headers,
  ).json()

  # 3. 上櫃收盤價與法人
  p_tpex = requests.get(
      f'https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&d={roc_date}&se=EW',
      headers=headers,
  ).json()
  i_tpex = requests.get(
      f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={roc_date}',
      headers=headers,
  ).json()

  # 若當日非交易日
  if p_twse.get('stat') != 'OK' or not p_tpex.get('aaData'):
    print('今日非交易日或資料尚未公佈')
    return None

  # 建立上市價格表
  df_price_twse = pd.DataFrame(
      p_twse['data'], columns=p_twse['fields']
  )[['證券代號', '證券名稱', '收盤價', '漲跌價差']].rename(
      columns={
          '證券代號': '代號',
          '證券名稱': '名稱',
          '漲跌價差': '價差',
      }
  )
  df_price_twse['收盤價'] = df_price_twse['收盤價'].apply(clean_num)
  df_price_twse['價差'] = df_price_twse['價差'].apply(clean_num)

  # 計算漲跌幅
  df_price_twse['昨收'] = df_price_twse['收盤價'] - df_price_twse['價差']
  df_price_twse['漲跌幅'] = (
      (df_price_twse['價差'] / df_price_twse['昨收']) * 100
  ).round(2)

  # 上市法人資料 (外資第 4 欄買賣超, 投信第 10 欄買賣超)
  df_i_twse = pd.DataFrame(i_twse['data'], columns=i_twse['fields'])
  f_col = [c for c in df_i_twse.columns if '外資' in c and '買賣超' in c][0]
  t_col = [c for c in df_i_twse.columns if '投信' in c and '買賣超' in c][0]

  df_i_twse = df_i_twse[['證券代號', f_col, t_col]].rename(
      columns={'證券代號': '代號', f_col: '外資張數', t_col: '投信張數'}
  )
  df_i_twse['外資張數'] = (
      df_i_twse['外資張數'].apply(clean_num) / 1000
  ).round(0)
  df_i_twse['投信張數'] = (
      df_i_twse['投信張數'].apply(clean_num) / 1000
  ).round(0)

  # 上市融資
  df_m_twse = pd.DataFrame(m_twse['tables'][0]['data'])
  df_m_twse = df_m_twse[[0, 5]].rename(columns={0: '代號', 5: '融資增減'})
  df_m_twse['融資增減'] = df_m_twse['融資增減'].apply(clean_num)

  # 合併上市
  twse_all = df_price_twse.merge(df_i_twse, on='代號', how='inner').merge(
      df_m_twse, on='代號', how='left'
  )
  twse_all['融資增減'] = twse_all['融資增減'].fillna(0)
  twse_all = twse_all[~twse_all['代號'].apply(is_excluded)].copy()

  # 金額換算 (億元)
  twse_all['外資金額'] = (
      twse_all['外資張數'] * 1000 * twse_all['收盤價'] / 1e8
  ).round(2)
  twse_all['投信金額'] = (
      twse_all['投信張數'] * 1000 * twse_all['收盤價'] / 1e8
  ).round(2)
  twse_all['融資金額'] = (
      twse_all['融資增減'] * 1000 * twse_all['收盤價'] / 1e8
  ).round(2)

  return twse_all


def build_top20(df, sort_col, shares_col, is_asc=False):
  sub = df.sort_values(by=sort_col, ascending=is_asc).head(20)
  res = []
  for idx, (_, r) in enumerate(sub.iterrows(), 1):
    chg_prefix = '+' if r['漲跌幅'] > 0 else ''
    s_prefix = '+' if r[shares_col] > 0 else ''
    a_prefix = '+' if r[sort_col] > 0 else ''
    res.append({
        'r': idx,
        'c': str(r['代號']),
        'n': str(r['名稱']),
        'p': f'{r["收盤價"]:,.1f}',
        'chg': f'{chg_prefix}{r["漲跌幅"]:.2f}%',
        's': f'{s_prefix}{int(r[shares_col]):,}',
        'a': f'{a_prefix}{r[sort_col]:.2f}',
        'note': '官方盤後結算',
    })
  return res


if __name__ == '__main__':
  today = datetime.date.today().strftime('%Y%m%d')
  df_twse = get_daily_data(today)

  if df_twse is not None:
    # 產生資料結構
    update_time_str = (
        datetime.datetime.now().strftime('%Y-%m-%d %H:%M') + ' (盤後完整結算)'
    )
    new_db = {
        'FOREIGN': {
            'TWSE': {
                'TOP': build_top20(
                    df_twse[df_twse['外資金額'] > 0], '外資金額', '外資張數', False
                ),
                'BOT': build_top20(
                    df_twse[df_twse['外資金額'] < 0], '外資金額', '外資張數', True
                ),
            },
            'TPEX': {'TOP': [], 'BOT': []},
        },
        'INST': {
            'TWSE': {
                'TOP': build_top20(
                    df_twse[df_twse['投信金額'] > 0], '投信金額', '投信張數', False
                ),
                'BOT': build_top20(
                    df_twse[df_twse['投信金額'] < 0], '投信金額', '投信張數', True
                ),
            },
            'TPEX': {'TOP': [], 'BOT': []},
        },
        'MARGIN': {
            'TWSE': {
                'TOP': build_top20(
                    df_twse[df_twse['融資金額'] > 0], '融資金額', '融資增減', False
                ),
                'BOT': build_top20(
                    df_twse[df_twse['融資金額'] < 0], '融資金額', '融資增減', True
                ),
            },
            'TPEX': {'TOP': [], 'BOT': []},
        },
    }

    # 更新 index.html 內的資料結構與更新時間
    with open('index.html', 'r', encoding='utf-8') as f:
      html = f.read()

    # 替換時間徽章
    html = re.sub(
        r'<div class="time-badge">.*?</div>',
        f'<div class="time-badge">最後更新時間：{update_time_str}</div>',
        html,
    )
    # 替換 const db = {...};
    db_json = json.dumps(new_db, ensure_ascii=False)
    html = re.sub(r'const db = \{.*?\};', f'const db = {db_json};', html)

    with open('index.html', 'w', encoding='utf-8') as f:
      f.write(html)
    print('index.html 已成功更新完畢！')
