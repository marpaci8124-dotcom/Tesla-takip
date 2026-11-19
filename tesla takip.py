#!/usr/bin/env python3
"""
Tesla Türkiye ikinci el (Sertifikalı İkinci El) envanter takipçisi.

Her ~5 dakikada bir Tesla'nın envanter API'sini tarar; yeni bir araç
eklendiğinde veya bir aracın fiyatı düştüğünde iPhone'una bildirim gönderir.

Kurulum:
    pip install curl_cffi
    python3 tesla_takip.py

Bildirim için iPhone'a "ntfy" uygulamasını kur (App Store, ücretsiz) ve
aşağıdaki NTFY_KONU adına abone ol.
"""

import json
import random
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

try:
    from curl_cffi import requests  # Chrome gibi görünür; Tesla'nın bot korumasını aşmaya yardımcı olur
except ImportError:
    sys.exit("Önce şunu çalıştır:  pip install curl_cffi")

# ======================= AYARLAR =======================

# Bildirim kanalı: ntfy uygulamasında abone olacağın konu adı.
# Başkası tahmin edemesin diye sonuna rastgele bir şey ekle.
import os
NTFY_KONU = os.environ.get("NTFY_KONU") or "tesla-ikinciel-BURAYI-DEGISTIR-48213"

# İstersen Telegram da kullanabilirsin (boş bırakırsan kullanılmaz)
TELEGRAM_BOT_TOKEN = ""   # @BotFather'dan alınan token
TELEGRAM_CHAT_ID = ""     # kendi chat id'n

MODELLER = ["my"]          # "my" = Model Y, "m3" = Model 3 (Türkiye'de şu an Model Y var)
MAKS_FIYAT = None          # örn. 2_000_000 → sadece bu fiyatın altını bildir. None = hepsi
MAKS_KM = None             # örn. 60_000. None = hepsi

TARAMA_ARALIGI_SN = 300    # 5 dakika
DURUM_DOSYASI = Path(__file__).with_name("tesla_gorulen.json")

# =======================================================

API = "https://www.tesla.com/inventory/api/v4/inventory-results"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Referer": "https://www.tesla.com/tr_TR/inventory/used/my",
}


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def sorgu(model, offset=0):
    q = {
        "query": {
            "model": model,
            "condition": "used",
            "options": {},
            "arrangeby": "Price",
            "order": "asc",
            "market": "TR",
            "language": "tr",
            "super_region": "europe",
            "lng": 28.9784,
            "lat": 41.0082,
            "zip": "",
            "range": 0,
        },
        "offset": offset,
        "count": 50,
        "outsideOffset": 0,
        "outsideSearch": False,
    }
    return API + "?query=" + urllib.parse.quote(json.dumps(q, separators=(",", ":")))


