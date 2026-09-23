"""
BTC Absorption Long Signal Monitor
===================================
He thong: chi tin hieu Long, dua tren hap thu cau (demand absorption) trong 1H,
da duoc backtest tren 5 thang du lieu that (T4-T8/2026), BTCUSDT spot Binance.

CACH CHAY:
    pip install requests
    python btc_absorption_monitor.py

Script se:
  1. Lay 1H klines gan nhat tu Binance (public API, khong can API key).
  2. Tinh delta_ratio (mua chu dong - ban chu dong) va bien dong gia moi gio.
  3. Kiem tra 3 dieu kien tin hieu (nguong da CO DINH tu backtest, khong tinh lai
     theo du lieu moi de tranh loi "nhin truoc" da phat hien khi test).
  4. Neu du dieu kien -> in ra tin hieu Long voi entry/stop/target cu the.
  5. Co the chay lap lai moi gio (vong lap while True) hoac chay 1 lan roi thoat.

LUU Y QUAN TRONG:
  - Day la CONG CU HO TRO, khong phai lenh tu dong dat vao san. Ban van phai
    tu tay dat lenh/stop/target tren san giao dich.
  - Cac nguong duoi day duoc "dong bang" tu ket qua backtest T4-T8/2026. Neu
    muon test lai/toi uu them, phai lam tren du lieu MOI (khong dung lai du
    lieu da dung de tinh nguong nay) de tranh nhin-truoc.
  - Chua tinh phi giao dich/truot gia trong tin hieu hien thi - ban tu cong
    them khi quyet dinh vao lenh.
"""

import os
import time
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import urlencode

# ============ CAU HINH (nguong DA CO DINH tu backtest T4-T8/2026) ============
SYMBOL = "BTCUSDT"
INTERVAL = "1h"

PRESSURE_THRESH = 0.194012      # |delta_ratio| >= nguong nay moi coi la "ap luc manh"
MOVE_THRESH = 0.001512          # |price_move_pct| <= nguong nay moi coi la "gia dung yen"
STRENGTH_CUT = 0.296896         # |delta_ratio| >= nguong nay moi coi la "tin hieu du manh"
CRASH_LOOKBACK_H = 6            # so gio nhin lai de kiem tra co dang sap khong
CRASH_TH = -0.01                # bo qua neu gia da giam >= 1% trong 6h truoc

STOP_LOOKBACK_H = 12            # dat stop duoi day thap nhat cua N gio gan nhat
STOP_BUFFER_PCT = 0.15          # dem them duoi day (%)
RR = 3.0                        # ty le target/risk

KLINES_NEEDED = 40              # so nen 1H can tai ve (>= STOP_LOOKBACK_H + du lieu tinh toan)

# ============ THONG BAO TELEGRAM (tuy chon) ============
# De nhan thong bao qua dien thoai khi co tin hieu:
#   1. Mo Telegram, tim "BotFather" -> go /newbot -> dat ten -> se duoc 1 TOKEN dang
#      "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#   2. Nhan tin BAT KY cho bot vua tao (vi du go "hi") de bot "biet" ban.
#   3. Mo trinh duyet, vao: https://api.telegram.org/bot<TOKEN>/getUpdates
#      (thay <TOKEN> bang token that) -> tim so "id" trong "chat":{"id": ...} -> do la CHAT_ID.
#   4. Dien TOKEN va CHAT_ID vao 2 dong duoi day. De trong ("") neu khong dung Telegram.
# Neu chay tren GitHub Actions, dien vao Secrets (khong dien thang vao day) -
# script se tu doc tu bien moi truong. Chay tren may ca nhan thi dien thang vao 2 dong duoi.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")   # vi du: "123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")       # vi du: "987654321"

# ============ THONG BAO QUA NTFY (khong can dang ky, don gian hon Telegram) ============
# Cach dung:
#   1. Tai app "ntfy" tren App Store (mien phi, khong can tai khoan).
#   2. Mo app -> bam "+" -> nhap 1 TEN CHU DE bat ky, cang la (kho doan) cang tot,
#      vi du: "btc-signal-nguyenvana-8823" -> bam Subscribe.
#   3. Dien dung TEN CHU DE do vao NTFY_TOPIC ben duoi (hoac dat lam bien moi truong
#      NTFY_TOPIC neu chay tren GitHub Actions).
# Luu y: ai biet ten chu de nay cung xem duoc thong bao, nen dat ten kho doan.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")   # vi du: "btc-signal-nguyenvana-8823"


