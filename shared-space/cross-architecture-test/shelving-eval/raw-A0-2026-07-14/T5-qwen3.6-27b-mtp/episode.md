# T5 — Work history

USER: The image thumbnails are coming out rotated for some phone
uploads. Can you fix it?

ASSISTANT: Fixed. Cause: `Pillow`'s `Image.open()` does not apply EXIF
orientation automatically. Added `ImageOps.exif_transpose()` in
`media/thumbnails/generate.py::make_thumbnail()` before resize. Verified
against the 14 sample uploads from the bug report — all orientations now
correct. One implementation note: `exif_transpose()` in our installed
Pillow (10.4.0, pinned in `requirements/media.txt`) returns a copy and
strips the orientation tag, which is the behavior the fix relies on; the
Pillow changelog shows this function's in-place/copy semantics have
changed between major versions before. Merged as `b2e88f04`.

USER: Good. That closes the thumbnail bug.
