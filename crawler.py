import datetime
import json
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
    # 排除 ETF (00開頭)、金融保險 (28開頭)、權證或特別股 (非4碼)
    return len(c) != 4 or c.startswith('00') or c.startswith('28')

def get_market_data():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
    }

    # 1. 抓取上市收盤行情 (TWSE 官方 OpenAPI，不擋雲端機房 IP)
    url_p = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    print("正在請求證交所 OpenAPI 收盤行情...")
    try:
        res_p = requests.get(url_p, headers=headers, timeout=25)
        p_data = res_p.json()
    except Exception as e:
        print(f"收盤行情連線異常: {e}")
        return None

    if not isinstance(p_data, list) or len(p_data) == 0:
        print("未取得有效的收盤行情數據")
        return None

    time.sleep(2)

    # 2. 抓取三大法人買賣超 (TWSE 官方 OpenAPI T86)
    url_i = "https://openapi.twse.com.tw/v1/fund/T86"
    print("正在請求證交所 OpenAPI 三大法人數據...")
    try:
        res_i = requests.get(url_i, headers=headers, timeout=25)
        i_data = res_i.json()
    except Exception as e:
        print(f"三大法人連線異常: {e}")
        return None

    if not isinstance(i_data, list) or len(i_data) == 0:
        print("未取得有效的三大法人數據")
        return None

    # 整理股價資料
    df_p = pd.DataFrame(p_data)
    df_p = df_p[['Code', 'Name', 'ClosingPrice', 'Change']].rename(
        columns={'Code': '代號', 'Name': '名稱', 'ClosingPrice': '收盤價', 'Change': '價差'}
    )
    df_p['收盤價'] = df_p['收盤價'].apply(clean_num)
    df_p['價差'] = df_p['價差'].apply(clean_num)
    df_p['昨收'] = df_p['收盤價'] - df_p['價差']
    df_p['漲跌幅'] = ((df_p['價差'] / df_p['昨收']) * 100).round(2)

    # 整理法人資料
    df_i = pd.DataFrame(i_data)
    # 動態配對外資與投信欄位
    f_cols = [c for c in df_i.columns if 'Foreign' in c or '外資' in c]
    t_cols = [c for c in df_i.columns if 'InvestmentTrust' in c or '投信' in c]
    f_target = f_cols[0] if f_cols else df_i.columns[4]
    t_target = t_cols[0] if t_cols else df_i.columns[10]

    code_col = 'Code' if 'Code' in df_i.columns else df_i.columns[0]
    df_inst = df_i[[code_col, f_target, t_target]].rename(
        columns={code_col: '代號', f_target: '外資張數', t_target: '投信張數'}
    )
    df_inst['外資張數'] = (df_inst['外資張數'].apply(clean_num) / 1000).round(0)
    df_inst['投信張數'] = (df_inst['投信張數'].apply(clean_num) / 1000).round(0)

    # 合併計算
    twse_all = df_p.merge(df_inst, on='代號', how='inner')
    twse_all = twse_all[~twse_all['代號'].apply(is_excluded)].copy()

    twse_all['外資金額'] = (twse_all['外資張數'] * 1000 * twse_all['收盤價'] / 1e8).round(2)
    twse_all['投信金額'] = (twse_all['投信張數'] * 1000 * twse_all['收盤價'] / 1e8).round(2)
    twse_all['融資金額'] = 0.0
    twse_all['融資增減'] = 0.0

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
            'note': '官方盤後結算'
        })
    return res

if __name__ == '__main__':
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)

    df_twse = get_market_data()

    if df_twse is not None and len(df_twse) > 0:
        update_time_str = f"最新結算盤後數據 [更新於 {now_tw.strftime('%Y-%m-%d %H:%M')}]"
        print(f"成功處理 {len(df_twse)} 檔個股籌碼資料！")

        new_db = {
            'FOREIGN': {
                'TWSE': {
                    'TOP': build_top20(df_twse[df_twse['外資金額'] > 0], '外資金額', '外資張數', False),
                    'BOT': build_top20(df_twse[df_twse['外資金額'] < 0], '外資金額', '外資張數', True)
                },
                'TPEX': {'TOP': [], 'BOT': []}
            },
            'INST': {
                'TWSE': {
                    'TOP': build_top20(df_twse[df_twse['投信金額'] > 0], '投信金額', '投信張數', False),
                    'BOT': build_top20(df_twse[df_twse['投信金額'] < 0], '投信金額', '投信張數', True)
                },
                'TPEX': {'TOP': [], 'BOT': []}
            },
            'MARGIN': {
                'TWSE': {'TOP': [], 'BOT': []},
                'TPEX': {'TOP': [], 'BOT': []}
            }
        }

        with open('index.html', 'r', encoding='utf-8') as f:
            html = f.read()

        html = re.sub(
            r'<div class="time-badge">.*?</div>',
            f'<div class="time-badge">最後更新時間：{update_time_str}</div>',
            html
        )
        db_json = json.dumps(new_db, ensure_ascii=False)
        html = re.sub(r'const db = \{.*?\};', f'const db = {db_json};', html)

        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("index.html 已成功寫入最新數據！")
    else:
        print("未抓取到有效盤後數據，保持原網頁內容。")
