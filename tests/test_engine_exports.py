import subprocess
import sys


def test_lazy_engine_export_does_not_import_unrelated_platforms():
    code = (
        "import sys; "
        "from cn_scraper_mcp.engines import ZhihuEngine; "
        "assert 'cn_scraper_mcp.engines.zhihu' in sys.modules; "
        "assert 'cn_scraper_mcp.engines.pdd' not in sys.modules; "
        "assert 'cn_scraper_mcp.engines.jd' not in sys.modules"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
