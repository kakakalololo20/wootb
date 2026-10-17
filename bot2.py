import requests, json, os, re, sys
from playwright.sync_api import sync_playwright

START_URLS = [
    "https://www.woot.com/alldeals",
]
# عدّل هذا بعد ما تنسخ الرابط، مثال: "sort=newest"  (اتركه "" لو ما عرفته)
SORT_QUERY = "selectedSort=nf"
MAX_PAGES = 40
KEYWORDS = ["SSD", "NVMe", "RTX", "GTX", "Radeon", "GPU", "graphics card"]

BOT_TOKEN = os.environ.get("BOT_TOKEN", "حط_التوكن_هنا_للتجربة_المحلية")
CHAT_ID = os.environ.get("CHAT_ID", "حط_رقمك_هنا_للتجربة_المحلية")
DB = "seen_keywords.json"

PATTERNS = [
    (k, re.compile(rf"(?<![A-Za-z]){re.escape(k)}s?(?![A-Za-z])", re.I))
    for k in KEYWORDS
]

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

def build_url(base, n):
    params = []
    if SORT_QUERY:
        params.append(SORT_QUERY)
    if n > 1:
        params.append(f"page={n}")
    return base + ("?" + "&".join(params) if params else "")

def get_products(seen):
    """seen = None في أول تشغيل (فحص كامل). غير كذا: يوقف عند أول صفحة ما فيها جديد."""
    available = {}
    all_links = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for base in START_URLS:
            for n in range(1, MAX_PAGES + 1):
                url = build_url(base, n)
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
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
                print(f"{base.split('/')[-1]} صفحة {n}: متوفر {len(ok)} | منتهي {len(new_links) - len(ok)}")
                if n == 1:
                    print("أول المنتجات (تأكد إنها الأحدث):")
                    for t in list(ok.values())[:3]:
                        print("  -", t[:80])
                # وقف مبكر: صفحة كلها قديمة = الباقي أقدم منها
                if seen is not None and all(h in seen for h in ok):
                    print("  لا جديد في هالصفحة، نوقف.")
                    break
        browser.close()
    return available

def matched(text):
    return [k for k, rx in PATTERNS if rx.search(text)]

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
current = get_products(seen)

if not current:
    print("ما جاب أي منتج، ممكن الموقع حظر الطلب. ما حفظت شي.")
    sys.exit(1)

if seen is not None:
    for link, name in current.items():
        if link not in seen:
            hits = matched(name)
            if hits:
                notify(f"🔎 {', '.join(hits)}\n{name[:200]}\n{link}")
    merged = {**seen, **current}   # ندمج لأن الفحص ما غطى كل الصفحات
else:
    merged = current

json.dump(merged, open(DB, "w", encoding="utf-8"), ensure_ascii=False)

print(f"\nجديد/مفحوص هالمرة: {len(current)} | المحفوظ كله: {len(merged)}")
print("أول تشغيل: تم الحفظ بدون تنبيه" if seen is None else "تمت المقارنة")
