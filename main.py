from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class ScrapeRequest(BaseModel):
    url: str
    wait_for: str = ""
    timeout: int = 45000
    extract_images: bool = True
    max_pages: int = 20  # follow pagination up to N pages

@app.get("/health")
def health():
    return {"ok": True}

async def scrape_single_page(p, url, wait_for, timeout):
    """Scrape one page and return lots + raw_text + next_page_url."""
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled"]
    )
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
        timezone_id="America/New_York"
    )
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = await context.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    await page.wait_for_timeout(3000)
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except: pass

    if wait_for:
        try: await page.wait_for_selector(wait_for, timeout=8000)
        except: pass

    await page.evaluate("""async () => {
        await new Promise(resolve => {
            let pos = 0;
            const timer = setInterval(() => {
                window.scrollBy(0, 600); pos += 600;
                if (pos >= document.body.scrollHeight) { window.scrollTo(0,0); clearInterval(timer); resolve(); }
            }, 200);
        });
    }""")
    await page.wait_for_timeout(1500)

    lots = await page.evaluate("""() => {
        const items = [];
        const sels = ['.lot-card','.catalogue-item','.lot-item','[class*="lot-row"]','[class*="LotCard"]','[class*="auction-item"]','[class*="item-card"]','.hibid-lot','.lot-listing','[class*="LotItem"]','[data-lot-id]','article'];
        for (const sel of sels) {
            const cards = document.querySelectorAll(sel);
            if (cards.length > 2) {
                cards.forEach((card,i) => {
                    const title = card.querySelector('h1,h2,h3,h4,[class*="title"],[class*="name"],[class*="description"]')?.innerText?.trim() || '';
                    const lotNum = card.querySelector('[class*="lot-num"],[class*="lot-number"]')?.innerText?.trim() || String(i+1);
                    const price = card.querySelector('[class*="price"],[class*="bid"],[class*="estimate"]')?.innerText?.trim() || '';
                    const imgEl = card.querySelector('img');
                    const img = imgEl?.dataset?.src || imgEl?.src || '';
                    if (title) items.push({lot: lotNum.replace(/[^0-9]/g,'')||String(i+1), title, image_url:img, estimate:price});
                });
                break;
            }
        }
        return items;
    }""")

    text = await page.inner_text("body")

    # Detect next page link — look for "next" button or numbered pagination
    next_url = await page.evaluate("""() => {
        // 1. Aria label "next"
        let a = document.querySelector('a[aria-label*="next" i]:not([aria-disabled="true"])');
        if (a && a.href) return a.href;
        // 2. Class containing "next"
        a = document.querySelector('a.next:not(.disabled), a[class*="Next"]:not([class*="disabled"]), .pagination a[rel="next"]');
        if (a && a.href) return a.href;
        // 3. Text "Next" or ">"
        const links = document.querySelectorAll('a');
        for (const link of links) {
            const txt = link.innerText?.trim().toLowerCase();
            if ((txt === 'next' || txt === 'next ›' || txt === '›' || txt === 'next page') && link.href && !link.classList.contains('disabled')) {
                return link.href;
            }
        }
        return null;
    }""")

    await browser.close()
    return lots, text, next_url

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    try:
        from playwright.async_api import async_playwright
        all_lots = []
        all_text = []
        seen_urls = set()
        current_url = req.url

        async with async_playwright() as p:
            for page_num in range(req.max_pages):
                if current_url in seen_urls:
                    print(f"Already scraped {current_url}, stopping")
                    break
                seen_urls.add(current_url)
                print(f"Scraping page {page_num+1}: {current_url}")
                try:
                    lots, text, next_url = await scrape_single_page(p, current_url, req.wait_for, req.timeout)
                except Exception as e:
                    print(f"Page {page_num+1} error: {e}")
                    break

                if not lots and not text.strip():
                    print(f"Empty page, stopping")
                    break

                all_lots.extend(lots)
                all_text.append(text[:8000])

                if not next_url or next_url == current_url:
                    print(f"No next page found, stopping")
                    break

                current_url = next_url

        return {
            "url": req.url,
            "lots": all_lots,
            "raw_text": "\n\n--- PAGE BREAK ---\n\n".join(all_text)[:60000],
            "lot_count": len(all_lots),
            "pages_scraped": len(seen_urls)
        }
    except Exception as e:
        raise HTTPException(500, f"Scrape error: {str(e)}")
