from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from nighthawk.harness._observability import emit_harness_event, harness_span
from nighthawk.harness.grants import Capability
from nighthawk.harness.perimeter import EffectFootprint


class ImageGenerationRequest(BaseModel):
    """Provider-driven image generation from a prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1)
    output_format: Literal["png", "jpeg", "webp"] = "png"


class GeneratedImage(BaseModel):
    """Typed result of one image generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_type: str
    artifact_path: Path | None = None
    byte_count: int | None = Field(default=None, ge=0)


@runtime_checkable
class ImageGenerationClient(Protocol):
    """Caller-supplied image-generation backend; owns provider call and storage."""

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...


def image_generation_capability(*, image_generation_client: ImageGenerationClient) -> Capability:
    def generate_image(request: ImageGenerationRequest) -> GeneratedImage:
        """Generate one image from a prompt through the configured provider."""

        with harness_span("capability.generate_image", {"output_format": request.output_format}):
            image = image_generation_client.generate(request)
        emit_harness_event("capability.generate_image", {"media_type": image.media_type})
        return image

    return Capability(
        name="generate_image",
        function=generate_image,
        authority_boundary="image generation through the configured provider and storage only",
        effect_footprint=EffectFootprint(perimeter_effect_class_set=frozenset({"egress", "durable_write_back"})),
    )
