"""Backend-private text-fallback formatting for multimodal content.

Used by both the text-projected coding-agent backends (``text_projection``)
and the MCP tool-return transport (``mcp_boundary``) to emit a consistent
``<image>``/``<file>`` placeholder line followed by descriptive metadata when
multimodal content cannot be carried natively by the transport.
"""

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
    """Return True when *content* represents image media."""
    if isinstance(content, ImageUrl):
        return True
    if isinstance(content, BinaryContent):
        return content.is_image
    if isinstance(content, UploadedFile):
        media_type = content.media_type
        return isinstance(media_type, str) and media_type.startswith("image/")
    return False


def _safe_file_url_media_type(*, content: FileUrl) -> str | None:
    """Return ``content.media_type`` for a FileUrl, or ``None`` when unresolvable.

    ``FileUrl.media_type`` is a pydantic-ai property that may raise
    ``ValueError`` when the MIME type cannot be inferred from the URL and no
    explicit ``media_type`` was supplied (for example ``VideoUrl`` pointing at
    a YouTube-like URL with no extension).
    """
    try:
        return content.media_type
    except ValueError:
        return None


def format_file_url_as_text_lines(*, content: FileUrl) -> list[str]:
    """Format ``FileUrl`` metadata lines for transports that need a text fallback.

    Returns only the descriptive metadata lines (the ``"... URL: <url>"`` line
    plus an optional ``"Media type: ..."`` line). The ``<image>`` / ``<file>``
    placeholder is emitted by :func:`format_file_url_with_placeholder`; this
    helper is exported separately for tests and for callers that already know
    which placeholder to use.
    """
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
    """Build the fallback metadata text for content the transport cannot resolve.

    Used only on the tool-return path. Spec §8.3 requires the rest of a
    successful tool-return payload to be preserved and only the unresolvable
    item replaced with explanatory text. Coding-agent user-prompt admission
    rejects ``UploadedFile`` outright at the backend boundary instead (see
    ``backends/base.py``), so this helper is not reused there.

    ``BinaryContent`` reaches this helper only on transports that cannot carry
    embedded bytes natively (today: MCP, for non-image/non-audio blobs).
    Text-projected backends stage the bytes to a local file rather than fall
    back to this metadata text.
    """
    if isinstance(content, UploadedFile):
        return f"UploadedFile: provider={content.provider_name}, file_id={content.file_id} (not resolvable by {transport_label})"
    return f"BinaryContent: identifier={content.identifier}, media_type={content.media_type} (embedded by this tool; not resolvable by {transport_label})"


def format_file_url_with_placeholder(*, content: FileUrl) -> list[str]:
    """Format ``FileUrl`` text-fallback lines including the ``<image>``/``<file>`` placeholder."""
    placeholder = "<image>" if isinstance(content, ImageUrl) else "<file>"
    return [placeholder, *format_file_url_as_text_lines(content=content)]


def format_uploaded_file_with_placeholder(
    *,
    content: UploadedFile,
    transport_label: str,
) -> list[str]:
    """Format ``UploadedFile`` fallback lines including placeholder.

    Used by text-projected backends (coding-agent backends) that cannot resolve
    provider-owned file references but still want to surface the failure as
    inline content. The MCP transport intentionally bypasses this helper and
    emits only the bare fallback text without a placeholder.
    """
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
    """Format unresolvable ``BinaryContent`` fallback lines including placeholder.

    Used on transports that cannot carry embedded bytes natively (today: MCP,
    for non-image/non-audio blobs). Text-projected backends stage the bytes to
    a local file instead and do not use this helper.
    """
    placeholder = "<image>" if is_image_content(content=content) else "<file>"
    return [placeholder, format_unresolvable_content_text(content=content, transport_label=transport_label)]
