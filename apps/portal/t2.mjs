import { chromium } from "playwright";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

// Search by the name the table shows.
await page.goto("http://localhost:3000/indicators", { waitUntil: "domcontentloaded" });
await page.waitForSelector("tbody tr");
await page.getByPlaceholder(/Search/).first().fill("APBD Revenue");
await page.waitForTimeout(900);
console.log("search 'APBD Revenue' ->", await page.locator("tbody tr").count(), "rows");
await page.getByPlaceholder(/Search/).first().fill("apbd_revenue");
await page.waitForTimeout(900);
console.log("search 'apbd_revenue' ->", await page.locator("tbody tr").count(), "rows");

await page.goto("http://localhost:3000/indicators/apbd_expenditure_realisasi", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1600);
console.log("h1:", await page.locator("h1").innerText());
console.log("id shown:", await page.locator("h1 ~ code, code").first().innerText());
await page.screenshot({ path: "/tmp/title-detail.png", clip: { x: 340, y: 60, width: 940, height: 300 } });
await browser.close();
