"""Covers the config loader, whose whole job is to fail with a message that names the problem."""

from __future__ import annotations

import pytest

from frame_tv_art_sync.config import (
    DEFAULT_HIGHLIGHT_ROLLOFF,
    DEFAULT_JPEG_QUALITY,
    ConfigError,
    load_config,
)

COMPLETE = """
[tv]
host = "192.168.1.50"
name = "frame-tv-art-sync"
token_file = "token"

[source.google_album]
url = "https://photos.app.goo.gl/EXAMPLE"

[art]
landscape_matte = "modern_black"
portrait_matte = "flexible_black"
"""


def write_config(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_reads_every_field(tmp_path):
    config = load_config(write_config(tmp_path, COMPLETE))

    assert config.tv.host == "192.168.1.50"
    assert config.tv.name == "frame-tv-art-sync"
    assert config.google_album.url == "https://photos.app.goo.gl/EXAMPLE"
    assert config.art.landscape_matte == "modern_black"
    assert config.art.portrait_matte == "flexible_black"


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
    path = write_config(tmp_path, COMPLETE.split("[art]")[0])

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


def test_a_matte_the_tv_would_crash_on_is_refused_when_the_config_loads(tmp_path):
    """Rather than at upload time, when a scheduled job would hit it with nobody watching."""
    body = COMPLETE.replace('portrait_matte = "flexible_black"',
                            'portrait_matte = "modernwide_polar"')

    with pytest.raises(ConfigError, match=r"art\.portrait_matte"):
        load_config(write_config(tmp_path, body))


def test_a_bad_landscape_matte_names_its_own_key(tmp_path):
    body = COMPLETE.replace('landscape_matte = "modern_black"',
                            'landscape_matte = "triptych_black"')

    with pytest.raises(ConfigError, match=r"art\.landscape_matte"):
        load_config(write_config(tmp_path, body))


def test_the_old_single_matte_key_explains_what_replaced_it(tmp_path):
    """A config written before the split would otherwise fail as a plain missing key."""
    body = COMPLETE.replace('landscape_matte = "modern_black"', 'matte = "none"')

    with pytest.raises(ConfigError, match=r"art\.landscape_matte"):
        load_config(write_config(tmp_path, body))

    with pytest.raises(ConfigError, match="has been replaced"):
        load_config(write_config(tmp_path, body))


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


def test_a_type_the_tv_offers_for_neither_orientation_is_refused(tmp_path):
    """`panoramic` is one of the four `get_matte_list()` returns that the picker never offers."""
    path = write_config(tmp_path, COMPLETE + '\n[bakeoff]\ntypes = ["panoramic"]\n')

    with pytest.raises(ConfigError, match="panoramic"):
        load_config(path)


def test_a_landscape_only_type_is_accepted_since_one_list_serves_both_orientations(tmp_path):
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
