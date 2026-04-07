# Polymarket Bot — Command Reference

## Menjalankan Bot (PM2)

```bash
# Start
pm2 start main.py --interpreter python3 --name polymarket-bot

# Stop
pm2 stop polymarket-bot

# Restart (setelah git pull)
pm2 restart polymarket-bot

# Reload graceful (finish siklus dulu)
pm2 reload polymarket-bot

# Status
pm2 status

# Log real-time
pm2 logs polymarket-bot

# Auto-start saat VPS reboot (jalankan sekali)
pm2 startup
pm2 save
```

---

## Dashboard (Streamlit UI)

```bash
# Install dependencies (sekali saja)
pip install streamlit pandas

# Jalankan dashboard
streamlit run dashboard.py

# Buka di browser: http://localhost:8501
# Auto-refresh setiap 10 detik
```

---

## Monitoring

```bash
# Lihat log real-time (semua aktivitas)
tail -f logs/bot.log

# Lihat trade log saja
tail -f logs/trades.log

# Lihat log ringkas (status, trade, error)
tail -50 logs/bot.log | grep -E "cycle|opportunities|AI analysis|Trade|STATUS|blocked|ERROR"

# Cek bot masih jalan
ps aux | grep main.py | grep -v grep
```

---

## Stop Bot

```bash
# Graceful stop (bot selesaikan siklus + save portfolio)
pkill -f main.py

# Force stop (hindari kalau bisa)
kill -9 $(pgrep -f main.py)
```

---

## Fresh Start

```bash
# Reset portfolio + AI stats + logs, lalu langsung start fresh
python main.py --fresh-start

# Reset portfolio + AI stats saja, simpan logs lama
python main.py --fresh-start --keep-logs
```

Atau lewat `.env`:

- `RESET_STATE_ON_START=true`
- `RESET_LOGS_ON_START=true` atau `false`

Catatan:
- Stop bot lama dulu sebelum fresh start supaya file state/log tidak sedang dipakai proses lain.
- Kalau pakai `RESET_STATE_ON_START=true`, bot akan reset setiap kali startup sampai flag itu dikembalikan ke `false`.

---

## Opportunity Report

```bash
# Inspect market yang selesai dalam 48 jam tanpa trade dan tanpa AI call
python main.py --opportunity-report --expiry-hours 48

# Batasi jumlah row output
python main.py --opportunity-report --expiry-hours 24 --report-limit 10
```

Catatan:
- Ini hanya report inspeksi. Bot tidak entry order dan tidak memanggil Claude.
- Report memakai detector opportunity yang sama dengan bot, lalu menandai jika market sudah ada di open positions.
- Gunakan ini untuk melihat apakah expiry window 1-2 hari punya opportunity tanpa mengubah prioritas trading loop utama.

---

## Update Config Tanpa Restart

```bash
# 1. Edit .env
nano .env

# 2. Kirim signal reload ke bot (langsung berlaku tanpa restart)
kill -USR1 $(pgrep -f main.py)
```

Config yang bisa diubah live:
- `MIN_AI_CONFIDENCE` — threshold confidence Claude
- `MIN_EDGE_PCT` — minimum edge untuk trade
- `BET_SIZE_PCT` — ukuran bet % dari bankroll
- `PAPER_TRADING` — toggle paper/live trading

---

## Update Kode

```bash
# Di PC lokal — commit dan push
git add .
git commit -m "fix: deskripsi perubahan"
git push

# Di VPS — pull dan restart
pkill -f main.py
git pull
nohup python3 main.py &
```

---

## Cek Portfolio

```bash
# Lihat file portfolio JSON
cat data/portfolio.json

# Lihat summary di log
tail -100 logs/bot.log | grep STATUS
```

---

## Screen (alternatif nohup)

```bash
# Buat session baru
screen -S polymarket

# Detach (bot tetap jalan)
# Tekan: Ctrl+A lalu D

# Re-attach
screen -r polymarket

# List semua session
screen -ls

# Kill session tertentu
screen -X -S polymarket quit
```

---

## Git

```bash
# Cek status perubahan
git status

# Push update ke GitHub
git add .
git commit -m "pesan commit"
git push

# Pull update dari GitHub (di VPS)
git pull
```

---

## Troubleshooting

```bash
# Bot tidak jalan — cek error terakhir
tail -20 logs/bot.log | grep ERROR

# Restart bersih
pkill -f main.py && sleep 2 && nohup python3 main.py &

# Cek penggunaan memory/CPU
top -p $(pgrep -f main.py)

# Lihat portfolio JSON
cat data/portfolio.json | python3 -m json.tool
```
