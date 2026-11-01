from mcp_file_system.errors import describe_os_error


def test_os_error_text_holds_the_description_but_no_path() -> None:
    message = describe_os_error(PermissionError(13, "Permission denied", "/very/secret/location"))
    assert "Permission denied" in message
    assert "/very/secret" not in message


def test_os_error_without_a_description_is_named_by_its_type() -> None:
    message = describe_os_error(OSError("disk full at /very/secret/location"))
    assert "OSError" in message
    assert "/very/secret" not in message
