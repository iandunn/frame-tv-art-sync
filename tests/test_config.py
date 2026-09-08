"""Covers the config loader, whose whole job is to fail with a message that names the problem."""

from __future__ import annotations

from fractions import Fraction

import pytest

from frame_tv_art_sync import mattes
from frame_tv_art_sync.config import (
    DEFAULT_HIGHLIGHT_ROLLOFF,
    DEFAULT_JPEG_QUALITY,
    ConfigError,
    load_config,
)

# Split out so a test can build an `[art]` table of its own without string surgery on a config
# that already holds one.
BEFORE_ART = """
[tv]
host = "192.168.1.50"
name = "frame-tv-art-sync"
token_file = "token"

[source.google_album]
url = "https://photos.app.goo.gl/EXAMPLE"

"""

COMPLETE = (
    BEFORE_ART
    + """[art]
fallback_matte = "flexible_black"

[art.matte_by_ratio]
"16:9" = "modern_black"
"4:3" = "flexible_black"
"3:4" = "shadowbox_black"
"""
)


def write_config(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def art_config(fallback: str = "flexible_black", by_ratio: str | None = None) -> str:
    """A whole config whose `[art]` table is only what a test is about."""
    body = BEFORE_ART + f'[art]\nfallback_matte = "{fallback}"\n'
    if by_ratio is not None:
        body += "\n[art.matte_by_ratio]\n" + by_ratio + "\n"

    return body


def test_reads_every_field(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.tv.host == "192.168.1.50"
    assert config.tv.name == "frame-tv-art-sync"
    assert config.google_album.url == "https://photos.app.goo.gl/EXAMPLE"
    assert config.art.fallback_matte == "flexible_black"
    assert config.art.matte_by_ratio == {
        Fraction(16, 9): "modern_black",
        Fraction(4, 3): "flexible_black",
        Fraction(3, 4): "shadowbox_black",
    }


def test_the_token_path_resolves_against_the_config_file(tmp_path):
    """So the same config works from a shell and from a job started in some other directory."""
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.tv.token_file == tmp_path / "token"


def test_a_missing_file_names_the_path_and_the_example(tmp_path):
    with pytest.raises(ConfigError, match="config.example.toml"):
        load_config(tmp_path / "config.toml")


def test_a_missing_key_names_it(tmp_path):
    path = write_config(tmp_path, COMPLETE.replace('url = "https://photos.app.goo.gl/EXAMPLE"', ""))

    with pytest.raises(ConfigError, match=r"source\.google_album\.url"):
        load_config(path)


def test_a_missing_table_names_the_first_level_that_is_absent(tmp_path):
    path = write_config(tmp_path, BEFORE_ART)

    with pytest.raises(ConfigError, match=r"`art`"):
        load_config(path)


def test_an_empty_value_is_not_accepted(tmp_path):
    path = write_config(tmp_path, COMPLETE.replace('host = "192.168.1.50"', 'host = "  "'))

    with pytest.raises(ConfigError, match=r"tv\.host"):
        load_config(path)


def test_invalid_toml_says_so(tmp_path):
    path = write_config(tmp_path, "[tv\nhost =")

    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_the_pipeline_table_is_optional(tmp_path):
    """A config written before `[pipeline]` existed still loads."""
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.pipeline.highlight_rolloff == DEFAULT_HIGHLIGHT_ROLLOFF
    assert config.pipeline.jpeg_quality == DEFAULT_JPEG_QUALITY


def test_the_pipeline_table_overrides_the_defaults(tmp_path):
    body = COMPLETE + "\n[pipeline]\nhighlight_rolloff = 0.2\njpeg_quality = 85\n"

    config = load_config(write_config(tmp_path, body))

    assert config.pipeline.highlight_rolloff == 0.2
    assert config.pipeline.jpeg_quality == 85


def test_a_rolloff_past_the_shoulder_is_refused(tmp_path):
    body = COMPLETE + "\n[pipeline]\nhighlight_rolloff = 0.6\n"

    with pytest.raises(ConfigError, match=r"highlight_rolloff"):
        load_config(write_config(tmp_path, body))


def test_a_non_numeric_rolloff_is_refused(tmp_path):
    body = COMPLETE + '\n[pipeline]\nhighlight_rolloff = "a lot"\n'

    with pytest.raises(ConfigError, match="has to be a number"):
        load_config(write_config(tmp_path, body))


@pytest.mark.parametrize("matte_type", sorted(mattes.FIXED_APERTURE_TYPES))
def test_a_fixed_aperture_matte_on_any_other_shape_is_refused_when_the_config_loads(
    tmp_path, matte_type
):
    """This is the check that keeps a crash off the panel.

    The TV accepts a fixed-aperture matte on a 4:3 and then puts an error dialog up that needs a
    power cycle, so it is refused at load time rather than at upload time, when a scheduled job
    would hit it with nobody watching.
    """
    path = write_config(tmp_path, art_config(by_ratio=f'"4:3" = "{matte_type}_black"'))

    with pytest.raises(ConfigError, match="fixed 16:9") as raised:
        load_config(path)

    assert 'art.matte_by_ratio."4:3"' in str(raised.value)


@pytest.mark.parametrize("matte_type", sorted(mattes.FIXED_APERTURE_TYPES))
def test_a_fixed_aperture_matte_on_the_one_shape_it_fits_is_accepted(tmp_path, matte_type):
    path = write_config(tmp_path, art_config(by_ratio=f'"16:9" = "{matte_type}_black"'))

    config = load_config(path)

    assert config.art.matte_by_ratio == {Fraction(16, 9): f"{matte_type}_black"}


def test_a_bad_matte_names_the_ratio_key_it_came_from(tmp_path):
    """`triptych` is one of the four the picker offers for no image at all."""
    path = write_config(tmp_path, art_config(by_ratio='"3:4" = "triptych_black"'))

    with pytest.raises(ConfigError, match=r'art\.matte_by_ratio\."3:4"'):
        load_config(path)


def test_a_color_the_firmware_has_no_record_of_is_refused_in_the_ratio_table(tmp_path):
    path = write_config(tmp_path, art_config(by_ratio='"4:3" = "flexible_chartreuse"'))

    with pytest.raises(ConfigError, match="chartreuse"):
        load_config(path)


def test_the_ratio_table_is_optional_and_every_shape_then_takes_the_fallback(tmp_path):
    config = load_config(write_config(tmp_path, art_config()))

    assert config.art.matte_by_ratio == {}
    assert config.art.fallback_matte == "flexible_black"


def test_a_ratio_key_that_is_not_a_ratio_names_itself(tmp_path):
    path = write_config(tmp_path, art_config(by_ratio='"widescreen" = "flexible_black"'))

    with pytest.raises(ConfigError, match="widescreen"):
        load_config(path)


def test_a_ratio_key_with_a_zero_denominator_is_refused(tmp_path):
    path = write_config(tmp_path, art_config(by_ratio='"16:0" = "flexible_black"'))

    with pytest.raises(ConfigError, match=r"art\.matte_by_ratio"):
        load_config(path)


def test_two_keys_that_reduce_to_one_shape_are_refused(tmp_path):
    """Otherwise `16:10` and `8:5` are two keys that silently shadow each other."""
    path = write_config(
        tmp_path,
        art_config(by_ratio='"16:10" = "flexible_black"\n"8:5" = "shadowbox_black"'),
    )

    with pytest.raises(ConfigError, match="twice"):
        load_config(path)


def test_a_ratio_key_is_reduced_on_the_way_in(tmp_path):
    config = load_config(write_config(tmp_path, art_config(by_ratio='"8:6" = "flexible_black"')))

    assert config.art.matte_by_ratio == {Fraction(4, 3): "flexible_black"}


def test_a_non_string_matte_names_its_own_ratio_key(tmp_path):
    path = write_config(tmp_path, art_config(by_ratio='"4:3" = 5'))

    with pytest.raises(ConfigError, match=r'art\.matte_by_ratio\."4:3"'):
        load_config(path)


def test_an_empty_matte_names_its_own_ratio_key(tmp_path):
    path = write_config(tmp_path, art_config(by_ratio='"4:3" = "  "'))

    with pytest.raises(ConfigError, match="non-empty string"):
        load_config(path)


def test_a_ratio_table_that_is_not_a_table_says_what_one_looks_like(tmp_path):
    body = BEFORE_ART + (
        '[art]\nfallback_matte = "flexible_black"\nmatte_by_ratio = "flexible_black"\n'
    )

    with pytest.raises(ConfigError, match="table of ratio names"):
        load_config(write_config(tmp_path, body))


def test_a_bare_none_is_a_matte_a_ratio_key_may_name(tmp_path):
    """The TV reports it as a bare `none` rather than `none_black`, so it has no color half."""
    config = load_config(write_config(tmp_path, art_config(by_ratio='"3:4" = "none"')))

    assert config.art.matte_by_ratio == {Fraction(3, 4): "none"}


def test_the_fallback_is_required(tmp_path):
    body = BEFORE_ART + '[art]\n\n[art.matte_by_ratio]\n"16:9" = "modern_black"\n'

    with pytest.raises(ConfigError, match=r"art\.fallback_matte"):
        load_config(write_config(tmp_path, body))


@pytest.mark.parametrize("matte_type", sorted(mattes.FIXED_APERTURE_TYPES))
def test_a_fixed_aperture_fallback_is_refused_because_it_lands_on_shapes_nobody_named(
    tmp_path, matte_type
):
    path = write_config(tmp_path, art_config(fallback=f"{matte_type}_black"))

    with pytest.raises(ConfigError, match="fixed 16:9") as raised:
        load_config(path)

    assert "art.fallback_matte" in str(raised.value)


@pytest.mark.parametrize("matte_type", sorted(mattes.ACCEPTED_ON_ANY_SHAPE - {"none"}))
def test_a_fallback_the_tv_draws_around_anything_is_accepted(tmp_path, matte_type):
    config = load_config(write_config(tmp_path, art_config(fallback=f"{matte_type}_polar")))

    assert config.art.fallback_matte == f"{matte_type}_polar"


def test_a_bare_none_fallback_is_accepted(tmp_path):
    config = load_config(write_config(tmp_path, art_config(fallback="none")))

    assert config.art.fallback_matte == "none"


def test_a_fallback_color_the_firmware_has_no_record_of_is_refused(tmp_path):
    path = write_config(tmp_path, art_config(fallback="flexible_chartreuse"))

    with pytest.raises(ConfigError, match="chartreuse") as raised:
        load_config(path)

    assert "art.fallback_matte" in str(raised.value)


def test_a_fallback_missing_its_color_half_names_its_own_key(tmp_path):
    """A type on its own is not an id, and the message has to say which key held it."""
    path = write_config(tmp_path, art_config(fallback="flexible"))

    with pytest.raises(ConfigError, match=r"art\.fallback_matte"):
        load_config(path)


@pytest.mark.parametrize("key", ["matte", "landscape_matte", "portrait_matte"])
def test_a_superseded_matte_key_explains_what_replaced_it(tmp_path, key):
    """A config written before the ratio table would otherwise fail as a plain missing key."""
    body = BEFORE_ART + f'[art]\n{key} = "flexible_black"\n'

    with pytest.raises(ConfigError, match="has been replaced") as raised:
        load_config(write_config(tmp_path, body))

    message = str(raised.value)
    assert f"`art.{key}`" in message
    assert "[art.matte_by_ratio]" in message
    assert "art.fallback_matte" in message
    assert "is missing" not in message


def test_every_superseded_key_in_one_file_is_named_at_once(tmp_path):
    body = BEFORE_ART + (
        '[art]\nmatte = "none"\nlandscape_matte = "modern_black"\n'
        'portrait_matte = "flexible_black"\n'
    )

    with pytest.raises(ConfigError, match="has been replaced") as raised:
        load_config(write_config(tmp_path, body))

    message = str(raised.value)
    assert "art.matte`" in message
    assert "art.landscape_matte" in message
    assert "art.portrait_matte" in message


def test_the_inventory_path_resolves_against_the_config_file(tmp_path):
    """The inventory is per-config, so a job pointed elsewhere gets that config's inventory."""
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.inventory_file == tmp_path / "inventory.json"


def test_the_sync_table_is_optional_and_short_run_is_off(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.sync.short_run == 0


def test_a_short_run_count_is_read(tmp_path):
    body = COMPLETE + "\n[sync]\nshort_run = 5\n"

    assert load_config(write_config(tmp_path, body)).sync.short_run == 5


def test_a_fractional_short_run_is_refused_rather_than_rounded(tmp_path):
    body = COMPLETE + "\n[sync]\nshort_run = 2.5\n"

    with pytest.raises(ConfigError, match="whole number"):
        load_config(write_config(tmp_path, body))


def test_a_negative_short_run_is_refused(tmp_path):
    body = COMPLETE + "\n[sync]\nshort_run = -1\n"

    with pytest.raises(ConfigError, match=r"short_run"):
        load_config(write_config(tmp_path, body))


def test_the_delete_flags_default_to_the_mirror_this_tool_has_always_been(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.sync.delete_removed_from_album is True
    assert config.sync.delete_added_by_hand is False


def test_the_delete_flags_are_read(tmp_path):
    body = (
        COMPLETE
        + "\n[sync]\ndelete_removed_from_album = false\ndelete_added_by_hand = true\n"
    )

    config = load_config(write_config(tmp_path, body))

    assert config.sync.delete_removed_from_album is False
    assert config.sync.delete_added_by_hand is True


def test_a_quoted_delete_flag_is_refused_rather_than_read_as_true(tmp_path):
    """Both flags decide whether a run destroys photos, so a near miss fails rather than reads."""
    body = COMPLETE + '\n[sync]\ndelete_added_by_hand = "true"\n'

    with pytest.raises(ConfigError, match="true` or `false"):
        load_config(write_config(tmp_path, body))


def test_a_numeric_delete_flag_is_refused(tmp_path):
    body = COMPLETE + "\n[sync]\ndelete_added_by_hand = 1\n"

    with pytest.raises(ConfigError, match="true` or `false"):
        load_config(write_config(tmp_path, body))


def test_the_play_order_defaults_to_what_the_tv_does_anyway(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.sync.play_order == "newest_first"
    assert config.sync.rebuilds_every_run is False


def test_asking_for_the_oldest_first_makes_every_run_a_rebuild(tmp_path):
    """It is only reachable by uploading the album backwards, so it cannot be maintained."""
    body = COMPLETE + '\n[sync]\nplay_order = "oldest_first"\n'

    assert load_config(write_config(tmp_path, body)).sync.rebuilds_every_run is True


def test_a_play_order_that_is_not_one_is_refused(tmp_path):
    """Read as the default it would quietly do the opposite of what the file asks for."""
    body = COMPLETE + '\n[sync]\nplay_order = "oldest"\n'

    with pytest.raises(ConfigError, match="play_order"):
        load_config(write_config(tmp_path, body))


def test_a_short_run_without_the_mirror_is_refused(tmp_path):
    """A short run works by mirroring the album down, so with the mirror off it does the reverse."""
    body = COMPLETE + "\n[sync]\nshort_run = 2\ndelete_removed_from_album = false\n"

    with pytest.raises(ConfigError, match="short_run"):
        load_config(write_config(tmp_path, body))


def test_a_short_run_of_zero_is_fine_without_the_mirror(tmp_path):
    body = COMPLETE + "\n[sync]\nshort_run = 0\ndelete_removed_from_album = false\n"

    assert load_config(write_config(tmp_path, body)).sync.short_run == 0


def test_no_bakeoff_table_means_every_matte(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.bakeoff.colors is None
    assert config.bakeoff.types is None


def test_the_bakeoff_table_narrows_a_round(tmp_path):
    body = COMPLETE + '\n[bakeoff]\ncolors = ["polar", "sand"]\ntypes = ["flexible"]\n'

    config = load_config(write_config(tmp_path, body))

    assert config.bakeoff.colors == ("polar", "sand")
    assert config.bakeoff.types == ("flexible",)


def test_a_color_the_firmware_has_no_record_of_is_refused(tmp_path):
    path = write_config(tmp_path, COMPLETE + '\n[bakeoff]\ncolors = ["chartreuse"]\n')

    with pytest.raises(ConfigError, match="chartreuse"):
        load_config(path)


def test_a_type_the_tv_draws_around_no_shape_at_all_is_refused(tmp_path):
    """`panoramic` is one of the four `get_matte_list()` returns that the picker never offers."""
    path = write_config(tmp_path, COMPLETE + '\n[bakeoff]\ntypes = ["panoramic"]\n')

    with pytest.raises(ConfigError, match="panoramic"):
        load_config(path)


def test_a_widescreen_only_type_is_accepted_since_one_list_serves_every_shape(tmp_path):
    """`modernwide` fits a 16:9 and nothing else, and which rounds it reaches is decided there."""
    config = load_config(
        write_config(tmp_path, COMPLETE + '\n[bakeoff]\ntypes = ["modernwide"]\n')
    )

    assert config.bakeoff.types == ("modernwide",)


def test_an_empty_bakeoff_list_means_every_matte(tmp_path):
    """The same as leaving the key out, so the key can stay in the file to be edited later."""
    config = load_config(write_config(tmp_path, COMPLETE + "\n[bakeoff]\ncolors = []\n"))

    assert config.bakeoff.colors is None


def test_a_bakeoff_list_that_is_not_a_list_is_refused(tmp_path):
    path = write_config(tmp_path, COMPLETE + '\n[bakeoff]\ncolors = "polar"\n')

    with pytest.raises(ConfigError, match="list of strings"):
        load_config(path)


def test_no_crop_table_means_nothing_is_cropped(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.pipeline.crop == ()
    assert config.pipeline.crop_overrides == ()


def test_reads_the_crop_rules_in_file_order(tmp_path):
    """Order is the rule, since the first match wins and nothing else disambiguates."""
    config = load_config(
        write_config(
            tmp_path,
            COMPLETE
            + """
[[pipeline.crop]]
when = "4:3"
to = "16:9"
anchor = "top"

[[pipeline.crop]]
when = "portrait"
to = "none"
""",
        )
    )

    assert [rule.when for rule in config.pipeline.crop] == ["4:3", "portrait"]
    assert config.pipeline.crop[0].to == "16:9"
    assert config.pipeline.crop[0].anchor == "top"
    assert config.pipeline.crop[1].anchor == "center"


def test_a_rule_with_no_when_matches_everything(tmp_path):
    config = load_config(
        write_config(tmp_path, COMPLETE + '\n[[pipeline.crop]]\nto = "16:9"\n')
    )

    assert config.pipeline.crop[0].when == "*"


@pytest.mark.parametrize(
    "above, below",
    [
        ("*", "3:4"),
        ("landscape", "4:3"),
        ("portrait", "3:4"),
        ("4:3", "85:64"),
        ("landscape", "landscape"),
    ],
)
def test_a_rule_that_buries_a_later_one_is_refused(tmp_path, above, below):
    """A table read top to bottom makes it easy to bury a rule, and nothing else would say so."""
    body = COMPLETE + (
        f'\n[[pipeline.crop]]\nwhen = "{above}"\nto = "16:9"\n\n'
        f'[[pipeline.crop]]\nwhen = "{below}"\nto = "none"\n'
    )

    with pytest.raises(ConfigError, match="can never fire"):
        load_config(write_config(tmp_path, body))


@pytest.mark.parametrize(
    "above, below",
    [("4:3", "3:4"), ("landscape", "portrait"), ("4:3", "landscape"), ("3:2", "4:3")],
)
def test_a_rule_that_leaves_a_later_one_reachable_is_allowed(tmp_path, above, below):
    body = COMPLETE + (
        f'\n[[pipeline.crop]]\nwhen = "{above}"\nto = "16:9"\n\n'
        f'[[pipeline.crop]]\nwhen = "{below}"\nto = "none"\n'
    )

    assert len(load_config(write_config(tmp_path, body)).pipeline.crop) == 2


@pytest.mark.parametrize(
    "row, message",
    [
        ('when = "sideways"\nto = "16:9"', "aspect ratio"),
        ('when = "4:3"\nto = "wide"', "aspect ratio"),
        ('when = "4:3"\nto = "16:9"\nanchor = "middle"', "anchor"),
        ('when = "4:3"', "missing `to`"),
        ('when = "4:3"\nto = "16:9"\nzoom = "2"', "unrecognized key"),
    ],
)
def test_a_rule_that_cannot_be_read_is_refused(tmp_path, row, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, COMPLETE + f"\n[[pipeline.crop]]\n{row}\n"))


def test_reads_the_crop_overrides(tmp_path):
    config = load_config(
        write_config(
            tmp_path,
            COMPLETE
            + """
[pipeline.crop_overrides]
"AF1QipNt2uKI" = { to = "none" }
"AF1QipObuXbB" = { to = "16:9", anchor = "bottom" }
""",
        )
    )

    assert [key for key, _ in config.pipeline.crop_overrides] == [
        "AF1QipNt2uKI",
        "AF1QipObuXbB",
    ]
    assert config.pipeline.crop_overrides[1][1].anchor == "bottom"


def test_an_override_key_too_short_to_name_one_photo_is_refused(tmp_path):
    body = COMPLETE + '\n[pipeline.crop_overrides]\n"AF1Qip" = { to = "none" }\n'

    with pytest.raises(ConfigError, match="shorter than"):
        load_config(write_config(tmp_path, body))


def test_an_override_sitting_inside_another_is_refused(tmp_path):
    """Only one of the pair could ever apply, and which one is not something to guess at."""
    body = (
        COMPLETE
        + '\n[pipeline.crop_overrides]\n"AF1QipNt2uKI" = { to = "none" }\n'
        '"AF1QipNt2uKIcvII" = { to = "16:9" }\n'
    )

    with pytest.raises(ConfigError, match="sitting inside"):
        load_config(write_config(tmp_path, body))


def test_an_override_cannot_pick_the_photos_it_applies_to(tmp_path):
    """Its key already did that, so a `when` on one means the file says something it didn't mean."""
    body = COMPLETE + (
        '\n[pipeline.crop_overrides]\n"AF1QipNt2uKI" = { when = "4:3", to = "none" }\n'
    )

    with pytest.raises(ConfigError, match="unrecognized key"):
        load_config(write_config(tmp_path, body))


# `[[pipeline.composite]]` and `[pipeline.composite_style]`


def test_no_composite_table_means_one_photo_per_image(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.pipeline.composite == ()
    assert config.pipeline.composite_style.mat == "antique"
    assert config.pipeline.composite_style.edge == "shadowbox"


def test_reads_a_composite_table(tmp_path):
    body = COMPLETE + """
[[pipeline.composite]]
when = "3:4"
count = 2
layout = "row"

[[pipeline.composite]]
when = "4:3"
count = 4
layout = "grid"
gap_down = 49

[pipeline.composite_style]
mat = "polar"
edge = "bevel"
tooth = false
"""

    config = load_config(write_config(tmp_path, body))

    assert [(rule.when, rule.count, rule.layout) for rule in config.pipeline.composite] == [
        ("3:4", 2, "row"),
        ("4:3", 4, "grid"),
    ]
    assert config.pipeline.composite[1].gap_down == 49
    assert config.pipeline.composite_style.mat == "polar"
    assert config.pipeline.composite_style.tooth is False


def test_a_composite_rule_buried_under_a_wider_one_is_refused(tmp_path):
    body = COMPLETE + """
[[pipeline.composite]]
when = "landscape"
count = 2
layout = "row"

[[pipeline.composite]]
when = "4:3"
count = 4
layout = "grid"
"""

    with pytest.raises(ConfigError, match="can never fire"):
        load_config(write_config(tmp_path, body))


def test_a_layout_the_pipeline_cannot_draw_is_refused(tmp_path):
    body = COMPLETE + '\n[[pipeline.composite]]\nwhen = "3:4"\ncount = 2\nlayout = "mosaic"\n'

    with pytest.raises(ConfigError, match="mosaic"):
        load_config(write_config(tmp_path, body))


def test_full_with_more_than_one_photo_is_refused(tmp_path):
    body = COMPLETE + '\n[[pipeline.composite]]\nwhen = "3:4"\ncount = 2\nlayout = "full"\n'

    with pytest.raises(ConfigError, match="full"):
        load_config(write_config(tmp_path, body))


def test_a_mat_color_the_tv_has_no_triple_for_is_refused(tmp_path):
    body = COMPLETE + '\n[pipeline.composite_style]\nmat = "chartreuse"\n'

    with pytest.raises(ConfigError, match="chartreuse"):
        load_config(write_config(tmp_path, body))


def test_an_unrecognized_composite_key_is_refused(tmp_path):
    body = COMPLETE + '\n[[pipeline.composite]]\nwhen = "3:4"\ncount = 2\nlayout = "row"\nmat = "polar"\n'

    with pytest.raises(ConfigError, match="mat"):
        load_config(write_config(tmp_path, body))
