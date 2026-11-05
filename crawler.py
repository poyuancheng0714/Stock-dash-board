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

def fetch_data(target_date_str):
    h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*'
    }
    
    # 1. 抓取當日指定收盤行情 (加入時間戳記防快取)
    ts = int(time.time() * 1000)
    url_p = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={target_date_str}&response=json&_={ts}"
    print(f"正在請求 [{target_date_str}] 收盤行情...")
    
    p_res = None
    try:
        r = requests.get(url_p, headers=h, timeout=25)
        res_json = r.json()
        if res_json.get('stat') == 'OK' and 'data' in res_json:
            p_res = res_json
    except Exception as e:
        print("指定日期收盤行情連線失敗，切換 OpenAPI:", e)

    # 若官網指定日期查詢無回應，切換 OpenAPI
    df_p = None
    if p_res:
        df_p = pd.DataFrame(p_res['data'], columns=p_res['fields'])[['證券代號', '證券名稱', '收盤價', '漲跌價差']].copy()
        df_p.columns = ['代號', '名稱', '收盤價', '價差']
    else:
        url_open = f"https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL?_={ts}"
        try:
            r_open = requests.get(url_open, headers=h, timeout=25).json()
            if isinstance(r_open, list) and len(r_open) > 0:
                df_p = pd.DataFrame(r_open)[['Code', 'Name', 'ClosingPrice', 'Change']].copy()
                df_p.columns = ['代號', '名稱', '收盤價', '價差']
        except Exception:
            return None

    if df_p is None or len(df_p) == 0:
        return None

    df_p['收盤價'] = df_p['收盤價'].apply(to_num)
    df_p['價差'] = df_p['價差'].apply(to_num)
    df_p['昨收'] = df_p['收盤價'] - df_p['價差']
    df_p['漲跌幅'] = ((df_p['價差'] / df_p['昨收']) * 100).round(2)

    time.sleep(2)

    # 2. 抓取當日指定三大法人
    url_i = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={target_date_str}&selectType=ALL&response=json&_={ts}"
    print(f"正在請求 [{target_date_str}] 三大法人數據...")
    i_res = None
    try:
        r_i = requests.get(url_i, headers=h, timeout=25)
        res_i = r_i.json()
        if res_i.get('stat') == 'OK' and 'data' in res_i:
            i_res = res_i
    except Exception as e:
        print("指定日期法人連線失敗，切換 OpenAPI:", e)

    df_i = None
    if i_res:
        df_raw = pd.DataFrame(i_res['data'], columns=i_res['fields'])
        f_col = [c for c in df_raw.columns if '外資' in c and '買賣超' in c][0]
        t_col = [c for c in df_raw.columns if '投信' in c and '買賣超' in c][0]
        df_i = df_raw[['證券代號', f_col, t_col]].copy()
        df_i.columns = ['代號', '外資張數', '投信張數']
    else:
        url_i_open = f"https://openapi.twse.com.tw/v1/fund/T86?_={ts}"
        try:
            res_open = requests.get(url_i_open, headers=h, timeout=25).json()
            if isinstance(res_open, list) and len(res_open) > 0:
                df_raw = pd.DataFrame(res_open)
                f_col = [c for c in df_raw.columns if 'Foreign' in c or '外資' in c][0]
                t_col = [c for c in df_raw.columns if 'InvestmentTrust' in c or '投信' in c][0]
                c_col = 'Code' if 'Code' in df_raw.columns else df_raw.columns[0]
                df_i = df_raw[[c_col, f_col, t_col]].copy()
                df_i.columns = ['代號', '外資張數', '投信張數']
        except Exception:
            return None

    if df_i is None or len(df_i) == 0:
        return None

    df_i['外資張數'] = (df_i['外資張數'].apply(to_num) / 1000).round(0)
    df_i['投信張數'] = (df_i['投信張數'].apply(to_num) / 1000).round(0)

    # 3. 合併並計算金額 (億元)
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
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)
    
    # 盤後 15:30 之後抓當天，15:30 之前抓前一天
    if now_tw.hour < 15 or (now_tw.hour == 15 and now_tw.minute < 30):
        target_dt = now_tw - datetime.timedelta(days=1)
    else:
        target_dt = now_tw

    target_date_str = target_dt.strftime('%Y%m%d')
    date_display = target_dt.strftime('%Y-%m-%d')
    print(f"鎖定查詢日期：{target_date_str}")

    df = fetch_data(target_date_str)

    if df is not None and len(df) > 0:
        time_tag = now_tw.strftime('%Y-%m-%d %H:%M')
        badge = f"最後更新時間：{date_display} 盤後完整結算 [更新於 {time_tag}]"
        
        f_top = make_list(df[df['外資金額'] > 0], '外資金額', '外資張數', False)
        f_bot = make_list(df[df['外資金額'] < 0], '外資金額', '外資張數', True)
        t_top = make_list(df[df['投信金額'] > 0], '投信金額', '投信張數', False)
        t_bot = make_list(df[df['投信金額'] < 0], '投信金額', '投信張數', True)

        empty_block = {'TOP': [], 'BOT': []}
        db = {
            'FOREIGN': {'TWSE': {'TOP': f_top, 'BOT': f_bot}, 'TPEX': empty_block},
            'INST': {'TWSE': {'TOP': t_top, 'BOT': t_bot}, 'TPEX': empty_block},
            'MARGIN': {'TWSE': empty_block, 'TPEX': empty_block}
        }

        with open('index.html', 'r', encoding='utf-8') as file:
            c = file.read()

        c = re.sub(r'<div class="time-badge">.*?</div>', f'<div class="time-badge">{badge}</div>', c)
        c = re.sub(r'const db = \{.*?\};', 'const db = ' + json.dumps(db, ensure_ascii=False) + ';', c)

        with open('index.html', 'w', encoding='utf-8') as file:
            file.write(c)
        print("成功同步更新當日股價與法人數據至 index.html！")
    else:
        print("無法取得完整數據，保持原狀態。")