import requests, json, os, sys
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.woot.com/category/computers/computer-parts-components"
MAX_PAGES = 10
BOT_TOKEN = os.environ.get("BOT_TOKEN", "حط_التوكن_هنا_للتجربة_المحلية")
CHAT_ID = os.environ.get("CHAT_ID", "حط_رقمك_هنا_للتجربة_المحلية")
DB = "seen.json"

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
    available = {}
    all_links = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for n in range(1, MAX_PAGES + 1):
            url = BASE_URL if n == 1 else f"{BASE_URL}?page={n}"
            page.goto(url, wait_until="networkidle", timeout=60000)
            try:
                page.wait_for_selector('a[href*="/offers/"]', timeout=10000)
            except Exception:
                break
            data = page.eval_on_selector_all('a[href*="/offers/"]', JS)
            new_links = [h for h in data if h not in all_links]
            if not new_links:
                break
            all_links.update(new_links)
            ok = {h: data[h]["text"] for h in new_links if not data[h]["sold"]}
            available.update(ok)
            print(f"صفحة {n}: متوفر {len(ok)} | منتهي {len(new_links) - len(ok)}")
        browser.close()
    return available

def notify(text):
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
    )
    print("تيليجرام:", r.status_code)

def load_seen():
    if not os.path.exists(DB):
        return None
    try:
        return json.load(open(DB, encoding="utf-8"))
    except json.JSONDecodeError:
        return None

seen = load_seen()
current = get_products()

if not current:
    print("ما جاب أي منتج، ممكن الموقع حظر الطلب. ما حفظت شي.")
    sys.exit(1)

if seen is not None:
    for link, name in current.items():
        if link not in seen:
            notify(f"🆕 {name[:150]}\n{link}")

json.dump(current, open(DB, "w", encoding="utf-8"), ensure_ascii=False)

print(f"\nتم جلب {len(current)} منتج متوفر")
print("أول تشغيل: تم الحفظ بدون تنبيه" if seen is None else "تمت المقارنة")