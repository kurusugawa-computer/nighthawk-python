from __future__ import annotations

from pathlib import Path

from nighthawk.parts.images import GeneratedImage, ImageGenerationRequest, image_generation_capability


class _RecordingImageClient:
    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        return GeneratedImage(media_type=f"image/{request.output_format}")


def test_generate_image_returns_the_typed_result(tmp_path: Path) -> None:
    capability = image_generation_capability(image_generation_client=_RecordingImageClient())
    assert capability.name == "generate_image"
    assert capability.function(ImageGenerationRequest(prompt="a harbour", output_format="webp")) == GeneratedImage(media_type="image/webp")
