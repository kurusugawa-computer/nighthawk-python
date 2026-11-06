from pydantic_ai.messages import BinaryContent, ImageUrl

from nighthawk.parts.workspace import ImageReference


def image_content(image: ImageReference) -> BinaryContent | ImageUrl:
    """Convert a selected workspace image into Pydantic AI multimodal content."""
    if image.url is not None:
        return ImageUrl(url=image.url)
    assert image.data is not None and image.media_type is not None
    return BinaryContent(data=image.data, media_type=image.media_type)
