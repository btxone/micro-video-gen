import pytest
from pydantic import ValidationError

from app.schemas import DishMetadata


def test_dish_metadata_requires_both_menu_fields() -> None:
    with pytest.raises(ValidationError):
        DishMetadata(title_plate="Gnocchi")

    with pytest.raises(ValidationError):
        DishMetadata(
            title_plate="Gnocchi",
            description_plate="short",
        )


def test_dish_metadata_normalizes_whitespace() -> None:
    metadata = DishMetadata(
        title_plate="  Gnocchi   al burro  ",
        description_plate="Gnocchi\nartesanales   con manteca noisette.",
    )

    assert metadata.title_plate == "Gnocchi al burro"
    assert metadata.description_plate == "Gnocchi artesanales con manteca noisette."
