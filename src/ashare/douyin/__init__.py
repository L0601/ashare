"""抖音抓取层：对外暴露同步接口，隔离 F2 的异步细节。"""

from .base import DouyinClient, FakeDouyinClient

__all__ = ["DouyinClient", "FakeDouyinClient"]
