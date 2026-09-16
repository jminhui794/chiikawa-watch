"""Exercise the actual browser request scheduler with simulated fetches, no network."""
import unittest
from playwright.sync_api import sync_playwright
from cinema_scan import BATCH_JS


class BrowserBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="chrome", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def run_batch(self, urls, timeout=1000, budget=5000):
        page = self.browser.new_page()
        try:
            page.evaluate("""() => {
              window.active = 0; window.peak = 0; window.calls = {};
              window.fetch = (url, options) => new Promise((resolve, reject) => {
                calls[url] = (calls[url] || 0) + 1;
                active++; peak = Math.max(peak, active);
                let finished = false;
                const finish = (aborted) => {
                  if (finished) return; finished = true; active--;
                  if (aborted) reject(new DOMException('aborted','AbortError'));
                  else resolve({status:url === 'retry' && calls[url] === 1 ? 503 : 200,
                                text:async () => url});
                };
                options.signal.addEventListener('abort', () => finish(true));
                if (url !== 'hang') setTimeout(() => finish(false), 30);
              });
            }""")
            results = page.evaluate(BATCH_JS, {"jobs": [{"url": url} for url in urls],
                "concurrency": 4, "timeout": timeout, "budget": budget})
            stats = page.evaluate("() => ({peak, calls, active})")
            return results, stats
        finally:
            page.close()

    def test_concurrency_cap_and_every_request_survives_retry(self):
        urls = [str(index) for index in range(12)] + ["retry"]
        results, stats = self.run_batch(urls)
        self.assertEqual(len(results), len(urls))
        self.assertTrue(all(result["s"] == 200 for result in results))
        self.assertEqual(stats["calls"]["retry"], 2)
        self.assertGreater(stats["peak"], 1)
        self.assertLessEqual(stats["peak"], 4)
        self.assertEqual(stats["active"], 0)

    def test_hung_requests_are_aborted_and_queued_scopes_are_accounted_for(self):
        results, stats = self.run_batch(["hang"] * 8, timeout=80, budget=200)
        self.assertEqual(len(results), 8)
        self.assertTrue(all("error" in result for result in results))
        self.assertEqual(stats["active"], 0)


if __name__ == "__main__":
    unittest.main()
