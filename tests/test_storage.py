from ashare.models import Video
from ashare.storage import video_dir_name


def test_video_dir_name_uses_beijing_publish_date():
    video = Video("v1", "s", "t", "https://d/v1", publish_time=200)

    assert video_dir_name(video, "v1") == "1970-01-01_v1"


def test_video_dir_name_falls_back_when_publish_time_missing():
    assert video_dir_name(None, "v1") == "unknown_v1"
