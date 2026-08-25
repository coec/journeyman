from types import SimpleNamespace
import pytest
from app.services.project_package_inputs import _is_valid_file_path,_is_valid_url
from app.services.project_package_launch import PackageLaunchError,_submitted_value
def _field(t,i=7): return SimpleNamespace(id=i,input_type=t,label="Value")
def test_url_input_validation():
    assert _is_valid_url("https://example.test/path?q=1")
    assert _is_valid_url("http://example.test/path")
    assert not _is_valid_url("ftp://example.test/file")
    assert not _is_valid_url("https://user:secret@example.test/path")
def test_file_path_validation_does_not_require_local_existence():
    assert _is_valid_file_path("/opt/application/config.yml")
    assert _is_valid_file_path(r"C:\\Program Files\\Application\\config.ini")
    assert not _is_valid_file_path("bad\x00path")
def test_submitted_url_and_file_path_are_preserved():
    f={"package_value_7":"https://example.test/a","package_value_8":"/remote/path/file.conf"}
    assert _submitted_value(_field("url"),f)=="https://example.test/a"
    assert _submitted_value(_field("file_path",8),f)=="/remote/path/file.conf"
    f["package_value_7"]="ftp://example.test/a"
    with pytest.raises(PackageLaunchError,match="valid http"): _submitted_value(_field("url"),f)
