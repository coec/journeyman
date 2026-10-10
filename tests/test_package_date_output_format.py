from types import SimpleNamespace

import pytest

from app.models.project_package import PACKAGE_INPUT_DATE
from app.services.project_package_launch import _date_output_value


@pytest.mark.parametrize("fmt,expected", [
    (None, "2026-10-15"),
    ("%d/%m/%Y", "15/10/2026"),
    ("%Y%m%d", "20261015"),
    ("%d-%b-%Y", "15-Oct-2026"),
])
def test_date_output_format(fmt, expected):
    obj = SimpleNamespace(input_type=PACKAGE_INPUT_DATE,
                          get_validation=lambda: {"output_format": fmt} if fmt else {})
    assert _date_output_value(obj, "2026-10-15") == expected
