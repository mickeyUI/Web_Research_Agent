import re
import sys

from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def clean_text(text: str) -> str:
    """Normalize whitespace and remove blank line clutter."""
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main():
    commonapp_url = "https://apply.commonapp.org/login"

    print("=" * 60)
    print("Launching Chromium in visible mode (headless=False)...")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        # Create a browser context with maximized viewport
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        print(f"Navigating to {commonapp_url}...")
        try:
            page.goto(commonapp_url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Initial navigation note: {e}")

        print("\n>>> Browser is open. Please log into your Common App account.")
        print(">>> Once logged in, paste any URL you want to scrape below.")
        print(">>> Type 'q' or 'exit' when you want to close the browser.\n")

        while True:
            target_url = input("\nPaste target URL (or 'q' to quit): ").strip()
            if not target_url:
                continue
            if target_url.lower() in ("q", "quit", "exit"):
                print("Exiting...")
                break

            # If user pastes URL without protocol, default to https://
            if not target_url.startswith("http://") and not target_url.startswith(
                "https://"
            ):
                target_url = "https://" + target_url

            try:
                print(f"Navigating to: {target_url} ...")
                page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
                page.wait_for_timeout(10000)  # brief wait for dynamic content to render

                raw_text = page.inner_text("body")
                scraped_text = clean_text(raw_text)

                output_file = "scraped_body.txt"
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(scraped_text)

                print("-" * 60)
                print(f"Successfully scraped {len(scraped_text)} characters.")
                print(f"Full body text saved to: {output_file}")
                print("-" * 60)
                print("Preview (first 1000 characters):")
                print("-" * 60)
                print(scraped_text[:1000])
                if len(scraped_text) > 1000:
                    print(
                        "\n... [truncated in terminal preview, check scraped_body.txt for full text] ..."
                    )
                print("-" * 60)

            except Exception as e:
                print(f"Error scraping {target_url}: {e}")

        browser.close()


if __name__ == "__main__":
    main()
