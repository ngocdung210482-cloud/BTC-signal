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
    last_checked_hour = None
    print(f"Bat dau theo doi {SYMBOL}... (Ctrl+C de dung)")
    channels = []
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID: channels.append("Telegram")
    if NTFY_TOPIC: channels.append("ntfy")
    if GMAIL_ADDRESS and GMAIL_APP_PASSWORD and NOTIFY_EMAIL_TO: channels.append("Email")
    print(f"Kenh thong bao da bat: {', '.join(channels) if channels else '(chua bat kenh nao)'}")
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
