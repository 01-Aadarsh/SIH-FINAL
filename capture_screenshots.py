#!/usr/bin/env python3
"""
Capture screenshots of IP-SAKTI Sahayak frontend for PPT presentation
"""

import asyncio
import time
from pathlib import Path
from playwright.async_api import async_playwright

async def capture_screenshots():
    """Capture and save screenshots of all three pages"""

    output_dir = Path("C:/Users/aps01/Downloads/iam/sih-2026/PPT_Screenshots")
    output_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1024, "height": 768})

        try:
            # Navigate to the app
            print("Navigating to IP-SAKTI Sahayak...")
            await page.goto("http://localhost:3000", wait_until="networkidle")
            await page.wait_for_load_state("networkidle")

            # Wait for setup page to load
            await page.wait_for_selector("text=Jurisdiction", timeout=5000)

            print("Step 1: Selecting jurisdiction and category...")
            # Click India
            await page.click("text=India")
            await page.wait_for_timeout(500)

            # Click Classical medicine
            await page.click("text=Classical medicine")
            await page.wait_for_timeout(500)

            # Wait for setup progress to reach 100%
            await page.wait_for_timeout(1000)

            print("Step 2: Clicking 'Start asking questions'...")
            # Click "Start asking questions" button
            await page.click("button:has-text('Start asking questions')")
            await page.wait_for_timeout(2000)

            # PAGE 1: Chat Interface (Landing)
            print("Step 3: Capturing PAGE 1 - Chat Interface...")
            await page.screenshot(path=str(output_dir / "PAGE_1_Chat_Interface.png"), full_page=False)
            print(f"  ✓ Saved: PAGE_1_Chat_Interface.png")

            # Now submit the question
            print("Step 4: Submitting question...")
            await page.fill("input[placeholder*='Ask a question']", "What does Section 3(p) say about traditional knowledge?")
            await page.click("button:has-text('Send')")

            # Wait for response
            await page.wait_for_timeout(6000)

            # PAGE 2: Answer + Citations
            print("Step 5: Capturing PAGE 2 - Answer + Citations...")
            # Scroll to show disclaimer and confidence score
            await page.evaluate("window.scrollBy(0, 200)")
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(output_dir / "PAGE_2_Answer_Citations.png"), full_page=False)
            print(f"  ✓ Saved: PAGE_2_Answer_Citations.png")

            # Click "View source PDF" for the first citation
            print("Step 6: Opening PDF viewer...")
            links = await page.query_selector_all("text=View source PDF")
            if links:
                await links[0].click()
                await page.wait_for_timeout(2000)

            # PAGE 3: Source PDF Viewer
            print("Step 7: Capturing PAGE 3 - Source PDF Viewer...")
            await page.screenshot(path=str(output_dir / "PAGE_3_PDF_Viewer.png"), full_page=False)
            print(f"  ✓ Saved: PAGE_3_PDF_Viewer.png")

            print("\n✅ All screenshots captured successfully!")
            print(f"📁 Location: {output_dir}")
            print(f"   - PAGE_1_Chat_Interface.png")
            print(f"   - PAGE_2_Answer_Citations.png")
            print(f"   - PAGE_3_PDF_Viewer.png")

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(capture_screenshots())
