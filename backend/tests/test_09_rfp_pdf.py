import fitz

from rfp.pdf import extract_specification_source_rows


def _draw_table(page: fitz.Page) -> None:
    xs = [40, 90, 180, 440, 500]
    ys = [40, 70, 100]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    headers = ["Item", "Category", "Spec", "Qty"]
    values = ["1", "Memory", "** 16GB 이상", "5"]
    for row, y in enumerate((60, 90)):
        for column, x in enumerate((45, 95, 185, 445)):
            page.insert_text((x, y), (headers if row == 0 else values)[column], fontsize=9)


def test_09_extracts_specification_rows_from_text_pdf(tmp_path) -> None:
    path = tmp_path / "spec.pdf"
    document = fitz.open()
    _draw_table(document.new_page())
    document.save(path)
    document.close()

    rows = extract_specification_source_rows(str(path))

    assert len(rows) == 1
    assert rows[0].item_number == "1"
    assert rows[0].category == "Memory"
    assert rows[0].raw_requirement.startswith("** 16GB")
    assert rows[0].quantity == "5"
    assert rows[0].page == 1
    assert rows[0].bbox is not None


def test_09_uses_marker_line_fallback_when_no_table_exists(tmp_path) -> None:
    path = tmp_path / "marker.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 40), "Item Category Spec Qty", fontsize=12)
    page.insert_text((40, 60), "** 16GB minimum", fontsize=12)
    document.save(path)
    document.close()

    rows = extract_specification_source_rows(str(path))

    assert len(rows) == 1
    assert rows[0].raw_requirement == "** 16GB minimum"
    assert rows[0].category == ""
