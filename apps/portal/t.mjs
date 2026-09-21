import { chromium } from "playwright";
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 1400 } });
await page.goto("http://localhost:3000/indicators", { waitUntil: "domcontentloaded" });
await page.waitForSelector("tbody tr");
const box = page.getByPlaceholder(/Search/).first();
for (const q of ["APBD Revenue", "apbd_revenue", "Growth YoY", "by City"]) {
  await box.fill(q);
  await box.press("Enter");
  await page.waitForTimeout(800);
  const names = await page.locator("tbody tr a").allInnerTexts();
  console.log(`"${q}"`.padEnd(16), "->", names.length, names.join(", ").slice(0, 70));
}
await browser.close();
