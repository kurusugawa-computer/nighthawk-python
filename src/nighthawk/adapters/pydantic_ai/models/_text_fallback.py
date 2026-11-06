"""Text fallback lines shared by text-projected backends and the MCP tool-return transport."""

from __future__ import annotations

from pydantic_ai.messages import (
    AudioUrl,
    BinaryContent,
    DocumentUrl,
    FileUrl,
    ImageUrl,
    UploadedFile,
    VideoUrl,
)


def is_image_content(*, content: object) -> bool:
    if isinstance(content, ImageUrl):
        return True
    if isinstance(content, BinaryContent):
        return content.is_image
    if isinstance(content, UploadedFile):
        media_type = content.media_type
        return isinstance(media_type, str) and media_type.startswith("image/")
    return False


def _safe_file_url_media_type(*, content: FileUrl) -> str | None:
    """``FileUrl.media_type`` raises ``ValueError`` when neither the URL nor the caller supplies a media type."""
    try:
        return content.media_type
    except ValueError:
        return None


def format_file_url_as_text_lines(*, content: FileUrl) -> list[str]:
    if isinstance(content, ImageUrl):
        label = "Image"
        include_media_type = False
    elif isinstance(content, AudioUrl):
        label = "Audio"
        include_media_type = True
    elif isinstance(content, DocumentUrl):
        label = "Document"
        include_media_type = True
    elif isinstance(content, VideoUrl):
        label = "Video"
        include_media_type = True
    else:
        label = "File"
        include_media_type = True

    rendered_line_list: list[str] = [f"{label} URL: {content.url}"]
    if include_media_type:
        media_type = _safe_file_url_media_type(content=content)
        if media_type is not None:
            rendered_line_list.append(f"Media type: {media_type}")
    return rendered_line_list


def format_unresolvable_content_text(
    *,
    content: UploadedFile | BinaryContent,
    transport_label: str,
) -> str:
    """Tool-return path only.

    Coding-agent user-prompt admission rejects ``UploadedFile`` earlier, and text-projected backends stage ``BinaryContent`` as files.
    """
    if isinstance(content, UploadedFile):
        return f"UploadedFile: provider={content.provider_name}, file_id={content.file_id} (not resolvable by {transport_label})"
    return f"BinaryContent: identifier={content.identifier}, media_type={content.media_type} (embedded by this tool; not resolvable by {transport_label})"


def format_file_url_with_placeholder(*, content: FileUrl) -> list[str]:
    placeholder = "<image>" if isinstance(content, ImageUrl) else "<file>"
    return [placeholder, *format_file_url_as_text_lines(content=content)]


def format_uploaded_file_with_placeholder(
    *,
    content: UploadedFile,
    transport_label: str,
) -> list[str]:
    """For text-projected backends; the MCP transport intentionally emits the fallback text without a placeholder."""
    placeholder = "<image>" if is_image_content(content=content) else "<file>"
    metadata_line_list = [format_unresolvable_content_text(content=content, transport_label=transport_label)]
    if content.media_type is not None:
        metadata_line_list.append(f"Media type: {content.media_type}")
    return [placeholder, *metadata_line_list]


def format_unresolvable_binary_content_with_placeholder(
    *,
    content: BinaryContent,
    transport_label: str,
) -> list[str]:
    """For transports that cannot carry embedded bytes; text-projected backends stage them as files instead."""
    placeholder = "<image>" if is_image_content(content=content) else "<file>"
    return [placeholder, format_unresolvable_content_text(content=content, transport_label=transport_label)]
