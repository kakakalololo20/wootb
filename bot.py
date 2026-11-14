import os
import sys
import json
import uuid
import subprocess
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

# ---------- الإعدادات ----------
BASE_URL = "https://www.woot.com/category/computers/computer-parts-components"
MAX_PAGES = 10
FAIL_THRESHOLD = 3

ROOT = Path(__file__).resolve().parent
DB = ROOT / "seen.json"

CHANNEL = os.getenv("BOT_CHANNEL", "server").lower()
if CHANNEL not in ("server", "github"):
    raise ValueError("BOT_CHANNEL يجب أن يكون server أو github")

STATUS = ROOT / ("status.json" if CHANNEL == "server" else "status_github.json")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

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
      if (text.length > out[href].text.length) {
        out[href].text = text;
      }
    }
  }
  return out;
}
"""


# ---------- أدوات عامة ----------
def now_riyadh():
    return datetime.now(ZoneInfo("Asia/Riyadh"))


def timestamp():
    return now_riyadh().isoformat(timespec="seconds")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp, path)


def git(*args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check
    )


def notify(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("تحذير: بيانات Telegram غير موجودة.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=20,
        )

        result = response.json()
        if response.ok and result.get("ok") is True:
            print("تم إرسال Telegram بنجاح.")
            return True

        print("فشل Telegram:", response.status_code, result)
    except Exception as e:
        print("فشل إرسال Telegram:", e)

    return False


# ---------- قاعدة المنتجات المركزية ----------
def read_remote_db():
    result = git("show", "origin/main:seen.json", check=False)

    if result.returncode != 0:
        raise RuntimeError(
            "قاعدة seen.json غير موجودة على GitHub. "
            "يجب رفع قاعدة السيرفر الأولية قبل تشغيل البوت."
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"قاعدة GitHub غير صالحة: {e}")

    if not isinstance(data, dict):
        raise RuntimeError("تنسيق قاعدة المنتجات غير صحيح.")

    return data


def normalize_item(item):
    if isinstance(item, dict):
        return {
            **item,
            "name": item.get("name", "Unknown"),
            "available": bool(item.get("available", True)),
        }

    return {"name": str(item), "available": True}


def merge_products(remote, current, checked_at):
    merged = {
        link: normalize_item(item)
        for link, item in remote.items()
    }

    for link, item in current.items():
        incoming = {
            "name": item["name"],
            "available": item["available"],
            "checked_at": checked_at,
        }

        old = merged.get(link)

        if old is not None:
            old_time = old.get("checked_at")

            # احتفظ بنتيجة الفحص الأحدث عند حدوث تزامن.
            if old_time and old_time > checked_at:
                continue

        merged[link] = incoming

    return merged


# ---------- جلب المنتجات ----------
def get_products():
    products = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            page = browser.new_page()

            for n in range(1, MAX_PAGES + 1):
                url = BASE_URL if n == 1 else f"{BASE_URL}?page={n}"

                response = page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=60000
                )

                if response is None or not response.ok:
                    code = response.status if response else "No response"
                    raise RuntimeError(f"فشل تحميل الصفحة {n}: {code}")

                try:
                    page.wait_for_selector(
                        'a[href*="/offers/"]',
                        timeout=10000
                    )
                except Exception:
                    body = page.locator("body").inner_text().lower()

                    blocked_words = [
                        "captcha",
                        "access denied",
                        "verify you are human",
                        "robot or human",
                    ]

                    if any(word in body for word in blocked_words):
                        raise RuntimeError(
                            f"احتمال وجود حظر أو اختبار تحقق في الصفحة {n}"
                        )

                    if n == 1:
                        raise RuntimeError(
                            "لم يتم العثور على منتجات في الصفحة الأولى."
                        )

                    # لا توجد منتجات إضافية في الصفحة التالية.
                    break

                data = page.eval_on_selector_all(
                    'a[href*="/offers/"]',
                    JS
                )

                new_links = [h for h in data if h not in products]

                if not new_links:
                    break

                for link in new_links:
                    products[link] = {
                        "name": data[link]["text"],
                        "available": not data[link]["sold"],
                    }

                available = sum(
                    1 for h in new_links if products[h]["available"]
                )

                print(
                    f"صفحة {n}: متوفر {available} | "
                    f"منتهي {len(new_links) - available}"
                )

        finally:
            browser.close()

    if not products:
        raise RuntimeError("الفحص لم يُرجع أي منتجات.")

    return products


# ---------- قراءة سجل البيئة من GitHub ----------
def read_remote_history(filename):
    result = git("show", f"origin/main:{filename}", check=False)

    if result.returncode != 0:
        if "does not exist in 'origin/main'" in result.stderr:
            return []
        raise RuntimeError(
            f"تعذر قراءة سجل GitHub {filename}: {result.stderr}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ملف السجل {filename} غير صالح: {e}")

    if not isinstance(data, list):
        raise RuntimeError(f"تنسيق ملف السجل {filename} غير صحيح.")

    return [
        {
            "checked_at": item["checked_at"],
            "channel": item["channel"],
        }
        for item in data
        if isinstance(item, dict)
        and "checked_at" in item
        and "channel" in item
    ]


# ---------- نشر النتائج إلى GitHub ----------
def publish_results(current, checked_at):
    history_file = ROOT / f"check_history_{CHANNEL}.json"

    history_record = {
        "checked_at": checked_at,
        "channel": CHANNEL,
    }

    git("config", "user.name", "Woot Monitor Bot")
    git("config", "user.email", "woot-monitor@users.noreply.github.com")

    for attempt in range(1, 6):
        print(f"محاولة نشر قاعدة المنتجات {attempt}/5")

        git("fetch", "origin", "main")

        remote = read_remote_db()
        merged = merge_products(remote, current, checked_at)

        remote_history = read_remote_history(history_file.name)

        already_recorded = any(
            item["checked_at"] == checked_at
            and item["channel"] == CHANNEL
            for item in remote_history
        )

        if already_recorded:
            merged_history = remote_history
        else:
            merged_history = [history_record] + remote_history

        save_json(DB, merged)
        save_json(history_file, merged_history)

        git("add", "seen.json", history_file.name)

        if CHANNEL == "github" and STATUS.exists():
            git("add", "status_github.json")

        staged = git("diff", "--cached", "--quiet", check=False)

        if staged.returncode == 0:
            print("لا توجد تغييرات جديدة للنشر.")
            return merged

        commit = git(
            "commit",
            "-m",
            f"Update Woot database ({CHANNEL})",
            check=False
        )

        if commit.returncode != 0:
            raise RuntimeError(
                "فشل إنشاء commit:\n" + commit.stderr
            )

        pushed = git(
            "push",
            "origin",
            "HEAD:main",
            check=False
        )

        if pushed.returncode == 0:
            print("تم تحديث قاعدة GitHub وسجل الفحص.")
            return merged

        print("تعذر الرفع، سنجلب آخر تحديث ونعيد دمج النتائج.")
        print(pushed.stderr)

        git("fetch", "origin", "main")
        git("reset", "--mixed", "origin/main")

    raise RuntimeError("فشل نشر التحديث بعد 5 محاولات.")


# ---------- التشغيل الرئيسي ----------
def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("BOT_TOKEN أو CHAT_ID غير مضبوط.")

    # احصل على قاعدة المنتجات المركزية قبل الفحص.
    git("fetch", "origin", "main")
    seen = read_remote_db()

    status = load_json(STATUS) or {
        "fails": 0,
        "alerted": False
    }

    try:
        current = get_products()

    except Exception as e:
        print("خطأ أثناء الفحص:", e)

        status["fails"] = status.get("fails", 0) + 1

        print(
            f"فشل الفحص ({status['fails']} "
            f"مرات متتالية). لم يتم تحديث قاعدة المنتجات."
        )

        if (
            status["fails"] >= FAIL_THRESHOLD
            and not status.get("alerted", False)
        ):
            notify(
                f"⚠️ بوت Woot ({CHANNEL}) متعطل: "
                f"فشل {status['fails']} مرات متتالية."
            )
            status["alerted"] = True

        save_json(STATUS, status)
        return 1

    # ---------- نجاح الفحص ----------
    if status.get("alerted"):
        notify(f"✅ بوت Woot ({CHANNEL}) عاد للعمل.")

    status = {"fails": 0, "alerted": False}
    save_json(STATUS, status)

    new_count = 0
    restock_count = 0

    for link, item in current.items():
        if not item["available"]:
            continue

        old = seen.get(link)

        if old is None:
            notify(f"🆕 {item['name'][:150]}\n{link}")
            new_count += 1

        elif not normalize_item(old)["available"]:
            notify(f"🔄 رجع للستوك: {item['name'][:150]}\n{link}")
            restock_count += 1

    checked_at = timestamp()

    publish_results(current, checked_at)

    print("\n========== ملخص الفحص ==========")
    print("القناة:", CHANNEL)
    print("الوقت:", checked_at)
    print("المنتجات المفحوصة:", len(current))
    print("المنتجات الجديدة:", new_count)
    print("المنتجات العائدة للمخزون:", restock_count)
    print("================================")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("خطأ تشغيلي:", e)
        sys.exit(1)
