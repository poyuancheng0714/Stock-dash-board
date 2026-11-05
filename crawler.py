import datetime
import json
import re
import time
import pandas as pd
import requests

def to_num(val):
    if not val:
        return 0.0
    try:
        return float(str(val).replace(',', '').strip())
    except Exception:
        return 0.0

def valid_code(c):
    s = str(c).strip()
    return len(s) == 4 and not s.startswith(('00', '28'))

def fetch_data():
    h = {'User-Agent': 'Mozilla/5.0'}
    
    # 1. 股價 (OpenAPI)
    url_p = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        r_p = requests.get(url_p, headers=h, timeout=20)
        p_list = r_p.json()
    except Exception as e:
        print("股價連線異常:", e)
        return None

    if not isinstance(p_list, list) or len(p_list) == 0:
        return None

    time.sleep(2)

    # 2. 法人 (OpenAPI / RWD 備援)
    i_list = None
    is_rwd = False
    urls = [
        "https://openapi.twse.com.tw/v1/fund/T86",
        "https://www.twse.com.tw/rwd/zh/fund/T86?selectType=ALL&response=json"
    ]
    for u in urls:
        try:
            r_i = requests.get(u, headers=h, timeout=20)
            res = r_i.json()
            if isinstance(res, list) and len(res) > 0:
                i_list = res
                is_rwd = False
                break
            elif isinstance(res, dict) and res.get('stat') == 'OK':
                i_list = res
                is_rwd = True
                break
        except Exception:
            pass
        time.sleep(2)

    if not i_list:
        return None

    # 整理股價
    df_p = pd.DataFrame(p_list)
    df_p = df_p[['Code', 'Name', 'ClosingPrice', 'Change']].copy()
    df_p.columns = ['代號', '名稱', '收盤價', '價差']
    df_p['收盤價'] = df_p['收盤價'].apply(to_num)
    df_p['價差'] = df_p['價差'].apply(to_num)
    df_p['昨收'] = df_p['收盤價'] - df_p['價差']
    df_p['漲跌幅'] = ((df_p['價差'] / df_p['昨收']) * 100).round(2)

    # 整理法人
    if is_rwd:
        df_i = pd.DataFrame(i_list['data'], columns=i_list['fields'])
        f_col = [c for c in df_i.columns if '外資' in c and '買賣超' in c][0]
        t_col = [c for c in df_i.columns if '投信' in c and '買賣超' in c][0]
        df_i = df_i[['證券代號', f_col, t_col]].copy()
    else:
        df_i = pd.DataFrame(i_list)
        f_col = [c for c in df_i.columns if 'Foreign' in c or '外資' in c][0]
        t_col = [c for c in df_i.columns if 'InvestmentTrust' in c or '投信' in c][0]
        c_col = 'Code' if 'Code' in df_i.columns else df_i.columns[0]
        df_i = df_i[[c_col, f_col, t_col]].copy()

    df_i.columns = ['代號', '外資張數', '投信張數']
    df_i['外資張數'] = (df_i['外資張數'].apply(to_num) / 1000).round(0)
    df_i['投信張數'] = (df_i['投信張數'].apply(to_num) / 1000).round(0)

    # 合併計算
    m = df_p.merge(df_i, on='代號', how='inner')
    m = m[m['代號'].apply(valid_code)].copy()
    m['外資金額'] = (m['外資張數'] * 1000 * m['收盤價'] / 1e8).round(2)
    m['投信金額'] = (m['投信張數'] * 1000 * m['收盤價'] / 1e8).round(2)
    return m

def make_list(df, s_col, n_col, asc=False):
    sub = df.sort_values(by=s_col, ascending=asc).head(20)
    out = []
    for i, (_, r) in enumerate(sub.iterrows(), 1):
        s_val = int(r[n_col])
        a_val = float(r[s_col])
        c_val = float(r['漲跌幅'])
        p_val = float(r['收盤價'])

        s_sgn = "+" if s_val > 0 else ""
        a_sgn = "+" if a_val > 0 else ""
        c_sgn = "+" if c_val > 0 else ""

        str_s = s_sgn + "{:,}".format(s_val)
        str_a = a_sgn + "{:.2f}".format(a_val)
        str_p = "{:,.1f}".format(p_val)
        str_c = c_sgn + "{:.2f}%".format(c_val)

        item = {
            'r': i,
            'c': str(r['代號']),
            'n': str(r['名稱']),
            'p': str_p,
            'chg': str_c,
            's': str_s,
            'a': str_a,
            'note': '官方盤後結算'
        }
        out.append(item)
    return out

if __name__ == '__main__':
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    df = fetch_data()

    if df is not None and len(df) > 0:
        time_tag = now.strftime('%Y-%m-%d %H:%M')
        badge = "最後更新時間：最新結算盤後數據 [更新於 " + time_tag + "]"
        
        f_top = make_list(df[df['外資金額'] > 0], '外資金額', '外資張數', False)
        f_bot = make_list(df[df['外資金額'] < 0], '外資金額', '外資張數', True)
        t_top = make_list(df[df['投信金額'] > 0], '投信金額', '投信張數', False)
        t_bot = make_list(df[df['投信金額'] < 0], '投信金額', '投信張數', True)

        db = {
            'FOREIGN': {'TWSE': {'TOP': f_top, 'BOT': f_bot}, 'TPEX': {'TOP': [], 'BOT': []}},
            'INST': {'TWSE': {'TOP': t_top, 'BOT': t_bot}, 'TPEX': {'TOP': [], 'BOT': []}},
            'MARGIN': {'TWSE': {'TOP': [], 'BOT': []}, 'TPEX': {'TOP': [], 'BOT':
