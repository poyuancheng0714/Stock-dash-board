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
    return len(c) != 4 or c.startswith('00') or c.startswith('28')

def get_market_data():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    }

    # 1. 抓取上市收盤行情 (OpenAPI)
    url_p = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    print("正在請求證交所 OpenAPI 收盤行情...")
    p_data = []
    try:
        res_p = requests.get(url_p, headers=headers, timeout=25)
        if res_p.status_code == 200:
            p_data = res_p.json()
    except Exception as e:
        print(f"收盤行情連線異常: {e}")

    if not isinstance(p_data, list) or len(p_data) == 0:
        print("未取得有效的收盤行情數據")
        return None

    print(f"成功取得收盤股價，共 {len(p_data)} 筆！")
    time.sleep(2)

    # 2. 抓取三大法人買賣超 (具備雙端點容錯備援)
    inst_urls = [
        "https://openapi.twse.com.tw/v1/fund/T86",
        "https://www.twse.com.tw/rwd/zh/fund/T86?selectType=ALL&response=json"
    ]
    
    i_data = None
    is_rwd_format = False

    for u in inst_urls:
        print(f"正在請求三大法人數據: {u} ...")
        try:
            res_i = requests.get(u, headers=headers, timeout=25)
            if res_i.status_code == 200 and res_i.text.strip().startswith(('{', '[')):
                parsed = res_i.json()
                if isinstance(parsed, list) and len(parsed) > 0:
                    i_data = parsed
                    is_rwd_format = False
                    break
                elif isinstance(parsed, dict) and parsed.get('stat') == 'OK' and 'data' in parsed:
                    i_data = parsed
                    is_rwd_format = True
                    break
        except Exception as e:
            print(f"該端點解析略過: {e}")
        time.sleep(2)

    if not i_data:
        print("三大法人端點皆無回應，停止更新。")
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
    if is_rwd_format:
        df_i = pd.DataFrame(i_data['data'], columns=i_data['fields'])
        f_col = [c for c in df_i.columns if '外資' in c and '買賣超' in c][0]
        t_col = [c for c in df_i.columns if '投信' in c and '買賣超' in c][0]
        df_inst = df_i[['證券代號', f_col, t_col]].rename(
            columns={'證券代號': '代號', f_col: '外資張數', t_col: '投信張數'}
        )
    else:
        df_i = pd.DataFrame(i_data)
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
            's': f"{s_prefix}{int(r[shares_col]):