def send_telegram(text):
    """Gui tin nhan qua Telegram bot. Bo qua neu chua dien TOKEN/CHAT_ID."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    try:
        req = Request(url, data=data)
        with urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as e:
        print(f"[Loi gui Telegram] {e}")


def send_ntfy(text, title="Tin hieu BTC Long"):
    """Gui thong bao qua ntfy.sh. Bo qua neu chua dien NTFY_TOPIC."""
    if not NTFY_TOPIC:
        return
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    try:
        req = Request(url, data=text.encode("utf-8"), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", "high")
        with urlopen(req, timeout=15) as resp:
            resp.read()
    except Exception as e:
        print(f"[Loi gui ntfy] {e}")


# ============ THONG BAO QUA EMAIL (dung Gmail, khong can Telegram) ============
# Cach dung:
#   1. Bat "2-Step Verification" cho tai khoan Gmail cua ban (Google Account ->
#      Security -> 2-Step Verification -> bat len). Bat buoc phai bat cai nay truoc.
#   2. Vao https://myaccount.google.com/apppasswords -> tao 1 "App password" moi
#      (dat ten bat ky, vi du "btc-signal") -> Google se dua 1 ma gom 16 ky tu,
#      dang "abcd efgh ijkl mnop". Copy ma nay (bo dau cach di cung duoc).
#   3. Dien vao GMAIL_ADDRESS (email Gmail cua ban) va GMAIL_APP_PASSWORD (ma 16
#      ky tu vua tao) ben duoi. KHONG dung mat khau Gmail thuong - phai dung App
#      Password rieng nay.
#   4. NOTIFY_EMAIL_TO la email se nhan thong bao (co the giong GMAIL_ADDRESS).
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")            # vi du: "banthan@gmail.com"
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")  # ma 16 ky tu tu buoc 2
NOTIFY_EMAIL_TO = os.environ.get("NOTIFY_EMAIL_TO", "")        # email nhan thong bao


def send_email(text, subject="Tin hieu Long BTC"):
    """Gui email qua Gmail SMTP. Bo qua neu chua dien du 3 thong tin ben tren."""
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD and NOTIFY_EMAIL_TO):
        return
    msg = MIMEText(text, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = NOTIFY_EMAIL_TO
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [NOTIFY_EMAIL_TO], msg.as_string())
    except Exception as e:
        print(f"[Loi gui email] {e}")


def notify(text):
    """Gui thong bao qua tat ca cac kenh da cau hinh (Telegram / ntfy / email)."""
    send_telegram(text)
    send_ntfy(text)
    send_email(text)

# ============ HAM LAY DU LIEU TU BINANCE ============

# Danh sach cac dia chi API du phong - thu lan luot neu cai truoc bi chan/loi.
# data-api.binance.vision la dia chi rieng cho du lieu thi truong cong khai,
# it bi chan theo vung hon so voi api.binance.com (hay gap loi 451 tren may
# chu GitHub Actions vi IP thuoc vung bi Binance han che).
BINANCE_BASE_URLS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
    "https://api-gcp.binance.com",
]


def _raw_looks_valid(raw):
    """Kiem tra so bo du lieu tra ve co hop le khong. Mot so dia chi API
    du phong co the tra ve du lieu "khong day du" (vi du cot taker-buy-volume
    luon la 0), khien delta_ratio luon tinh ra -1.000 mot cach GIA TAO chu
    khong phai thi truong that su one-sided. Neu phat hien nhieu nen lien
    tiep co taker_buy_quote_volume = 0 (hoac = quote_volume), coi la du lieu
    kha nghi va thu dia chi API khac."""
    if not raw or len(raw) < 5:
        return False
    suspicious = 0
    checked = 0
    for row in raw[-10:]:  # kiem tra 10 nen gan nhat
        quote_volume = float(row[7])
        taker_buy_quote_volume = float(row[9])
        if quote_volume <= 0:
            continue
        checked += 1
        # nghi ngo neu taker-buy = 0 het hoac = toan bo volume het (delta_ratio = +-1.000 chinh xac)
        if taker_buy_quote_volume <= 0 or abs(taker_buy_quote_volume - quote_volume) < 1e-9:
            suspicious += 1
    if checked == 0:
        return False
    # neu qua nua so nen kiem tra deu co dau hieu du lieu gia/thieu -> khong hop le
    return (suspicious / checked) < 0.5


def fetch_klines(symbol=SYMBOL, interval=INTERVAL, limit=KLINES_NEEDED):
    """Lay du lieu 1H gan nhat tu Binance public API (khong can API key).
    Moi nen tra ve: open, high, low, close, volume, taker_buy_quote_volume, quote_volume.
    Thu lan luot nhieu dia chi API neu cai truoc bi loi/bi chan (HTTP 451...) HOAC
    tra ve du lieu kha nghi (xem _raw_looks_valid).
    """
    qs = urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    last_err = None
    raw = None
    tried_invalid = []
    for base in BINANCE_BASE_URLS:
        url = f"{base}/api/v3/klines?{qs}"
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=15) as resp:
                candidate = json.loads(resp.read().decode())
            if not _raw_looks_valid(candidate):
                tried_invalid.append(base)
                last_err = f"Du lieu tu {base} kha nghi (taker-buy-volume bat thuong)"
                continue
            raw = candidate
            break
        except Exception as e:
            last_err = e
            continue
    if raw is None:
        extra = f" (da thu nhung du lieu kha nghi tu: {tried_invalid})" if tried_invalid else ""
        raise RuntimeError(f"Khong lay duoc du lieu HOP LE tu bat ky dia chi Binance nao.{extra} Loi cuoi: {last_err}")

    klines = []
    for row in raw:
        open_time_ms = row[0]
        o, h, l, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        quote_volume = float(row[7])              # tong gia tri giao dich (USD) trong nen
        taker_buy_quote_volume = float(row[9])     # gia tri mua chu dong (USD)
        klines.append({
            "time": datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
            "open": o, "high": h, "low": l, "close": c,
            "total_value": quote_volume,
            "buy_value": taker_buy_quote_volume,
            "sell_value": quote_volume - taker_buy_quote_volume,
        })
    return klines


# ============ LOGIC TIN HIEU ============

def analyze(klines):
    """Kiem tra nen VUA DONG GAN NHAT (klines[-2], vi klines[-1] la nen dang chay
    chua dong) co phai la tin hieu Long hop le khong."""
    if len(klines) < STOP_LOOKBACK_H + 2:
        return None

    # nen vua dong = phan tu ap chot (nen cuoi cung thuong la nen dang chay, chua dong)
    signal_bar = klines[-2]
    idx = len(klines) - 2

    total = signal_bar["total_value"]
    if total <= 0:
        return None
    delta_ratio = (signal_bar["buy_value"] - signal_bar["sell_value"]) / total
    price_move_pct = (signal_bar["close"] - signal_bar["open"]) / signal_bar["open"]

    # Lop bao ve thu 2 (phong khi buoc kiem tra du lieu o fetch_klines bi lot):
    # delta_ratio dung tuyet doi 1.000 (100% mot chieu, 0% ben con lai) gan nhu
    # khong xay ra tu nhien voi cap co thanh khoan cao nhu BTCUSDT - rat co the
    # la du lieu bi thieu/loi (vi du taker-buy-volume tra ve = 0) chu khong phai
    # thi truong that su one-sided. Bo qua tin hieu nay va bao ro ly do.
    if abs(abs(delta_ratio) - 1.0) < 1e-9:
        return {"signal": False,
                "reason": "NGHI NGO LOI DU LIEU: delta_ratio dung tuyet doi 1.000 (bat thuong) - bo qua tin hieu nay de an toan",
                "delta_ratio": delta_ratio, "price_move_pct": price_move_pct}

    # dieu kien 1+2: ap luc ban manh (delta_ratio am, vuot nguong) VA gia dung yen/khong giam theo
    is_pressure = abs(delta_ratio) >= PRESSURE_THRESH
    is_sell_pressure = delta_ratio < 0
    small_move = abs(price_move_pct) <= MOVE_THRESH
    opposite = (price_move_pct >= 0) if delta_ratio < 0 else False
    is_absorption = is_pressure and is_sell_pressure and (small_move or opposite)

    if not is_absorption:
        return {"signal": False, "reason": "Khong co tin hieu hap thu cau gio nay",
                "delta_ratio": delta_ratio, "price_move_pct": price_move_pct}

    # dieu kien 3: du manh
    if abs(delta_ratio) < STRENGTH_CUT:
        return {"signal": False, "reason": "Co hap thu nhung CHUA DU MANH (bo qua)",
                "delta_ratio": delta_ratio, "price_move_pct": price_move_pct}

    # dieu kien 4: khong dang sap nhanh (gia 6h truoc so voi bay gio)
    if idx - CRASH_LOOKBACK_H < 0:
        return {"signal": False, "reason": "Chua du du lieu de kiem tra crash filter"}
    ref_close = klines[idx - CRASH_LOOKBACK_H]["close"]
    recent_drop_pct = signal_bar["close"] / ref_close - 1
    if recent_drop_pct < CRASH_TH:
        return {"signal": False, "reason": f"Dang SAP NHANH ({recent_drop_pct*100:.2f}% trong {CRASH_LOOKBACK_H}h) - bo qua de tranh bat dao roi",
                "delta_ratio": delta_ratio, "price_move_pct": price_move_pct}

    # DU DIEU KIEN -> tinh entry / stop / target
    entry = signal_bar["close"]
    lookback_bars = klines[max(0, idx - STOP_LOOKBACK_H + 1): idx + 1]
    swing_low = min(b["low"] for b in lookback_bars)
    stop = swing_low * (1 - STOP_BUFFER_PCT / 100)
    risk = entry - stop
    if risk <= 0:
        return {"signal": False, "reason": "Risk <= 0, du lieu bat thuong"}
    target = entry + risk * RR

    return {
        "signal": True,
        "hour": signal_bar["time"],
        "delta_ratio": delta_ratio,
        "price_move_pct": price_move_pct,
        "recent_drop_pct": recent_drop_pct,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_pct": risk / entry * 100,
        "rr": RR,
    }


def format_signal(result):
    if result is None:
        return "Chua du du lieu de phan tich."
    if not result.get("signal"):
        return f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] Khong co tin hieu. {result.get('reason','')} (delta_ratio={result.get('delta_ratio', float('nan')):.3f})"

    lines = [
        "=" * 50,
        f"  TIN HIEU LONG - {SYMBOL} - gio {result['hour'].strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 50,
        f"  Entry (gia dong cua gio tin hieu): {result['entry']:,.2f}",
        f"  Stop loss:                          {result['stop']:,.2f}  (risk {result['risk_pct']:.2f}%)",
        f"  Target (R:R 1:{result['rr']:.1f}):          {result['target']:,.2f}",
        f"  delta_ratio: {result['delta_ratio']:.3f} | bien dong gio tin hieu: {result['price_move_pct']*100:.3f}%",
        "-" * 50,
        "  LUU Y: day la ban chua tru phi/truot gia. Tu dat lenh tren san,",
        "  dat san stop loss + take profit ngay khi vao lenh.",
        "=" * 50,
    ]
    return "\n".join(lines)


def run_once():
    klines = fetch_klines()
    result = analyze(klines)
    text = format_signal(result)
    print(text)
    if result and result.get("signal"):
        notify(text)
    return result


def run_loop(check_interval_sec=300):
    """Chay lien tuc, kiem tra moi 5 phut xem nen 1H moi nhat da dong chua va co tin hieu khong."""
    last_checked_hour = None
    print(f"Bat dau theo doi {SYMBOL}... (Ctrl+C de dung)")
    channels = []
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID: channels.append("Telegram")
    if NTFY_TOPIC: channels.append("ntfy")
    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD and NOTIFY_EMAIL_TO: channels.append("Email")
    print(f"Kenh thong bao da bat: {', '.join(channels) if channels else '(chua bat kenh nao - chi in ra man hinh)'}")
    while True:
        try:
            klines = fetch_klines()
            latest_closed_hour = klines[-2]["time"]
            if latest_closed_hour != last_checked_hour:
                result = analyze(klines)
                text = format_signal(result)
                print(text)
                if result and result.get("signal"):
                    notify(text)
                last_checked_hour = latest_closed_hour
        except Exception as e:
            print(f"[Loi] {e}")
        time.sleep(check_interval_sec)


if __name__ == "__main__":
    import sys
    if "--loop" in sys.argv:
        run_loop()
    else:
        run_once()
