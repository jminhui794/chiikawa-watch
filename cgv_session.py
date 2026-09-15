"""CGV 전용 로그인 프로필 생성. 창을 닫으면 종료한다."""
from playwright.sync_api import sync_playwright
from watch import CGV_PROFILE


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            CGV_PROFILE, channel="chrome", headless=False, locale="ko-KR")
        page = context.pages[0] if context.pages else context.new_page()
        context.on("close", lambda _: print("CGV login browser closed.", flush=True))
        try:
            page.goto("https://cgv.co.kr/cnm/movieBook/movie",
                      wait_until="domcontentloaded", timeout=45000)
            print("Log in to CGV, open the cinema schedule, then close this browser window.",
                  flush=True)
            while context.pages:
                context.pages[0].wait_for_timeout(1000)
        except Exception as e:
            if context.pages:
                raise
        finally:
            context.close()


if __name__ == "__main__":
    main()
