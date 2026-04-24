"""Capture Mode Designer — Mode / Preset / Attachment three-layer model.

Public API:
    * :class:`Mode`, :class:`Preset`, :class:`Attachment`,
      :class:`AttachmentKind` — the data model (``models``).
    * :data:`ATTACHMENT_CATALOG` — the predefined atomic attachments
      (``catalog``).
    * :func:`migrate_legacy_presets` — convert old
      ``screenshot_mode`` + ``screenshot_presets`` into a "Default" Mode
      (``migrations``).
    * :class:`CaptureExecutor` — runs a Preset end-to-end
      (``executor``).

Only the manifest-level symbols are re-exported here; callers should
import concrete handlers from submodules directly.
"""

from .catalog import (
    ATTACHMENT_CATALOG,
    AttachmentSpec,
    catalog_as_list,
    validate_attachments,
)
from .executor import (
    CaptureExecutor,
    ExecutionContext,
    ExecutionReport,
    FrameResult,
)
from .migrations import (
    LEGACY_FIELDS,
    migrate_legacy_presets,
    default_modes,
)
from .models import (
    Attachment,
    AttachmentKind,
    Mode,
    ModesState,
    Preset,
    Source,
    SourceKind,
)
from .templates import (
    ModeTemplate,
    export_mode_as_template,
    get_template,
    list_templates,
    save_user_template,
)

__all__ = [
    "ATTACHMENT_CATALOG",
    "Attachment",
    "AttachmentKind",
    "AttachmentSpec",
    "CaptureExecutor",
    "ExecutionContext",
    "ExecutionReport",
    "FrameResult",
    "LEGACY_FIELDS",
    "Mode",
    "ModeTemplate",
    "ModesState",
    "Preset",
    "Source",
    "SourceKind",
    "catalog_as_list",
    "default_modes",
    "export_mode_as_template",
    "get_template",
    "list_templates",
    "migrate_legacy_presets",
    "save_user_template",
    "validate_attachments",
]
