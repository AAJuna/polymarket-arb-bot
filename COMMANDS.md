# Polymarket Bot — Command Reference

## Menjalankan Bot

```bash
# Jalankan di background (tetap jalan saat SSH disconnect)
nohup python3 main.py &

# Jalankan di foreground (untuk debug)
python3 main.py
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
