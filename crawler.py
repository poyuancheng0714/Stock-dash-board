import datetime
import json
import os
import re
import time
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

def safe_get_json(url, headers):
    try:
        res = requests.get(url, headers=headers, timeout=20)
        if res.status_code == 200 and res.text.strip().startswith('{'):
            return res.json()
    except Exception as e:
        print(f"連線失敗 [{url}]: {e}")
    return None

def get_daily_data(date_str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }

    # 1. 上市收盤價
    url_p = f'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={date_str}&response=json'
    p_twse = safe_get_json(url_p, headers)
    if not p_twse or p_twse.get('stat') != 'OK':
        print(f"[{date_str}] 股價資料未公佈或取得失敗")
        return None

    # 間隔 3 秒避免被 TWSE 判定為連續爬蟲
    time.sleep(3)

    # 2. 上市三大法人
    url_i = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=json'
    i_twse = safe_get_json(url_i, headers)
    if not i_twse or i_twse.get('stat') != 'OK':
        print(f"[{date_str}] 法人資料未公佈或取得失敗")
        return None

    time.sleep(3)

    # 3. 上市融資融券
    url_m = f'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=ALL&response=json'
    m_twse = safe_get_json(url_m, headers)

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
    df_price_twse['昨收'] = df_price_twse['收盤價'] - df_price_twse['價差']
    df_price_twse['漲跌幅'] = (
        (df_price_twse['價差'] / df_price_twse['昨收']) * 100
    ).round(2)

    # 上市法人資料 (外資買賣超, 投信買賣超)
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
    df_m_twse = pd.DataFrame(columns=['代號', '融資增減'])
    if m_twse and m_twse.get('stat') == 'OK' and 'tables' in m_twse and len(m_twse['tables']) > 0:
        df_m_twse = pd.DataFrame(m_twse['tables'][0]['data'])[[0, 5]].rename(columns={0: '代號', 5: '融資增減'})
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
            'p': f"{r['收盤價']:,.1f}",
            'chg': f"{chg_prefix}{r['漲跌幅']:.2f}%",
            's': f"{s_prefix}{int(r[shares_col]):,}",
            'a': f"{a_prefix}{r[sort_col]:.2f}",
            'note': '官方盤後結算',
        })
    return res

if __name__ == '__main__':
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)

    # 若在下午 15:30 以前執行，當天尚未結算，自動改抓前一天
    if now_tw.hour < 15 or (now_tw.hour == 15 and now_tw.minute < 30):
        target_date = now_tw - datetime.timedelta(days=1)
    else:
        target_date = now_tw

    today = target_date.strftime('%Y%m%d')
    date_display = target_date.strftime('%Y-%m-%d')
    print(f"執行目標日期：{today}")

    df_twse = get_daily_data(today)

    if df_twse is not None:
        update_time_str = (
            f"{date_display} 數據 [更新於 {now_tw.strftime('%m/%d %H:%M')}] (盤後完整結算)"
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

        with open('index.html', 'r', encoding='utf-8') as f:
            html = f.read()

        html = re.sub(
            r'<div class="time-badge">.*?</div>',
            f'<div class="time-badge">最後更新時間：{update_time_str}</div>',
            html,
        )
        db_json = json.dumps(new_db, ensure_ascii=False)
        html = re.sub(r'const db = \{.*?\};', f'const db = {db_json};', html)

        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print('index.html 已成功更新完畢！')
    else:
        print('未抓取到有效盤後數據，保持原網頁內容。')
