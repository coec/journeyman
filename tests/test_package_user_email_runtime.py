from werkzeug.datastructures import MultiDict

from app.models import Project, ProjectPackage, ProjectPackageInput
from app.models.project_package import (
    PACKAGE_BINDING_EXTRA_VAR,
    PACKAGE_INPUT_EMAIL_ADDRESSES,
)
from app.services.project_package_launch import (
    package_launch_fields,
    prepare_package_launch,
)


def _package_with_email_default():
    project = Project(
        id=901,
        name="User email project",
        owner="admin",
        enabled=True,
    )
    package = ProjectPackage(
        id=902,
        name="User email package",
        owner="admin",
        enabled=True,
        project=project,
    )
    package.set_fixed_vars({"requestor": "{{ user_email }}"})
    item = ProjectPackageInput(
        id=903,
        position=1,
        variable_name="recipient",
        label="Recipient",
        input_type=PACKAGE_INPUT_EMAIL_ADDRESSES,
        required=True,
        binding_type=PACKAGE_BINDING_EXTRA_VAR,
    )
    item.set_default_value("{{ user_email }}")
    item.set_choices([])
    item.set_validation({})
    item.set_conditions({})
    package.inputs.append(item)
    return package, item


def test_user_email_runtime_value_prefills_package_field_and_fixed_vars(app):
    package, item = _package_with_email_default()
    runtime_values = {"user_email": "operator@example.com"}

    fields = package_launch_fields(package, runtime_values=runtime_values)
    assert fields[0]["value"] == "operator@example.com"

    form = MultiDict({
        "package_value_{}".format(item.id): "{{ user_email }}",
    })
    errors, _fields, prepared = prepare_package_launch(
        package=package,
        form=form,
        runtime_values=runtime_values,
    )

    assert errors == []
    assert prepared.execution_data.execution_vars["recipient"] == [
        "operator@example.com"
    ]
    assert prepared.execution_data.execution_vars["requestor"] == (
        "operator@example.com"
    )


def test_user_email_placeholder_is_valid_email_default():
    from app.models.project_package import PACKAGE_INPUT_EMAIL_ADDRESSES
    from app.services.project_package_inputs import _validate_default_value

    assert _validate_default_value(
        2,
        PACKAGE_INPUT_EMAIL_ADDRESSES,
        "{{ user_email }}",
        [],
    ) == []


def test_other_template_text_is_not_valid_email_default():
    from app.models.project_package import PACKAGE_INPUT_EMAIL_ADDRESSES
    from app.services.project_package_inputs import _validate_default_value

    assert _validate_default_value(
        2,
        PACKAGE_INPUT_EMAIL_ADDRESSES,
        "{{ arbitrary_value }}",
        [],
    ) == [
        "Input 2 default value contains an invalid email address: "
        "{{ arbitrary_value }}."
    ]
