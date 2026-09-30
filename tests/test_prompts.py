from app.prompts import (
    ANALYSIS_SCHEMA,
    DEFAULT_IMAGE_EDIT_PROMPT,
    INTEGRITY_SCHEMA,
    VIDEO_QC_SCHEMA,
    build_ref2va_prompt,
    build_image_edit_prompt,
    build_reference_image_edit_prompt,
)
from app.schemas import DishMetadata
from app.services.pipeline import render_h3_prompt


def test_prompt_modules_have_documented_sections() -> None:
    assert "category" in ANALYSIS_SCHEMA["properties"]
    assert "food" in ANALYSIS_SCHEMA["properties"]["category"]["enum"]
    assert "preserved" in str(INTEGRITY_SCHEMA["properties"])
    assert "Preserve exactly" in DEFAULT_IMAGE_EDIT_PROMPT
    assert "Replace the" in DEFAULT_IMAGE_EDIT_PROMPT
    assert "original support surface and entire background" in DEFAULT_IMAGE_EDIT_PROMPT
    assert "menu_context_en" in ANALYSIS_SCHEMA["required"]
    assert "no_menu_driven_visual_changes" in INTEGRITY_SCHEMA["required"]
    assert "reference_descriptions" in ANALYSIS_SCHEMA["required"]
    assert "full_360_orbit_completed" in VIDEO_QC_SCHEMA["required"]
    assert "background_professional" in INTEGRITY_SCHEMA["required"]


def test_h3_prompt_is_fully_rendered() -> None:
    prompt = render_h3_prompt(
        {
            "category": "food",
            "main_subject": "gnocchi",
            "title_plate": "Gnocchi al burro e salvia",
            "description_plate": "Handmade potato gnocchi served with brown butter and sage.",
            "menu_context_en": "Handmade potato gnocchi with brown butter and sage.",
            "preservation_description": "golden gnocchi on a ceramic plate",
            "reference_descriptions": [],
        }
    )
    assert 'Gnocchi al burro e salvia' in prompt
    assert "Handmade potato gnocchi served with brown butter and sage." in prompt
    assert "exactly one clockwise 360-degree orbit" in prompt
    assert "0 degrees to 90 degrees" in prompt
    assert "270 degrees to exactly 360 degrees" in prompt
    assert "not a small arc" in prompt
    assert "subject_definitions:" in prompt
    assert "summary:" in prompt
    assert "retention_analysis:" in prompt
    assert "detailed_description:" in prompt
    assert "<Picture 1>: fully_preserved" in prompt
    assert "<Picture 2>" not in prompt
    assert "overall_soundscape: N/A" in prompt
    assert "non_diegetic_music: N/A" in prompt


def test_ref2va_prompt_numbers_four_optional_references() -> None:
    prompt = build_ref2va_prompt(
        title_plate="Gnocchi",
        description_plate="Gnocchi artesanales con salsa de la casa.",
        menu_context_en="Handmade gnocchi with house sauce.",
        main_subject="plated gnocchi",
        preservation_description="plated gnocchi and its original composition",
        reference_descriptions=[f"reference view {index}" for index in range(1, 5)],
    )
    assert "<Picture 5>" in prompt
    assert "<Picture 6>" not in prompt
    assert prompt.count("weak_reference only") == 4
    assert "<Subject 1> does not rotate" in prompt
    assert "The plate does not rotate" in prompt


def test_menu_metadata_is_normalized_and_added_to_edit_prompt() -> None:
    metadata = DishMetadata(
        title_plate="  Gnocchi   al burro  ",
        description_plate="Handmade\n potato gnocchi with brown butter.",
    )
    prompt = build_image_edit_prompt(
        DEFAULT_IMAGE_EDIT_PROMPT,
        title_plate=metadata.title_plate,
        description_plate=metadata.description_plate,
    )
    assert metadata.title_plate == "Gnocchi al burro"
    assert metadata.description_plate == "Handmade potato gnocchi with brown butter."
    assert "Menu item title: Gnocchi al burro" in prompt
    assert "The reference image is" in prompt


def test_reference_edit_uses_master_only_for_background_style() -> None:
    prompt = build_reference_image_edit_prompt(
        title_plate="Gnocchi",
        description_plate="Gnocchi artesanales con salsa de la casa.",
    )
    assert "Picture 1 is the current reference view" in prompt
    assert "Picture 2 is the approved advertising master" in prompt
    assert "Never copy, blend, replace" in prompt

