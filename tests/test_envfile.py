import pytest

from dokploy_mcp.envfile import EnvFileError, merge_env


def test_merge_updates_and_appends() -> None:
    current = "PORT=3000\n# keep\nSHARED=old\n"
    merged = merge_env(current, variables={"SHARED": "new", "TOKEN": "abc"})
    assert merged == "PORT=3000\n# keep\nSHARED=new\nTOKEN=abc\n"


def test_unset_removes_and_beats_set() -> None:
    merged = merge_env("A=1\nB=2\n", variables={"A": "9"}, unset=["A", "B"])
    assert merged == ""


def test_replacement_is_the_base() -> None:
    merged = merge_env("OLD=1\n", replacement="NEW=2\n", variables={"EXTRA": "x"})
    assert merged == "NEW=2\nEXTRA=x\n"


def test_quotes_values_with_spaces() -> None:
    merged = merge_env("", variables={"GREETING": 'say "hi"'})
    assert merged == 'GREETING="say \\"hi\\""\n'


def test_rejects_bad_names_and_newlines() -> None:
    with pytest.raises(EnvFileError):
        merge_env("", variables={"1BAD": "x"})
    with pytest.raises(EnvFileError):
        merge_env("", variables={"OK": "a\nb"})
