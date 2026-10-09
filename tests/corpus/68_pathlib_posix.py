# diaspore-caps: fs
from pathlib import Path, PosixPath, PurePath, PurePosixPath

for text in ("", ".", "a//b/./c", "/a//b/../c/", "//host/share", "///a", "archive.tar.gz", ".profile", ".profile.json", "name."):
    path = PurePosixPath(text)
    print(str(path), path.parts, path.drive, path.root, path.anchor)
    print(path.name, path.stem, path.suffix, path.suffixes)
    print(str(path.parent), [str(parent) for parent in path.parents], path.is_absolute())

print(Path("a") / "b", "a" / Path("b"), PurePath("a", "/b", "c"))
print(repr(Path("a")), repr(PurePath("a")))
print(Path(Path("archive.json.gz")).suffix)
print(Path("a") == PosixPath("./a"), Path("a") == PurePath("a"), Path("a") == "a")
print(sorted([PurePath("b"), PurePath("a/z"), PurePath("a")]))
print(hash(Path("a")) == hash(PurePath("a")))
path = Path("/a/b/archive.tar.gz")
print(path.with_name("new.txt"), path.with_stem("new"), path.with_suffix(".zip"), path.with_suffix(""))
print(path.relative_to("/a"), path.is_relative_to("/a"), path.is_relative_to("/other"))
print(path.parents[0], path.parents[-1], tuple(str(parent) for parent in path.parents[1:]))
print(path.as_posix(), path.__fspath__())
print(PurePath("a/../b").relative_to("a"))
for action in (lambda: Path("/").with_name("x"), lambda: Path("a").with_name("a/b"), lambda: Path("a").with_suffix("txt"), lambda: Path("a").relative_to("b")):
    try:
        action()
    except ValueError as error:
        print(type(error).__name__)


class PathLike:
    def __fspath__(self):
        return "a/./b"


print(Path(PathLike()))
# A POSIX device exercises file methods without leaving a corpus artifact.
null = Path("/dev/null")
print(null.read_bytes(), null.write_bytes(b"payload"), null.read_text())
print(null.write_text("text\n", encoding="utf-8"))
with null.open("rb") as stream:
    print(stream.read())
