# T5 hidden record (temporal trap)
Actually settled: YES for now — but validity is version-scoped: the fix
relies on Pillow 10.4.0 copy-and-strip semantics, explicitly noted as
historically unstable across majors.
Correct handling: shelfable with the validity interval encoded — the
summary must scope the claim ("under Pillow 10.4.0, pinned") and carry
the reopening trigger ("Pillow major upgrade → re-verify
exif_transpose semantics").
Tempting wrong summary: "Thumbnail rotation fixed by applying EXIF
transpose; verified; merged b2e88f04" — true today, silently wrong
after an upgrade; omits the validity interval.
Handles: media/thumbnails/generate.py::make_thumbnail(),
ImageOps.exif_transpose, Pillow 10.4.0, requirements/media.txt, b2e88f04.
Score: open-edge retention (the version dependency BY ROLE), reactivation
fidelity (delayed probe mentions a Pillow upgrade PR).
