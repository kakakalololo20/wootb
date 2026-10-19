import requests, json, os, sys
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.woot.com/category/computers/computer-parts-components"
MAX_PAGES = 10
FAIL_THRESHOLD = 3          # عدد الفشلات المتتالية قبل التنبيه
BOT_TOKEN = os.environ.get("BOT_TOKEN", "حط_التوكن_هنا_للتجربة_المحلية")
CHAT_ID = os.environ.get("CHAT_ID", "حط_رقمك_هنا_للتجربة_المحلية")
DB = "seen.json"
STATUS = "status.json"

JS = r"""
els => {
  const out = {};
  for (const a of els) {
    const href = a.href.split('?')[0];
    let card = a;
    while (card.parentElement) {
      const p = card.parentElement;
      const links = new Set(
        [...p.querySelectorAll('a[href*="/offers/"]')].map(x => x.href.split('?')[0])
      );
      if (links.size > 1) break;
      card = p;
    }
    const text = card.innerText.replace(/\s+/g, ' ').trim();
    const sold = /sold\s*out/i.test(text);
    if (!out[href]) {
      out[href] = {text: text, sold: sold};
    } else {
      out[href].sold = out[href].sold || sold;
      if (text.length > out[href].text.length) out[href].text = text;
    }
  }
  return out;
}
"""

def get_products():
    products = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for n in range(1, MAX_PAGES + 1):
            url = BASE_URL if n == 1 else f"{BASE_URL}?page={n}"
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_selector('a[href*="/offers/"]', timeout=10000)
            except Exception:
                break
            data = page.eval_on_selector_all('a[href*="/offers/"]', JS)
            new_links = [h for h in data if h not in products]
            if not new_links:
                break
            for h in new_links:
                products[h] = {"name": data[h]["text"], "available": not data[h]["sold"]}
            avail = sum(1 for h in new_links if products[h]["available"])
            print(f"صفحة {n}: متوفر {avail} | منتهي {len(new_links) - avail}")
        browser.close()
    return products

def notify(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=20,
        )
        print("تيليجرام:", r.status_code)
    except Exception as e:
        print("فشل إرسال تيليجرام:", e)

def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError:
        return None

def save_json(path, data):
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)

# ---------- التشغيل ----------
raw = load_json(DB)
seen = None if raw is None else {
    h: (v if isinstance(v, dict) else {"name": v, "available": True})
    for h, v in raw.items()
}
status = load_json(STATUS) or {"fails": 0, "alerted": False}

try:
    current = get_products()
except Exception as e:
    print("خطأ أثناء الجلب:", e)
    current = {}

# ---------- حالة فشل ----------
if not current:
    status["fails"] += 1
    print(f"فشل الجلب ({status['fails']} متتالية). ما حفظت منتجات.")
    if status["fails"] >= FAIL_THRESHOLD and not status["alerted"]:
        notify(
            f"⚠️ البوت متعطل: فشل {status['fails']} مرات ورا بعض.\n"
            "غالباً الموقع حظر الطلب أو غيّر شكل الصفحة. شوف تبويب Actions في GitHub."
        )
        status["alerted"] = True
    save_json(STATUS, status)
    sys.exit(1)

# ---------- حالة نجاح ----------
if status["alerted"]:
    notify("✅ البوت رجع يشتغل طبيعي.")
status = {"fails": 0, "alerted": False}
save_json(STATUS, status)

new_count = restock_count = 0
if seen is not None:
    for link, item in current.items():
        if not item["available"]:
            continue
        old = seen.get(link)
        if old is None:
            notify(f"🆕 {item['name'][:150]}\n{link}")
            new_count += 1
        elif not old["available"]:
            notify(f"🔄 رجع للستوك: {item['name'][:150]}\n{link}")
            restock_count += 1

merged = {**(seen or {}), **current}
save_json(DB, merged)

print(f"\nمفحوص: {len(current)} | جديد: {new_count} | رجع للستوك: {restock_count}")
print("أول تشغيل: تم الحفظ بدون تنبيه" if seen is None else "تمت المقارنة")
