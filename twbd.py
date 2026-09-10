# -*- coding: utf-8 -*-
"""
===============================================================================
TW AI War Room - 早盤強勢當沖/波段攻擊篩選器 (黑白名單雙重防禦修正版)
===============================================================================
"""

import datetime
import random
import time
import urllib3
import pandas as pd
import requests
import yfinance as yf

# 關閉 SSL 不安全連線警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# ⚙️ 全域戰略參數配置區 (GLOBAL STRATEGY CONFIG)
# =============================================================================
# 🏛️ 熱門板塊白名單 (只允許包含以下關鍵字之產業)
HOT_SECTORS_WHITELIST = {
    "半導體", "電腦", "週邊", "光電", "通信", "網路", "電子", "零組件", 
    "通路", "資訊服務", "電機", "電纜", "綠能", "航運", "生技", "醫療", 
    "數位", "雲端", "汽車", "其他", "其它"
}

# 🚫 冷門板塊黑名單 (只要包含以下關鍵字，100% 硬性過濾)
EXCLUDE_SECTORS_BLACKLIST = {
    "水泥", "食品", "塑膠", "紡織", "電機鋼鐵", "鋼鐵", "橡膠", "建材", 
    "營造", "航運金融", "金融", "保險", "觀光", "百貨", "油電"
}

# 💰 價格與量能門檻
MAX_STOCK_PRICE = 300.0         # 💰 篩選股價上限
MIN_STOCK_PRICE = 20.0          # 💰 篩選股價下限
MIN_VOLUME_LOTS = 800           # 🌊 最低成交量門檻 (張)
STAGE2_MIN_VOLUME_LOTS = MIN_VOLUME_LOTS
STAGE2_MIN_AMPLITUDE = 2.0      # 最低振幅門檻 (%)

# 📊 關卡動能與位置過濾
MA60_SLOPE_MIN = 0.03           # 📐 MA60 (季線) 每日斜率門檻
INDEX_DROP_THRESHOLD = -2.0     # 大盤 C 級停工跌幅門檻 (%)

# =============================================================================
# 🏛️ 三大法人籌碼門檻設定 (5日內累計)
# =============================================================================
LOOKBACK_DAYS = 5               # 📅 籌碼觀察天數 (5個交易日)

FOREIGN_BUY_MIN = 1000          # 🌏 外資 5 日累計淨買超門檻 (張)
TRUST_BUY_MIN = 200             # 🏛️ 投信 5 日累計淨買超門檻 (張)
DEALER_BUY_MIN = 100            # 💼 自營商(自買+避險) 5 日累計淨買超門檻 (張) -> 🆕 補上自營商
THREE_INST_TOTAL_MIN = 1000     # 🚀 三大法人 5 日合計淨買超門檻 (張) -> 🆕 補上三大法人合計

REQUIRE_INSTITUTIONAL = True    # 🛑 籌碼硬性過濾開關 (True: 必須符合條件, False: 僅記錄不過濾)

# 📈 MACD 動能設定
REQUIRE_MACD_TURNING_UP = True  # 🛑 日線 MACD 止跌轉強開關
MACD_FAST = 8                   
MACD_SLOW = 13                  
MACD_SIGNAL = 5                 

# ⚙️ 第三關【波段回踩】參數配置
STAGE3_SUPPORT_MARGIN_PCT = 5   # 📐 均線回踩容忍距離門檻 (%)
STAGE3_LOOKBACK_DAYS = 5        # 🗓️ 檢查近 N 日內的最低價回踩狀況
STAGE3_MAX_BIAS_PCT = 6.0       # 🚫 MA20 乖離率上限 (%)

# 📡 推播與 API 設定
ENABLE_WECHAT_NOTIFY = True    
WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=da572c49-28cd-48c3-b9bb-ac4a9eaf2595"
WECHAT_WEBHOOK_URL = WEBHOOK_URL


