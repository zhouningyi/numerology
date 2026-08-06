from server import app


def test_canon_chapter_navigation_uses_book_chapter_order():
    client = app.test_client()
    first = client.get("/canon/daodejing_wangbi?chapter=1").get_data(as_text=True)
    assert "已是第一章" in first
    assert "下一章（第 2 章）→" in first
    middle = client.get("/canon/daodejing_wangbi?chapter=2").get_data(as_text=True)
    assert "← 上一章（第 1 章）" in middle
    assert "下一章（第 3 章）→" in middle
