"""`python -m invest_retrospect` 실행 시 GUI 창을 띄운다.

CLI 가 필요하면 `invest-retrospect <subcommand>` 스크립트나
`python -m invest_retrospect.cli ...` 를 사용.
"""

from invest_retrospect.gui import main

if __name__ == "__main__":
    main()
