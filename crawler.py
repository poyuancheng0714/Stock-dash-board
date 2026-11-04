import datetime
import json
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
    # 排除 ETF (00開頭)、金融保險 (28開頭)、權證或特別股 (非4碼)
    return len(c) != 4 or c.startswith('00') or c.startswith('28')

def fetch_twse_by_date(date_str):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

    try:
        p_res = requests.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={date_str}&response=json", headers=headers, timeout=15)
        p_twse = p_res.json()
        i_res = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL&response=json", headers=headers, timeout=15)
        i_twse = i_res.json()
    except Exception as e:
        print(f"[{date_str}] 連線異常: {e}")
        return None, False

    # 若非交易日或尚未產出
    if p_twse.get('stat') != 'OK' or i_twse.get('stat') != 'OK':
        return None, False

    # 嘗試抓取融資融券
    has_margin = False
    df_m_twse = pd.DataFrame(columns=['代號', '融資增減'])
    try:
        m_res = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=ALL&response=json", headers=headers, timeout=15)
        m_twse = m_res.json()
        if m_twse.get('stat') == 'OK' and 'tables' in m_twse and len(m_twse['tables']) > 0:
            df_m_twse = pd.DataFrame(m_twse['tables'][0]['data'])[[0, 5]].rename(columns={0: '代號', 5: '融資增減'})
            df_m_twse['融資增減'] = df_m_twse['融資增減'].apply(clean_num)
            has_margin = True
    except Exception as e:
        pass

    # 整理股價
    df_price_twse = pd.DataFrame(p_twse['data'], columns=p_twse['fields'])[['證券代號', '證券名稱', '收盤價', '漲跌價差']].rename(
        columns={'證券代號': '代號', '證券名稱': '名稱', '漲跌價差': '價差'}
    )
    df_price_twse['收盤價'] = df_price_twse['收盤價'].apply(clean_num)
    df_price_twse['價差'] = df_price_twse['價差'].apply(clean_num)
    df_price_twse['昨收'] = df_price_twse['收盤價'] - df_price_twse['價差']
    df_price_twse['漲跌幅'] = ((df_price_twse['價差'] / df_price_twse['昨收']) * 100).round(2)

    # 整理法人
    df_i_twse = pd.DataFrame(i_twse['data'], columns=i_twse['fields'])
    f_col = [c for c in df_i_twse.columns if '外資' in c and '買賣超' in c][0]
    t_col = [c for c in df_i_twse.columns if '投信' in c and '買賣超' in c][0]
    df_i_twse = df_i_twse[['證券代號', f_col, t_col]].rename(columns={'證券代號': '代號', f_col: '外資張數', t_col: '投信張數'})
    df_i_twse['外資張數'] = (df_i_twse['外資張數'].apply(clean_num) / 1000).round(0)
    df_i_twse['投信張數'] = (df_i_twse['投信張數'].apply(clean_num) / 1000).round(0)

    # 合併
    twse_all = df_price_twse.merge(df_i_twse, on='代號', how='inner')
    if has_margin:
        twse_all = twse_all.merge(df_m_twse, on='代號', how='left')
        twse_all['融資增減'] = twse_all['融資增減'].fillna(0)
    else:
        twse_all['融資增減'] = 0.0

    twse_all = twse_all[~twse_all['代號'].apply(is_excluded)].copy()
    twse_all['外資金額'] = (twse_all['外資張數'] * 1000 * twse_all['收盤價'] / 1e8).round(2)
    twse_all['投信金額'] = (twse_all['投信張數'] * 1000 * twse_all['收盤價'] / 1e8).round(2)
    twse_all['融資金額'] = (twse_all['融資增減'] * 1000 * twse_all['收盤價'] / 1e8).round(2)

    return twse_all, has_margin

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

    # 若今天尚未公佈或休市，自動往回找最近 7 天內最新的一個交易日
    df_twse = None
    target_date_str = ""
    has_margin = False

    for offset in range(0, 7):
        check_date = now_tw - datetime.timedelta(days=offset)
        d_str = check_date.strftime('%Y%m%d')
        print(f"嘗試抓取日期：{d_str} ...")
        df_res, margin_res = fetch_twse_by_date(d_str)
        if df_res is not None:
            df_twse = df_res
            has_margin = margin_res
            target_date_str = check_date.strftime('%Y-%m-%d')
            print(f"成功抓取到交易日數據：{target_date_str}")
            break

    if df_twse is not None:
        status_suffix = " (完整結算)" if has_margin else " (外資/投信結算，資券結算中)"
        update_time_str = f"{target_date_str} 資料 [更新於 {now_tw.strftime('%m/%d %H:%M')}]{status_suffix}"

        margin_top = build_top20(df_twse[df_twse['融資金額'] > 0], '融資金額', '融資增減', False) if has_margin else []
        margin_bot = build_top20(df_twse[df_twse['融資金額'] < 0], '融資金額', '融資增減', True) if has_margin else []

        new_db = {
            "FOREIGN": {
                "TWSE": {
                    "TOP": build_top20(df_twse[df_twse['外資金額'] > 0], '外資金額', '外資張數', False),
                    "BOT": build_top20(df_twse[df_twse['外資金額'] < 0], '外資金額', '外資張數', True)
                },
                "TPEX": {"TOP": [], "BOT": []}
            },
            "INST": {
                "TWSE": {
                    "TOP": build_top20(df_twse[df_twse['投信金額'] > 0], '投信金額', '投信張數', False),
                    "BOT": build_top20(df_twse[df_twse['投信金額'] < 0], '投信金額', '投信張數', True)
                },
                "TPEX": {"TOP": [], "BOT": []}
            },
            "MARGIN": {
                "TWSE": {
                    "TOP": margin_top,
                    "BOT": margin_bot
                },
                "TPEX": {"TOP": [], "BOT": []}
            }
        }

        with open("index.html", "r", encoding="utf-8") as f:
            html = f.read()

        html = re.sub(r'<div class="time-badge">.*?</div>', f'<div class="time-badge">最後更新時間：{update_time_str}</div>', html)
        db_json = json.dumps(new_db, ensure_ascii=False)
        html = re.sub(r'const db = \{.*?\};', f'const db = {db_json};', html)

        with open("index.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("index.html 資料庫已成功更新！")
    else:
        print("過去 7 天內均未抓到交易日數據。")
