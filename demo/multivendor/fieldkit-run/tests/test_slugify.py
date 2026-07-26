from fieldkit.slugify import slugify


def test_slugify_examples():
    assert slugify("Hello World") == "hello-world"
    assert slugify("  Multi   Space  ") == "multi-space"
    assert slugify("Already-Slugged") == "already-slugged"


def test_slugify_no_alphanumeric_edge_case():
    assert slugify("!!! ### @$$") == ""