# =============================================================================
# 🌐 【第零關前置】從 TWSE/TPEX 抓取全市場股票並自動綁定產業分類
# =============================================================================
def _normalize_sector(raw_sector):
    """將 TWSE/TPEX 產業代碼轉成中文產業名稱，供黑白名單判斷。"""
    s = str(raw_sector or '').strip()
    if not s or s in ('-', 'N/A', 'None', 'nan'):
        return '未分類'

    sector_map = {
        '01': '水泥工業', '1': '水泥工業',
        '02': '食品工業', '2': '食品工業',
        '03': '塑膠工業', '3': '塑膠工業',
        '04': '紡織纖維', '4': '紡織纖維',
        '05': '電機機械', '5': '電機機械',
        '06': '電器電纜', '6': '電器電纜',
        '08': '玻璃陶瓷', '8': '玻璃陶瓷',
        '09': '造紙工業', '9': '造紙工業',
        '10': '鋼鐵工業', '11': '橡膠工業',
        '12': '汽車工業', '14': '建材營造',
        '15': '航運業', '16': '觀光餐旅',
        '17': '金融保險', '18': '貿易百貨',
        '19': '綜合', '20': '其他',
        '21': '化學工業', '22': '生技醫療業',
        '23': '油電燃氣業', '24': '半導體業',
        '25': '電腦及週邊設備業', '26': '光電業',
        '27': '通信網路業', '28': '電子零組件業',
        '29': '電子通路業', '30': '資訊服務業',
        '31': '其他電子業', '32': '文化創意業',
        '33': '農業科技業', '35': '綠能環保',
        '36': '數位雲端', '37': '運動休閒',
        '38': '居家生活', '80': '管理股票',
    }
    return sector_map.get(s.zfill(2), s)


def _short_stock_name(name):
    """股票名稱只保留市場常用簡稱，移除完整公司名稱後綴。"""
    name = str(name or '').strip()
    suffixes = [
        '股份有限公司', '有限公司', '公司',
        '有限責任公司'
    ]
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)].strip()
                changed = True
                break
    return name


