"""抓取层纯逻辑单测：时间解析、sec_uid 提取、分页拼装。

真实 F2 网络抓取依赖 Cookie 与联网，不在单测范围；此处只测可离线验证的纯函数。
"""

from ashare.douyin.client import (
    build_videos_from_page,
    extract_aweme_id,
    extract_sec_uid,
    parse_create_time,
)


def test_extract_aweme_id():
    assert extract_aweme_id("https://www.douyin.com/video/7412345678901234567?x=1") == "7412345678901234567"
    assert extract_aweme_id("7412345678901234567") == "7412345678901234567"
    assert extract_aweme_id("  7412345678901234567  ") == "7412345678901234567"
    # 搜索/弹窗页把视频 id 放在 modal_id 参数里
    assert extract_aweme_id(
        "https://www.douyin.com/search/创业魔法师?modal_id=7656479758474039545&type=video"
    ) == "7656479758474039545"


def test_parse_create_time_formatted_string():
    # 东八区 2024-01-02 03-04-05 -> 对应 Unix 秒
    ts = parse_create_time("2024-01-02 03-04-05")
    assert ts == 1704135845


def test_parse_create_time_raw_timestamp():
    assert parse_create_time(1704135845) == 1704135845
    assert parse_create_time("1704135845") == 1704135845


def test_parse_create_time_invalid():
    assert parse_create_time("") == 0
    assert parse_create_time(None) == 0
    assert parse_create_time("not-a-date") == 0


def test_extract_sec_uid_from_user_url():
    url = "https://www.douyin.com/user/MS4wLjABAAAAabc-123?from=search"
    assert extract_sec_uid(url) == "MS4wLjABAAAAabc-123"


def test_extract_sec_uid_passthrough():
    assert extract_sec_uid("MS4wLjABAAAAxyz") == "MS4wLjABAAAAxyz"
    assert extract_sec_uid("  MS4wLjABAAAAxyz  ") == "MS4wLjABAAAAxyz"


class _FakePage:
    """模拟 F2 UserPostFilter：各字段是并列列表。"""

    def __init__(self):
        self.aweme_id = ["111", "222"]
        self.desc = ["看好新能源", "聊聊半导体"]
        self.create_time = ["2024-01-02 03-04-05", "2024-01-03 03-04-05"]
        self.video_play_addr = [["http://cdn/a.mp4", "http://cdn/a2.mp4"], "http://cdn/b.mp4"]
        self.cover = ["http://cdn/a.jpg", "http://cdn/b.jpg"]
        self.video_duration = [12000, 30000]
        self.has_more = False


def test_build_videos_from_page():
    vids = build_videos_from_page(_FakePage(), "sec_x")
    assert len(vids) == 2
    a, b = vids
    assert a.aweme_id == "111"
    assert a.title == "看好新能源"
    assert a.author_sec_uid == "sec_x"
    assert a.video_url == "https://www.douyin.com/video/111"
    assert a.publish_time == 1704135845
    # 嵌套列表的播放地址应取第一个
    assert a.download_url == "http://cdn/a.mp4"
    assert a.duration_ms == 12000
    assert b.download_url == "http://cdn/b.mp4"


def test_build_videos_skips_empty_id():
    page = _FakePage()
    page.aweme_id = ["", "222"]
    vids = build_videos_from_page(page, "sec_x")
    assert len(vids) == 1
    assert vids[0].aweme_id == "222"
