"""Plain-text book parser contracts."""

from core.knowledge.parsing import ParseRequest, TxtParser


def _request(payload: bytes) -> ParseRequest:
    return ParseRequest(
        source_id="book-23962",
        relative_path="books/journey.txt",
        source_revision="sha256:book",
        media_type="text/plain",
        payload=payload,
    )


def test_txt_parser_preserves_chapter_path_and_stable_source_locators() -> None:
    payload = (
        b"\xef\xbb\xbfJourney to the West\r\n\r\n"
        b"Chapter 1\r\n\r\n"
        b"The first paragraph.\r\nStill the same paragraph.\r\n\r\n"
        + "第二回\r\n\r\n".encode()
        + "第一段落。\r\n".encode()
    )

    document = TxtParser().parse(_request(payload))
    repeated = TxtParser().parse(_request(payload))

    assert document == repeated
    assert document.title == "Journey to the West"
    assert document.language == "zh"
    assert document.provenance.parser_id == "sage.txt"
    assert [block.kind for block in document.blocks] == [
        "paragraph",
        "heading",
        "paragraph",
        "heading",
        "paragraph",
    ]
    first_heading = document.blocks[1]
    assert first_heading.heading_path == ("Chapter 1",)
    assert first_heading.line_start == 3
    assert first_heading.line_end == 3
    paragraph = document.blocks[2]
    assert paragraph.heading_path == ("Chapter 1",)
    assert paragraph.line_start == 5
    assert paragraph.line_end == 6
    assert paragraph.char_start is not None and paragraph.char_end is not None
    assert document.rendered_markdown.startswith("Journey to the West\r\n")
    assert paragraph.byte_start is not None and paragraph.byte_end is not None
    assert payload[paragraph.byte_start : paragraph.byte_end].decode("utf-8") == (
        "The first paragraph.\r\nStill the same paragraph."
    )


def test_txt_parser_rejects_non_utf8_payload() -> None:
    request = _request(b"\xff\xfe\x00")

    try:
        TxtParser().parse(request)
    except Exception as exc:
        assert "UTF-8" in str(exc)
    else:
        raise AssertionError("non-UTF-8 TXT must be rejected")