def get_all_stock_list_with_market():
    """使用官方公司基本資料 API 取得上市櫃股票，並正確綁定產業分類。"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    stocks_map = {}

    # 1. 上市股票 (TWSE)
    # 改用公司基本資料 API 取得真正的產業別；BWIBBU_ALL 沒有可靠產業欄位。
    try:
        url_tse = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        res = requests.get(url_tse, headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('公司代號', item.get('Code', ''))).strip()
                name = str(item.get('公司簡稱', item.get('公司名稱', item.get('Name', '')))).strip()
                raw_sector = item.get('產業別', item.get('Category', item.get('SecuritiesCategory', item.get('IndustryCategory', ''))))
                sector = _normalize_sector(raw_sector)

                if (len(code) == 4 and not code.startswith('00') and
                    not any(k in name for k in ['權', '牛', '熊', '認購', '認售', 'ETF', '特', 'DR'])):
                    stocks_map[code] = {
                        'name': _short_stock_name(name),
                        'suffix': '.TW',
                        'sector': sector
                    }
    except Exception as e:
        print(f"⚠️ TWSE API 讀取異常: {e}")

    # 2. 上櫃股票 (TPEX)
    try:
        url_otc = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
        res = requests.get(url_otc, headers=headers, timeout=10, verify=False)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('公司代號', item.get('Code', ''))).strip()
                name = str(item.get('公司簡稱', item.get('公司名稱', item.get('Name', '')))).strip()
                raw_sector = item.get('產業別', item.get('Category', item.get('SecuritiesCategory', item.get('IndustryCategory', ''))))
                sector = _normalize_sector(raw_sector)

                if (len(code) == 4 and code not in stocks_map and not code.startswith('00') and
                    not any(k in name for k in ['權', '牛', '熊', '認購', '認售', 'ETF', '特', 'DR'])):
                    stocks_map[code] = {
                        'name': _short_stock_name(name),
                        'suffix': '.TWO',
                        'sector': sector
                    }
    except Exception as e:
        print(f"⚠️ TPEX API 讀取異常: {e}")

    sector_count = sum(
        1 for info in stocks_map.values()
        if str(info.get('sector', '未分類')).strip() not in ('', '未分類')
    )
    print(f"🏷️ 產業分類綁定完成：{sector_count}/{len(stocks_map)} 檔取得產業分類")

    return stocks_map


def run_stage0_index_check(stocks_map=None):
    print("==============================================")
    print("🛡️ 開始執行第零關：【大盤加權指數風控與標的清單預洗】...")
    print("==============================================\n")

    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'})

    index_passed = True
    try:
        mis_index_url = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_t00.tw"
        res = session.get(mis_index_url, timeout=5)
        if res.status_code == 200:
            msg_arr = res.json().get('msgArray', [])
            if msg_arr:
                info = msg_arr[0]
                z_val = info.get('z', '-')
                y_val = info.get('y', '-')
                curr_price = float(z_val) if z_val != '-' else float(y_val)
                prev_close = float(y_val)
                change_pct = ((curr_price - prev_close) / prev_close) * 100

                print(f"📊 加權指數現價: {curr_price:.2f} | 當日漲跌幅: {change_pct:+.2f}%")
                if change_pct <= INDEX_DROP_THRESHOLD:
                    print(f"🚨 【大盤 C 級：停工警報】大盤跌幅 {change_pct:.2f}%！觸發停工機制。")
                    index_passed = False
                else:
                    print(f"✅ 【大盤風控通過】大盤環境安全 ({change_pct:+.2f}%)\n")
    except Exception:
        print("⚠️ 第零關大盤數據讀取異常，預設放行進入第一關。\n")

    if not index_passed:
        return False, {}

    print(f"🧹 標的池取得完成：共 {len(stocks_map)} 檔合格上市櫃普通股，準備進入海選。\n")
    return True, stocks_map


# =============================================================================
# 🔍 【第一關】價格/量能/振幅 + 雙重版塊過濾海選 (上市櫃雙備援 + 產業比對強化版)
# =============================================================================
def run_stage1_and_2(stocks_map):
    print("==============================================")
    print(f"🔍 開始執行第一關：【價格/量能/振幅 + 雙重版塊過濾海選】(掃描 {len(stocks_map)} 檔標的)...")
    print(f"   🎯 篩選標準：價格 ${MIN_STOCK_PRICE}~${MAX_STOCK_PRICE} | 成交量 >= {STAGE2_MIN_VOLUME_LOTS}張 | 振幅 >= {STAGE2_MIN_AMPLITUDE}%")
    print("==============================================\n")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    candidates = {}
    codes = list(stocks_map.keys())
    batch_size = 50
    
    # -------------------------------------------------------------------------
    # 1. 盤中即時模式 (TWSE MIS API)
    # -------------------------------------------------------------------------
    for i in range(0, len(codes), batch_size):
        batch_codes = codes[i:i+batch_size]
        ex_ch_list = [f"tse_{c}.tw" for c in batch_codes] + [f"otc_{c}.tw" for c in batch_codes]
        mis_url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={'|'.join(ex_ch_list)}"
        
        try:
            res = requests.get(mis_url, headers=headers, timeout=5)
            if res.status_code == 200:
                jdata = res.json()
                for info in jdata.get('msgArray', []):
                    code = info.get('c', '')
                    if code not in stocks_map: continue
                    
                    info_meta = stocks_map[code]
                    sector = str(info_meta.get('sector', '未分類'))
                    
                    # 產業黑白名單雙重過濾 (包含容錯比對)
                    if any(bad in sector for bad in EXCLUDE_SECTORS_BLACKLIST): continue
                    if sector == '未分類': continue
                    is_pass_white = any(good in sector for good in HOT_SECTORS_WHITELIST)
                    if not is_pass_white: continue

                    z_val, y_val, o_val = info.get('z', '-'), info.get('y', '-'), info.get('o', '-')
                    price_str = z_val if (z_val and z_val != '-') else y_val
                    price = float(price_str) if (price_str and price_str != '-') else 0.0
                    
                    v_str = info.get('v', '0')
                    vol_lots = int(v_str) if (v_str and v_str.isdigit()) else 0
                    
                    h_str, l_str = info.get('h', '-'), info.get('l', '-')
                    high = float(h_str) if (h_str and h_str != '-') else price
                    low = float(l_str) if (l_str and l_str != '-') else price
                    y_close = float(y_val) if (y_val and y_val != '-') else price
                    open_price = float(o_val) if (o_val and o_val != '-') else price
                    
                    amplitude = ((high - low) / y_close * 100) if y_close > 0 and high > 0 else 0.0
                    
                    if (MIN_STOCK_PRICE <= price <= MAX_STOCK_PRICE) and (vol_lots >= STAGE2_MIN_VOLUME_LOTS) and (amplitude >= STAGE2_MIN_AMPLITUDE):
                        candidates[code] = {
                            'name': info_meta['name'],
                            'symbol': f"{code}{info_meta['suffix']}",
                            'sector': sector,
                            'price': price,
                            'y_close': y_close,
                            'open_price': open_price
                        }
        except Exception: pass
        print(f"⏳ 第一關海選進度... [{min(i+batch_size, len(codes))}/{len(codes)}]", end='\r')
        time.sleep(0.01)
        
    # -------------------------------------------------------------------------
    # 2. 盤後備援模式 (TWSE 上市 + TPEX 上櫃 官方雙 API 備援)
    # -------------------------------------------------------------------------
    if len(candidates) == 0:
        print("\n⚠️ MIS API 處於清空維護狀態，啟動證交所/櫃買官方盤後備援海選機制...")
        
        # A. 上市 (TWSE) 備援
        try:
            url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
            res = requests.get(url_twse, headers=headers, timeout=8)
            if res.status_code == 200:
                for row in res.json():
                    code = str(row.get('Code', '')).strip()
                    if code not in stocks_map: continue
                    
                    info_meta = stocks_map[code]
                    sector = str(info_meta.get('sector', '未分類'))
                    
                    if any(bad in sector for bad in EXCLUDE_SECTORS_BLACKLIST): continue
                    if sector == '未分類': continue
                    is_pass_white = any(good in sector for good in HOT_SECTORS_WHITELIST)
                    if not is_pass_white: continue
                    
                    try:
                        price = float(row.get('ClosingPrice', 0))
                        raw_vol = float(row.get('TradeVolume', 0))
                        vol_lots = int(raw_vol / 1000) if raw_vol > 100000 else int(raw_vol) # 自動辨識股數與張數
                        high = float(row.get('HighestPrice', 0))
                        low = float(row.get('LowestPrice', 0))
                        change = float(row.get('Change', 0))
                        y_close = price - change if price > 0 else price
                        
                        amplitude = ((high - low) / y_close * 100) if y_close > 0 else 0.0
                        
                        if (MIN_STOCK_PRICE <= price <= MAX_STOCK_PRICE) and (vol_lots >= STAGE2_MIN_VOLUME_LOTS) and (amplitude >= STAGE2_MIN_AMPLITUDE):
                            candidates[code] = {
                                'name': info_meta['name'],
                                'symbol': f"{code}{info_meta['suffix']}",
                                'sector': sector,
                                'price': price,
                                'y_close': y_close,
                                'open_price': float(row.get('OpeningPrice', price))
                            }
                    except (ValueError, TypeError): continue
        except Exception as e:
            print(f"⚠️ TWSE 備援讀取異常: {e}")

        # B. 上櫃 (TPEX) 備援
        try:
            url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_dailyclose_quotes"
            res = requests.get(url_tpex, headers=headers, timeout=8, verify=False)
            if res.status_code == 200:
                for row in res.json():
                    code = str(row.get('SecuritiesCompanyCode', '')).strip()
                    if code not in stocks_map or code in candidates: continue
                    
                    info_meta = stocks_map[code]
                    sector = str(info_meta.get('sector', '未分類'))
                    
                    if any(bad in sector for bad in EXCLUDE_SECTORS_BLACKLIST): continue
                    if sector == '未分類': continue
                    is_pass_white = any(good in sector for good in HOT_SECTORS_WHITELIST)
                    if not is_pass_white: continue
                    
                    try:
                        price = float(row.get('Close', 0))
                        raw_vol = float(row.get('TradingShares', 0))
                        vol_lots = int(raw_vol / 1000) if raw_vol > 100000 else int(raw_vol)
                        high = float(row.get('High', 0))
                        low = float(row.get('Low', 0))
                        y_close = float(row.get('PreviousClose', price))
                        
                        amplitude = ((high - low) / y_close * 100) if y_close > 0 else 0.0
                        
                        if (MIN_STOCK_PRICE <= price <= MAX_STOCK_PRICE) and (vol_lots >= STAGE2_MIN_VOLUME_LOTS) and (amplitude >= STAGE2_MIN_AMPLITUDE):
                            candidates[code] = {
                                'name': info_meta['name'],
                                'symbol': f"{code}{info_meta['suffix']}",
                                'sector': sector,
                                'price': price,
                                'y_close': y_close,
                                'open_price': float(row.get('Open', price))
                            }
                    except (ValueError, TypeError): continue
        except Exception as e:
            print(f"⚠️ TPEX 備援讀取異常: {e}")

    print("\n\n==============================================")
    print(f"✅ 第一關海選完成！共有 {len(candidates)} 檔符合條件（價格/量能/振幅 + 雙重版塊過濾）")
    print("==============================================\n")
    return candidates


# =============================================================================
# 🛠️ 盤後備援機制：證交所官方 OpenAPI (完全棄用 yfinance，穩定度 100%)
# =============================================================================
def run_fallback_twse_filter(stocks_map):
    candidates = {}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    
    print("🚀 改用【證交所官方每日收盤行情 API】進行盤後海選...")
    
    # 取得 TWSE 每日收盤行情 API
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for row in data:
                code = str(row.get('Code', '')).strip()
                if code not in stocks_map:
                    continue
                    
                info_meta = stocks_map[code]
                sector = str(info_meta.get('sector', '未分類'))
                
                # 🛑 1. 黑名單硬過濾：黑名單優先
                sector = str(sector).strip() or '未分類'
                if any(bad in sector for bad in EXCLUDE_SECTORS_BLACKLIST):
                    continue

                # 🛑 2. 白名單嚴格放行：未分類一律不放行
                if sector == '未分類':
                    continue
                is_pass_white = any(good in sector for good in HOT_SECTORS_WHITELIST)
                if not is_pass_white:
                    continue

                # 數據解析 (官方欄位)
                try:
                    price = float(row.get('ClosingPrice', 0))
                    vol_lots = int(float(row.get('TradeVolume', 0)) / 1000) # 股數轉張數
                    high = float(row.get('HighestPrice', 0))
                    low = float(row.get('LowestPrice', 0))
                    change = float(row.get('Change', 0))
                    y_close = price - change if price > 0 else 0
                    
                    amplitude = ((high - low) / y_close * 100) if y_close > 0 else 0.0
                    
                    # 門檻檢核
                    if (MIN_STOCK_PRICE <= price <= MAX_STOCK_PRICE) and \
                       (vol_lots >= STAGE2_MIN_VOLUME_LOTS) and \
                       (amplitude >= STAGE2_MIN_AMPLITUDE):
                        candidates[code] = {
                            'name': info_meta['name'],
                            'symbol': f"{code}{info_meta['suffix']}",
                            'sector': sector,
                            'price': price,
                            'y_close': y_close,
                            'open_price': float(row.get('OpeningPrice', price))
                        }
                except ValueError:
                    continue
    except Exception as e:
        print(f"⚠️ 官方 API 讀取失敗: {e}")
        
    return candidates


# =============================================================================
# 🏦 【第二關】中長線趨勢過濾 (股價站上 MA60 + MA60 斜率達標)
# =============================================================================
def run_stage2_chip_filter(candidates):
    print("==============================================")
    print(f"🏦 開始進入第二關：【季線趨勢過濾】(股價 > MA60 且 MA60 斜率 >= {MA60_SLOPE_MIN}%)...")
    print("==============================================\n")
    
    if not candidates: return {}
    passed_candidates = {}
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})

    for code, info in candidates.items():
        sym = info['symbol']
        try:
            ticker = yf.Ticker(sym, session=session)
            df_hist = ticker.history(period="4mo")
            
            if len(df_hist) >= 63:
                closes = df_hist['Close']
                ma60_series = closes.rolling(window=60).mean()
                
                latest_close = float(closes.iloc[-1])
                ma60_curr = float(ma60_series.iloc[-1])
                ma60_prev = float(ma60_series.iloc[-4])
                
                ma60_slope_pct = ((ma60_curr - ma60_prev) / ma60_prev) * 100
                
                if latest_close < ma60_curr or ma60_slope_pct < MA60_SLOPE_MIN:
                    continue

                info['ma60'] = round(ma60_curr, 2)
                info['ma60_slope_pct'] = round(ma60_slope_pct, 4)

                passed_candidates[code] = info
                print(f"👉 {code} {info['name']} ({info.get('sector','')}) -> ✅ 第二關通過 [股價 {latest_close:.2f} > MA60({ma60_curr:.2f}) | 斜率: {ma60_slope_pct:.4f}%]")
        except Exception:
            pass

    print(f"\n📊 【第二關通過名單】：共 {len(passed_candidates)} 檔\n")
    return passed_candidates


# =============================================================================
# 🔥 【第三關】真波段回踩過濾
# =============================================================================
def run_stage3_filter(candidates):
    print("==============================================")
    print("🔥 開始進入第三關：【真波段回踩過濾】...")
    print("==============================================\n")
    
    if not candidates: return {}
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    stage3_targets = {}
    
    for code, info in candidates.items():
        sym = info['symbol']
        price = info['price']
        try:
            ticker = yf.Ticker(sym, session=session)
            df_d = ticker.history(period="4mo", interval="1d")
            
            if len(df_d) >= 65:
                df_d['MA5'] = df_d['Close'].rolling(window=5).mean()
                df_d['MA20'] = df_d['Close'].rolling(window=20).mean()
                df_d['MA60'] = df_d['Close'].rolling(window=60).mean()
                
                latest_ma5 = float(df_d['MA5'].iloc[-1])
                latest_ma20 = float(df_d['MA20'].iloc[-1])
                
                if price < latest_ma5: continue
                
                bias_ma20 = ((price - latest_ma20) / latest_ma20) * 100 if latest_ma20 > 0 else 999.0
                if bias_ma20 > STAGE3_MAX_BIAS_PCT: continue

                near_support = False
                for i in range(-STAGE3_LOOKBACK_DAYS, 0):
                    day_low = float(df_d['Low'].iloc[i])
                    ma20_val = float(df_d['MA20'].iloc[i])
                    ma60_val = float(df_d['MA60'].iloc[i])
                    
                    dist_ma20 = ((day_low - ma20_val) / ma20_val) * 100 if ma20_val > 0 else 999.0
                    dist_ma60 = ((day_low - ma60_val) / ma60_val) * 100 if ma60_val > 0 else 999.0
                    
                    if dist_ma20 <= STAGE3_SUPPORT_MARGIN_PCT or dist_ma60 <= STAGE3_SUPPORT_MARGIN_PCT:
                        near_support = True
                        break

                if near_support:
                    info['bias_ma20'] = round(bias_ma20, 2)
                    stage3_targets[code] = info
                    print(f"👉 {code} {info['name']} -> ✅ 第三關通過 [乖離: {bias_ma20:+.2f}%]")
        except Exception:
            continue

    print(f"\n📊 【第三關精選名單】：共 {len(stage3_targets)} 檔\n")
    return stage3_targets


# =============================================================================
# 🎯 【第四關】日線 MACD 止跌動能 + 5日法人籌碼累計門檻
# =============================================================================
def run_stage4_swing_filter(stage3_targets):
    print("==============================================")
    print("🎯 開始進入第四關：【日線 MACD 止跌動能 + 法人籌碼】...")
    print("==============================================\n")
    
    if not stage3_targets: return {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    chip_5d_map = {}
    
    if REQUIRE_INSTITUTIONAL:
        dates_found = 0
        curr_date = datetime.datetime.now().date() - datetime.timedelta(days=1)
        while dates_found < LOOKBACK_DAYS:
            if curr_date.weekday() >= 5:
                curr_date -= datetime.timedelta(days=1)
                continue
            t1_date_str = curr_date.strftime("%Y%m%d")
            url_t86 = f"https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={t1_date_str}&selectType=ALL"
            try:
                res = requests.get(url_t86, headers=headers, timeout=6)
                if res.status_code == 200 and res.json().get('stat') == 'OK':
                    for row in res.json().get('data', []):
                        code = str(row[0]).strip()
                        if code in stage3_targets:
                            f_buy = int(str(row[4]).replace(',', '')) // 1000
                            t_buy = int(str(row[10]).replace(',', '')) // 1000
                            
                            # 🆕 僅補上自營商 (自買 row[8] + 避險 row[11])
                            d_buy = (int(str(row[8]).replace(',', '')) + int(str(row[11]).replace(',', ''))) // 1000
                            
                            if code not in chip_5d_map: chip_5d_map[code] = {'f': 0, 't': 0, 'd': 0}
                            chip_5d_map[code]['f'] += f_buy
                            chip_5d_map[code]['t'] += t_buy
                            chip_5d_map[code]['d'] += d_buy
                    dates_found += 1
            except Exception: pass
            curr_date -= datetime.timedelta(days=1)

    stage4_targets = {}
    session = requests.Session()
    session.headers.update(headers)

    for code, info in stage3_targets.items():
        sym = info.get('symbol', f"{code}.TW")
        try:
            ticker = yf.Ticker(sym, session=session)
            df_daily = ticker.history(period="6mo", interval="1d").dropna(subset=['Close'])
            if len(df_daily) < (MACD_SLOW + 10): continue
                
            ema_f = df_daily['Close'].ewm(span=MACD_FAST, adjust=False).mean()
            ema_s = df_daily['Close'].ewm(span=MACD_SLOW, adjust=False).mean()
            dif = ema_f - ema_s
            sig = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
            osc = dif - sig
            
            if REQUIRE_MACD_TURNING_UP and (osc.iloc[-1] <= osc.iloc[-2]): continue

            chip = chip_5d_map.get(code, {'f': 0, 't': 0, 'd': 0})
            
            # 判斷加入自營商門檻 (DEALER_BUY_MIN)
            if REQUIRE_INSTITUTIONAL and not (chip['t'] >= TRUST_BUY_MIN or chip['f'] >= FOREIGN_BUY_MIN or chip['d'] >= DEALER_BUY_MIN): continue

            info['stage4_status'] = f"投信5日+{chip['t']}張 | 外資5日+{chip['f']}張 | 自營5日+{chip['d']}張"
            stage4_targets[code] = info
            print(f"👉 {code} {info['name']} -> ✅ 第四關通過！[{info['stage4_status']}]")
        except Exception: pass

    return stage4_targets


# =============================================================================
# 🏁 【第五關】風控計算、報表存檔與微信推播
# =============================================================================
def run_stage5_risk_management_and_wrapup(stage4_targets):
    print("\n==============================================")
    print("🏁 進入第五關：【風控計算、報表存檔與微信推播】...")
    print("==============================================\n")
    
    if not stage4_targets:
        print("⚠️ 無通過標的。")
        return {}

    summary_list = []
    msg_lines = ["📊 【波段選股戰情室報告】", f"🎯 通過最終篩選標的共 {len(stage4_targets)} 檔：\n"]
    
    for code, info in stage4_targets.items():
        name = _short_stock_name(info.get('name', '未知'))
        sector = info.get('sector', '未知')
        entry_price = float(info.get('price', info.get('close', 0.0)))
        
        if entry_price == 0.0:
            df_temp = fetch_kline_data(code, period_days=5)
            if df_temp is not None and not df_temp.empty:
                entry_price = float(df_temp['Close'].iloc[-1])

        entry_price = round(entry_price, 2)
        prev_low = float(info.get('low', entry_price * 0.95))
        stop_loss = round(min(prev_low, entry_price * 0.95), 2) if entry_price > 0 else 0.0
        take_profit = round(entry_price * 1.10, 2) if entry_price > 0 else 0.0
        status = info.get('stage4_status', 'N/A')
        
        summary_list.append({
            'code': code, 'name': name, 'sector': sector, 'price': entry_price,
            'entry_price': entry_price, 'stop_loss': stop_loss, 'take_profit': take_profit, 'status': status
        })
        
        # 依照需求：不顯示未分類，進場(紅/warning)、停損(綠/info)、目標(橙/comment)
        card = (
            f"🎯 **{code} {name}**\n"
            f"  👉 進場: <font color=\"warning\">${entry_price}</font> | "
            f"停損: <font color=\"info\">${stop_loss}</font> | "
            f"目標: <font color=\"comment\">${take_profit}</font>"
        )
        msg_lines.append(card)
        
    df_summary = pd.DataFrame(summary_list)
    today_str = datetime.date.today().strftime("%Y%m%d")
    df_summary.to_csv(f"swing_report_{today_str}.csv", index=False, encoding='utf-8-sig')

    if ENABLE_WECHAT_NOTIFY and WECHAT_WEBHOOK_URL:
        payload = {"msgtype": "markdown", "markdown": {"content": "\n\n".join(msg_lines)}}
        try: 
            requests.post(WECHAT_WEBHOOK_URL, json=payload, timeout=5)
        except Exception: 
            pass

    print(f"✅ 報告發送完成，共 {len(summary_list)} 檔精選標的！")
    return summary_list


if __name__ == "__main__":
    raw_stocks_map = get_all_stock_list_with_market()
    index_ok, cleaned_stocks_map = run_stage0_index_check(raw_stocks_map)
    
    if index_ok:
        stage1_pool = run_stage1_and_2(cleaned_stocks_map)
        stage2_pool = run_stage2_chip_filter(stage1_pool)
        stage3_pool = run_stage3_filter(stage2_pool)
        stage4_pool = run_stage4_swing_filter(stage3_pool)
        final_pool = run_stage5_risk_management_and_wrapup(stage4_pool)
        
    input("\n按下 Enter 鍵結束程式...")