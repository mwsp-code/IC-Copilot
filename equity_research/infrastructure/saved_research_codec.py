from __future__ import annotations

import ast
import base64
import builtins
from functools import lru_cache
import json
import re
import sys
import types
import typing as typing_module
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from ..domain.saved_research import (
    SAVED_RESEARCH_CODEC_VERSION,
    SAVED_RESEARCH_RESULT_SCHEMA_VERSION,
    ArtifactVerificationResult,
    SavedArtifactHealth,
)


class SavedResearchCodecError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason: str = "migration_failed",
        failure_path: str = "",
        annotation: str = "",
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.failure_path = failure_path
        self.annotation = annotation


class _AnnotationResolutionError(TypeError):
    def __init__(
        self,
        message: str,
        *,
        failure_path: str = "",
        annotation: str = "",
    ) -> None:
        super().__init__(message)
        self.failure_path = failure_path
        self.annotation = annotation


_UNION_TYPE = getattr(types, "UnionType", None)


_SECRET_FIELD_TOKENS = {
    "api_key",
    "access_token",
    "auth_token",
    "authorization",
    "credential_lease",
    "credential_lease_id",
    "credential_lease_ids",
    "password",
    "private_key",
    "secret",
    "session_key",
}
_RAW_PROVIDER_FIELDS = {
    "raw_provider_payload",
    "raw_provider_response",
    "request_body",
    "response_body",
}
_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}")
_WINDOWS_USER_PATH = re.compile(
    r"[A-Za-z]:\\Users\\[^\\\r\n\"']+\\[^\r\n\"']+"
)
_POSIX_USER_PATH = re.compile(r"/(?:home|Users)/[^/\r\n\"']+/[^\r\n\"']+")


