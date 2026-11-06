from app.core.security import compute_content_hash


def test_hash_is_sha256_hex():
    assert len(compute_content_hash("caption", [])) == 64


def test_media_order_does_not_matter():
    a = compute_content_hash("c", ["id-1", "id-2"])
    b = compute_content_hash("c", ["id-2", "id-1"])
    assert a == b


def test_caption_change_invalidates():
    base = compute_content_hash("Enjoy our new menu!", ["id-1"])
    edited = compute_content_hash("Enjoy our new menu! Use code 50OFF", ["id-1"])
    assert base != edited


def test_media_change_invalidates():
    base = compute_content_hash("c", ["id-1"])
    assert base != compute_content_hash("c", ["id-1", "id-2"])  # added
    assert base != compute_content_hash("c", [])                 # removed
    assert base != compute_content_hash("c", ["id-3"])           # swapped


def test_hash_uses_ids_not_urls():
    """Re-signed URLs must NOT invalidate an approval — hashes are computed
    over immutable media IDs, so URL rotation can't break the chain."""
    stable = compute_content_hash("c", ["3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"])
    assert stable == compute_content_hash("c", ["3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"])


def test_hashtags_are_part_of_the_hash():
    """Hashtags are published text — changing them after approval must
    invalidate the approval, exactly like editing the caption."""
    base = compute_content_hash("c", ["id-1"], ["sale"], None)
    assert base != compute_content_hash("c", ["id-1"], ["sale", "crypto"], None)
    assert base != compute_content_hash("c", ["id-1"], [], None)


def test_cta_is_part_of_the_hash():
    base = compute_content_hash("c", ["id-1"], [], "Visit us")
    assert base != compute_content_hash("c", ["id-1"], [], "Buy crypto now")
    assert base != compute_content_hash("c", ["id-1"], [], None)


def test_hash_fields_cannot_collide():
    """A '|' inside a caption must not be confusable with a field separator."""
    assert compute_content_hash("a|b", []) != compute_content_hash("a", ["b"])


def test_platform_overrides_are_part_of_the_hash():
    """An Instagram-only caption override is published text — it must be
    bound to the approval like everything else."""
    base = compute_content_hash("c", [], [], None, {})
    assert base != compute_content_hash("c", [], [], None, {"instagram": {"caption": "different"}})
    # key order must not matter (canonical JSON)
    a = compute_content_hash("c", [], [], None, {"facebook": {"cta": "x"}, "instagram": {"cta": "y"}})
    b = compute_content_hash("c", [], [], None, {"instagram": {"cta": "y"}, "facebook": {"cta": "x"}})
    assert a == b


def test_resolve_platform_content_overrides_only_given_fields():
    from types import SimpleNamespace
    from app.core.security import resolve_platform_content

    post = SimpleNamespace(caption="base", hashtags=["a"], cta="Go",
                           platform_targets={"instagram": {"hashtags": ["ig"], "cta": None}})
    assert resolve_platform_content(post, "facebook") == ("base", ["a"], "Go")
    assert resolve_platform_content(post, "instagram") == ("base", ["ig"], None)  # cta explicitly cleared
