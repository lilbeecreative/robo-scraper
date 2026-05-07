from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import base64

app = FastAPI()

class ScrapeRequest(BaseModel):
    url: str
    wait_for: str = ""
    timeout: int = 45000
    screenshot: bool = False
    extract_images: bool = True

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/scrape")
async def scrape(req: ScrapeRequest):
    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import stealth_async
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"macOS"'
                }
            )
            page = await context.new_page()
            await stealth_async(page)

            await page.goto(req.url, wait_until="domcontentloaded", timeout=req.timeout)
            # Wait for Cloudflare check to clear
            await page.wait_for_timeout(5000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except:
                pass

            if req.wait_for:
                try:
                    await page.wait_for_selector(req.wait_for, timeout=10000)
                except:
                    pass

            # Scroll to load lazy content
            await page.evaluate("""async () => {
                await new Promise(resolve => {
                    let pos = 0;
                    const timer = setInterval(() => {
                        window.scrollBy(0, 600);
                        pos += 600;
                        if (pos >= document.body.scrollHeight) {
                            window.scrollTo(0, 0);
                            clearInterval(timer);
                            resolve();
                        }
                    }, 200);
                });
            }""")
            await page.wait_for_timeout(2000)

            lots = await page.evaluate("""() => {
                const items = [];
                const selectors = [
                    '.lot-card', '.catalogue-item', '.lot-item',
                    '[class*="lot-row"]', '[class*="LotCard"]',
                    '[class*="auction-item"]', '[class*="item-card"]',
                    '.hibid-lot', '.lot-listing', '[class*="LotItem"]',
                    '[class*="lot_item"]', 'article', '[data-lot-id]'
                ];
                for (const sel of selectors) {
                    const cards = document.querySelectorAll(sel);
                    if (cards.length > 2) {
                        cards.forEach((card, i) => {
                            const title = card.querySelector('h1,h2,h3,h4,[class*="title"],[class*="name"],[class*="description"]')?.innerText?.trim() || '';
                            const lotNum = card.querySelector('[class*="lot-num"],[class*="lot-number"],[class*="lotNum"]')?.innerText?.trim() || String(i+1);
                            const price = card.querySelector('[class*="price"],[class*="estimate"],[class*="bid"],[class*="amount"]')?.innerText?.trim() || '';
                            const imgEl = card.querySelector('img');
                            const img = imgEl?.dataset?.src || imgEl?.dataset?.lazySrc || imgEl?.src || '';
                            if (title) items.push({
                                lot: lotNum.replace(/[^0-9]/g, '') || String(i+1),
                                title, image_url: img, estimate: price
                            });
                        });
                        break;
                    }
                }
                return items;
            }""")

            text = await page.inner_text("body")
            await browser.close()
            return {
                "url": req.url,
                "lots": lots,
                "raw_text": text[:20000],
                "lot_count": len(lots)
            }
    except Exception as e:
        raise HTTPException(500, f"Scrape error: {str(e)}")