class JsonResearchResultArtifactCodec:
    """Safe, fixed-root JSON codec for immutable displayable research results."""

    def encode(self, result: Any) -> bytes:
        from ..pipeline import ResearchResult

        if not isinstance(result, ResearchResult):
            raise SavedResearchCodecError("Only ResearchResult artifacts can be saved.")
        payload = _jsonable(result)
        payload = _redact_disallowed_fields(payload)
        if _contains_disallowed_licensed_content(payload):
            raise SavedResearchCodecError(
                "The result contains provider content that is not licensed for persistence."
            )
        serialized = json.dumps(
            {
                "schema_version": SAVED_RESEARCH_RESULT_SCHEMA_VERSION,
                "root_type": "ResearchResult",
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if _TOKEN_PATTERN.search(serialized):
            raise SavedResearchCodecError(
                "A credential-like token was detected; the result was not saved."
            )
        return serialized.encode("utf-8")

    def decode(self, payload: bytes) -> Any:
        from ..pipeline import ResearchResult

        try:
            envelope = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SavedResearchCodecError(
                "Saved research payload is not valid JSON.",
                reason="payload_corrupt",
            ) from exc
        if envelope.get("schema_version") != SAVED_RESEARCH_RESULT_SCHEMA_VERSION:
            raise SavedResearchCodecError(
                "This saved result uses an unsupported result schema.",
                reason="schema_unsupported",
            )
        if envelope.get("root_type") != "ResearchResult":
            raise SavedResearchCodecError(
                "Saved payload has an unexpected root type.",
                reason="schema_unsupported",
            )
        try:
            return _decode_value(envelope.get("payload"), ResearchResult)
        except _AnnotationResolutionError as exc:
            raise SavedResearchCodecError(
                "A saved-result annotation could not be resolved safely.",
                reason="annotation_unresolved",
                failure_path=exc.failure_path,
                annotation=exc.annotation,
            ) from exc
        except (AttributeError, LookupError, TypeError, ValueError) as exc:
            raise SavedResearchCodecError(
                "The saved result could not be migrated to the current research schema.",
                reason="migration_failed",
            ) from exc

    def verify(self, payload: bytes) -> tuple[Any, ArtifactVerificationResult]:
        """Decode on the writer runtime before an artifact is declared durable."""

        restored = self.decode(payload)
        identity = getattr(restored, "identity", None)
        if identity is None or not str(getattr(identity, "ticker", "") or ""):
            raise SavedResearchCodecError(
                "The restored result does not contain a typed security identity.",
                reason="migration_failed",
                failure_path="ResearchResult.identity",
            )
        if getattr(restored, "run_manifest", None) is None:
            raise SavedResearchCodecError(
                "The restored result does not contain a typed run manifest.",
                reason="migration_failed",
                failure_path="ResearchResult.run_manifest",
            )
        return restored, ArtifactVerificationResult(
            status=SavedArtifactHealth.VERIFIED,
            codec_version=SAVED_RESEARCH_CODEC_VERSION,
            writer_runtime=_runtime_label(),
        )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SavedResearchCodecError(
        f"Unsupported saved-research value: {type(value).__name__}."
    )


def _redact_disallowed_fields(value: Any, *, field_name: str = "") -> Any:
    normalized = field_name.casefold()
    if normalized in _SECRET_FIELD_TOKENS or normalized in _RAW_PROVIDER_FIELDS:
        if isinstance(value, dict):
            return {}
        if isinstance(value, (list, tuple, set, frozenset)):
            return []
        return "" if value is not None else None
    if isinstance(value, dict):
        return {
            key: _redact_disallowed_fields(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_disallowed_fields(item) for item in value]
    if isinstance(value, str):
        return _POSIX_USER_PATH.sub(
            "[local path redacted]",
            _WINDOWS_USER_PATH.sub("[local path redacted]", value),
        )
    return value


def _contains_disallowed_licensed_content(
    value: Any,
    *,
    restricted_parent: bool = False,
) -> bool:
    if isinstance(value, dict):
        license_value = str(
            value.get("licensing_policy")
            or value.get("license_policy")
            or ""
        ).casefold()
        restricted = restricted_parent or any(
            token in license_value
            for token in ("do_not_store", "no_storage", "session_only_content")
        )
        has_payload = any(
            value.get(key) not in (None, "", [], {})
            for key in ("content", "full_text", "document_text", "raw_text")
        )
        if restricted and has_payload:
            return True
        return any(
            _contains_disallowed_licensed_content(
                item,
                restricted_parent=restricted,
            )
            for item in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_disallowed_licensed_content(
                item,
                restricted_parent=restricted_parent,
            )
            for item in value
        )
    return False


def _decode_value(value: Any, annotation: Any) -> Any:
    if annotation in (Any, object) or annotation is None:
        return value
    if isinstance(annotation, str):
        raise _AnnotationResolutionError(
            f"Unresolved saved-research annotation: {annotation}.",
            annotation=annotation,
        )
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Union or (_UNION_TYPE is not None and origin is _UNION_TYPE):
        if value is None and type(None) in args:
            return None
        errors = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _decode_value(value, option)
            except (TypeError, ValueError, KeyError) as exc:
                errors.append(exc)
        raise TypeError(f"Value does not match union {annotation!r}: {errors!r}")
    if origin is Literal:
        if value not in args:
            raise ValueError(f"Unexpected literal {value!r}")
        return value
    if origin in (list, Sequence):
        item_type = args[0] if args else Any
        return [_decode_value(item, item_type) for item in (value or [])]
    if origin is tuple:
        values = list(value or [])
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_decode_value(item, args[0]) for item in values)
        return tuple(
            _decode_value(item, args[index] if index < len(args) else Any)
            for index, item in enumerate(values)
        )
    if origin in (set, frozenset):
        item_type = args[0] if args else Any
        decoded = (_decode_value(item, item_type) for item in (value or []))
        return origin(decoded)
    if origin in (dict, Mapping):
        key_type, item_type = args if len(args) == 2 else (Any, Any)
        return {
            _decode_value(key, key_type): _decode_value(item, item_type)
            for key, item in (value or {}).items()
        }
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation is datetime:
        return datetime.fromisoformat(value)
    if annotation is date:
        return date.fromisoformat(value)
    if annotation is Path:
        return Path(value)
    if annotation is bytes:
        if isinstance(value, dict) and "__bytes__" in value:
            return base64.b64decode(value["__bytes__"])
        raise TypeError("Expected encoded bytes.")
    if isinstance(annotation, type) and annotation.__name__ == "HybridResearchRun":
        from ..hybrid_research import hybrid_run_from_payload

        return hybrid_run_from_payload(dict(value or {}))
    if isinstance(annotation, type) and annotation.__name__ == "DualModelResearchRun":
        from ..dual_model_benchmark import dual_model_run_from_payload

        return dual_model_run_from_payload(dict(value or {}))
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, dict):
            raise TypeError(f"Expected object for {annotation.__name__}.")
        hints = _resolved_type_hints(annotation)
        kwargs: dict[str, Any] = {}
        for item in fields(annotation):
            if not item.init:
                continue
            if item.name in value:
                kwargs[item.name] = _decode_value(
                    value[item.name], hints.get(item.name, Any)
                )
            elif item.default is MISSING and item.default_factory is MISSING:
                raise KeyError(f"Missing required field {annotation.__name__}.{item.name}")
        return annotation(**kwargs)
    if annotation in (str, int, float, bool):
        if value is None:
            return None
        return annotation(value)
    return value


@lru_cache(maxsize=None)
def _resolved_type_hints(annotation: type) -> dict[str, Any]:
    """Resolve postponed annotations without requiring PEP 604 at runtime."""

    module = sys.modules.get(annotation.__module__)
    module_globals = dict(vars(module)) if module is not None else {}
    local_names = {**module_globals, annotation.__name__: annotation}
    try:
        hints = get_type_hints(
            annotation,
            globalns=module_globals,
            localns=local_names,
        )
        return {
            name: _fully_resolve_annotation(
                value,
                module_globals=module_globals,
                local_names=local_names,
            )
            for name, value in hints.items()
        }
    except (AttributeError, NameError, TypeError):
        pass
    resolved: dict[str, Any] = {}
    for name, value in dict(getattr(annotation, "__annotations__", {}) or {}).items():
        if not isinstance(value, str):
            resolved[name] = value
            continue
        try:
            expression = ast.parse(value, mode="eval").body
            resolved[name] = _fully_resolve_annotation(
                _resolve_annotation_expression(
                    expression,
                    module_globals=module_globals,
                    local_names=local_names,
                ),
                module_globals=module_globals,
                local_names=local_names,
            )
        except (AttributeError, KeyError, SyntaxError, TypeError, ValueError) as exc:
            raise _AnnotationResolutionError(
                f"Could not resolve {annotation.__name__}.{name}: {value}.",
                failure_path=f"{annotation.__module__}.{annotation.__name__}.{name}",
                annotation=value,
            ) from exc
    return resolved


def _fully_resolve_annotation(
    annotation: Any,
    *,
    module_globals: dict[str, Any],
    local_names: dict[str, Any],
) -> Any:
    """Resolve quoted generic arguments that Python 3.9 leaves as strings."""

    if isinstance(annotation, str):
        return _resolve_annotation_expression(
            ast.parse(annotation, mode="eval").body,
            module_globals=module_globals,
            local_names=local_names,
        )
    forward_ref = getattr(typing_module, "ForwardRef", None)
    if forward_ref is not None and isinstance(annotation, forward_ref):
        return _fully_resolve_annotation(
            annotation.__forward_arg__,
            module_globals=module_globals,
            local_names=local_names,
        )
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is None or not arguments:
        return annotation
    # Literal string arguments are values, not forward type references.
    if origin is Literal:
        return annotation
    resolved_arguments = tuple(
        _fully_resolve_annotation(
            item,
            module_globals=module_globals,
            local_names=local_names,
        )
        for item in arguments
    )
    if resolved_arguments == arguments:
        return annotation
    if origin is Union:
        return Union[resolved_arguments]
    if origin is Literal:
        return Literal[resolved_arguments]
    copy_with = getattr(annotation, "copy_with", None)
    if callable(copy_with):
        return copy_with(resolved_arguments)
    try:
        subscription = (
            resolved_arguments[0]
            if len(resolved_arguments) == 1
            else resolved_arguments
        )
        return origin[subscription]
    except (AttributeError, TypeError) as exc:
        raise _AnnotationResolutionError(
            f"Could not rebuild resolved annotation {annotation!r}.",
            annotation=repr(annotation),
        ) from exc


def _resolve_annotation_expression(
    node: ast.AST,
    *,
    module_globals: dict[str, Any],
    local_names: dict[str, Any],
) -> Any:
    if isinstance(node, ast.Name):
        if node.id in local_names:
            return local_names[node.id]
        if node.id in module_globals:
            return module_globals[node.id]
        if hasattr(typing_module, node.id):
            return getattr(typing_module, node.id)
        if hasattr(builtins, node.id):
            return getattr(builtins, node.id)
        if node.id == "NoneType":
            return type(None)
        raise KeyError(node.id)
    if isinstance(node, ast.Constant):
        if node.value is None:
            return type(None)
        if node.value is Ellipsis:
            return Ellipsis
        if isinstance(node.value, str):
            nested = ast.parse(node.value, mode="eval").body
            return _resolve_annotation_expression(
                nested,
                module_globals=module_globals,
                local_names=local_names,
            )
        return node.value
    if isinstance(node, ast.Attribute):
        owner = _resolve_annotation_expression(
            node.value,
            module_globals=module_globals,
            local_names=local_names,
        )
        return getattr(owner, node.attr)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = _resolve_annotation_expression(
            node.left,
            module_globals=module_globals,
            local_names=local_names,
        )
        right = _resolve_annotation_expression(
            node.right,
            module_globals=module_globals,
            local_names=local_names,
        )
        return Union[left, right]
    if isinstance(node, ast.Subscript):
        generic = _resolve_annotation_expression(
            node.value,
            module_globals=module_globals,
            local_names=local_names,
        )
        slice_node = node.slice
        if hasattr(ast, "Index") and isinstance(slice_node, ast.Index):
            slice_node = slice_node.value
        if generic is Literal:
            literal_nodes = (
                tuple(slice_node.elts)
                if isinstance(slice_node, ast.Tuple)
                else (slice_node,)
            )
            literal_values = tuple(
                _resolve_literal_value(item)
                for item in literal_nodes
            )
            return Literal[literal_values]
        parameters = _resolve_annotation_expression(
            slice_node,
            module_globals=module_globals,
            local_names=local_names,
        )
        try:
            return generic[parameters]
        except (AttributeError, TypeError) as exc:
            compatibility_aliases = {
                list: typing_module.List,
                dict: typing_module.Dict,
                tuple: typing_module.Tuple,
                set: typing_module.Set,
                frozenset: typing_module.FrozenSet,
                type: typing_module.Type,
            }
            alias = compatibility_aliases.get(generic)
            if alias is None:
                raise exc
            return alias[parameters]
    if isinstance(node, ast.Tuple):
        return tuple(
            _resolve_annotation_expression(
                item,
                module_globals=module_globals,
                local_names=local_names,
            )
            for item in node.elts
        )
    if isinstance(node, ast.List):
        return [
            _resolve_annotation_expression(
                item,
                module_globals=module_globals,
                local_names=local_names,
            )
            for item in node.elts
        ]
    raise TypeError(f"Unsupported annotation expression: {type(node).__name__}.")


def _resolve_literal_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _resolve_literal_value(node.operand)
        if isinstance(value, (int, float)):
            return -value
    raise TypeError("Literal annotations may contain constants only.")


def _runtime_label() -> str:
    return f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