def araclari_getir(oturum, model):
    araclar, offset = [], 0
    while True:
        r = oturum.get(sorgu(model, offset), headers=HEADERS, impersonate="chrome", timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        veri = r.json()
        sonuc = veri.get("results") or []
        if isinstance(sonuc, dict):        # Tesla boş olunca bazen {} döndürür
            sonuc = list(sonuc.values())
        araclar.extend(sonuc)
        toplam = int(veri.get("total_matches_found") or 0)
        offset += len(sonuc)
        if not sonuc or offset >= toplam:
            return araclar


def fiyat(a):
    for k in ("InventoryPrice", "PurchasePrice", "Price", "TotalPrice"):
        if a.get(k):
            try:
                return int(float(a[k]))
            except (TypeError, ValueError):
                pass
    return None


def tl(x):
    return "?" if x is None else f"{x:,.0f} TL".replace(",", ".")


def ozet(a, model):
    renk = a.get("PAINT")
    renk = ", ".join(renk) if isinstance(renk, list) else (renk or "")
    km = a.get("Odometer")
    satirlar = [
        f"{a.get('Year', '')} {a.get('TrimName') or model.upper()}".strip(),
        f"Fiyat: {tl(fiyat(a))}",
        f"KM: {km:,}".replace(",", ".") if isinstance(km, (int, float)) else None,
        f"Renk: {renk}" if renk else None,
        f"Konum: {a.get('City')}" if a.get("City") else None,
    ]
    return "\n".join(s for s in satirlar if s)


def link(a, model):
    vin = a.get("VIN")
    return f"https://www.tesla.com/tr_TR/{model}/order/{vin}" if vin else \
        f"https://www.tesla.com/tr_TR/inventory/used/{model}"


def bildir(baslik, mesaj, url=None, oncelik="high"):
    log(f"BİLDİRİM: {baslik} | {mesaj.replace(chr(10), ' | ')}")
    if NTFY_KONU:
        try:
            govde = {"topic": NTFY_KONU, "title": baslik, "message": mesaj,
                     "priority": {"urgent": 5, "high": 4, "default": 3, "low": 2}[oncelik],
                     "tags": ["red_car"]}
            if url:
                govde["click"] = url
                govde["actions"] = [{"action": "view", "label": "Aracı aç", "url": url}]
            requests.post("https://ntfy.sh/", json=govde, timeout=15)
        except Exception as e:
            log(f"ntfy hatası: {e}")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            metin = f"{baslik}\n{mesaj}" + (f"\n{url}" if url else "")
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": metin}, timeout=15)
        except Exception as e:
            log(f"Telegram hatası: {e}")


def filtreye_uyar(a):
    f, km = fiyat(a), a.get("Odometer")
    if MAKS_FIYAT and f and f > MAKS_FIYAT:
        return False
    if MAKS_KM and isinstance(km, (int, float)) and km > MAKS_KM:
        return False
    return True


def durum_oku():
    try:
        return json.loads(DURUM_DOSYASI.read_text())
    except Exception:
        return None


def durum_yaz(d):
    DURUM_DOSYASI.write_text(json.dumps(d, ensure_ascii=False, indent=1))


def tara(oturum, gorulen, ilk_calisma):
    yeni_durum = {}
    for model in MODELLER:
        for a in araclari_getir(oturum, model):
            vin = a.get("VIN")
            if not vin:
                continue
            f = fiyat(a)
            yeni_durum[vin] = f
            if ilk_calisma or not filtreye_uyar(a):
                continue
            if vin not in gorulen:
                bildir("🚗 Yeni ikinci el Tesla!", ozet(a, model), link(a, model), "urgent")
            elif f and gorulen[vin] and f < gorulen[vin]:
                bildir("💸 Fiyat düştü!",
                       f"{ozet(a, model)}\nEski: {tl(gorulen[vin])}", link(a, model))
    return yeni_durum


def tek_tarama():
    """GitHub Actions için: bir kez tara, durumu dosyaya yaz, çık."""
    d = durum_oku() or {}
    ilk = "araclar" not in d
    gorulen = d.get("araclar", {})
    hata = d.get("ardisik_hata", 0)
    try:
        gorulen = tara(requests.Session(), gorulen, ilk)
        log(f"Tarama tamam: {len(gorulen)} araç listede.")
        if ilk:
            bildir("✅ Tesla takibi başladı",
                   f"Şu an listede {len(gorulen)} ikinci el araç var. "
                   "Yeni araç gelince haber vereceğim.",
                   f"https://www.tesla.com/tr_TR/inventory/used/{MODELLER[0]}", "default")
        if hata >= 3:
            bildir("✅ Takip tekrar çalışıyor", "Tesla'ya yeniden erişilebiliyor.", oncelik="low")
        hata = 0
    except Exception as e:
        hata += 1
        log(f"Hata ({hata}): {e}")
        if hata == 3:
            bildir("⚠️ Tesla takibi hata veriyor",
                   f"Son 3 tarama başarısız: {e}", oncelik="default")
    durum_yaz({"araclar": gorulen, "ardisik_hata": hata,
               "gun": datetime.now().strftime("%Y-%m-%d")})


def main():
    oturum = requests.Session()
    d = durum_oku()
    gorulen = d.get("araclar") if d else None
    ilk = gorulen is None
    ardisik_hata, hata_bildirildi = 0, False

    log("Takip başladı. Durdurmak için Ctrl+C.")
    while True:
        try:
            yeni = tara(oturum, gorulen or {}, ilk)
            # Satılan/kaldırılan araçları da unutma: geri gelirse tekrar bildirilsin
            gorulen = yeni
            durum_yaz({"araclar": gorulen})
            log(f"Tarama tamam: {len(gorulen)} araç listede.")
            if ilk:
                bildir("✅ Tesla takibi başladı",
                       f"Şu an listede {len(gorulen)} ikinci el araç var. "
                       "Yeni araç gelince haber vereceğim.",
                       f"https://www.tesla.com/tr_TR/inventory/used/{MODELLER[0]}", "default")
                ilk = False
            if hata_bildirildi:
                bildir("✅ Takip tekrar çalışıyor", "Tesla'ya yeniden erişilebiliyor.", oncelik="low")
            ardisik_hata, hata_bildirildi = 0, False
        except Exception as e:
            ardisik_hata += 1
            log(f"Hata ({ardisik_hata}): {e}")
            if ardisik_hata >= 3 and not hata_bildirildi:
                bildir("⚠️ Tesla takibi hata veriyor",
                       f"Son 3 tarama başarısız: {e}. Bilgisayarı / interneti kontrol et.",
                       oncelik="default")
                hata_bildirildi = True
            if ardisik_hata >= 2:            # engellenmişsek yeni oturum aç
                oturum = requests.Session()
        # Tam 5 dakikada bir değil, biraz rastgele bekle (bot gibi görünmemek için)
        time.sleep(TARAMA_ARALIGI_SN + random.randint(-30, 30))


if __name__ == "__main__":
    if "--tek" in sys.argv:
        tek_tarama()
        sys.exit(0)
    try:
        main()
    except KeyboardInterrupt:
        log("Durduruldu.")
