def test_python_and_imports():
    import psycopg  # noqa: F401
    import databricks.sdk  # noqa: F401

    assert True
