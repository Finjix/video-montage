"""Build a delivery basename from the batch's approved labels and editorial keywords."""
import ntpath
import re


def delivery_filename(material_id: str, name_fields: list[str], keyword: str, series: str) -> str:
    """Keep the supplied material ID, A keyword and B series intact across variants."""
    if not isinstance(name_fields, list):
        raise ValueError("name_fields must be an array of labels")
    fields = [material_id, *name_fields, keyword, series]
    for field in fields:
        if not isinstance(field, str) or not field or field.strip() != field:
            raise ValueError(f"filename fields must be nonempty text without surrounding whitespace: {field!r}")
        if field in (".", "..") or re.search(r'[<>:"/\\|?*_\x00-\x1f]', field):
            raise ValueError(f"invalid character or separator in filename field: {field!r}")

    filename = "_".join(fields) + ".mp4"
    if ntpath.isreserved(filename) or len(filename.encode("utf-16-le")) > 255 * 2:
        raise ValueError(f"reserved or overlong Windows filename: {filename!r}")
    return filename
