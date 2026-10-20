"""Pure FFmpeg filter construction for non-destructive per-clip layouts."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CompositionFilter:
    filter_complex: str
    video_map: str
    overlay_inputs: tuple[str, ...]


def _even(value: float, *, minimum: int = 0) -> int:
    rounded = int(round(value))
    rounded -= rounded % 2
    return max(minimum, rounded)


def even_rect(rect: dict, frame_width: int, frame_height: int) -> tuple[int, int, int, int]:
    """Convert a normalized rectangle to clamped even x/y/width/height pixels."""
    if frame_width < 2 or frame_height < 2:
        raise ValueError("Размер кадра слишком мал для композиции")
    x = min(_even(float(rect["x"]) * frame_width), _even(frame_width - 2))
    y = min(_even(float(rect["y"]) * frame_height), _even(frame_height - 2))
    width = min(_even(float(rect["width"]) * frame_width, minimum=2), _even(frame_width - x))
    height = min(_even(float(rect["height"]) * frame_height, minimum=2), _even(frame_height - y))
    if width < 2 or height < 2:
        raise ValueError("Область композиции слишком мала")
    return x, y, width, height


def _fit_filters(width: int, height: int, fit: str) -> list[str]:
    if fit == "stretch":
        return [f"scale={width}:{height}:flags=lanczos"]
    if fit == "contain":
        return [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        ]
    if fit == "cover":
        return [
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
            f"crop={width}:{height}",
        ]
    raise ValueError(f"Неизвестный режим вписывания: {fit}")


def build_composition_filter(
    composition: dict,
    source_width: int,
    source_height: int,
    output_width: int,
    output_height: int,
    banner_input: int | None,
    ad_input: int | None,
    post_filters: list[str],
) -> CompositionFilter:
    """Build one visual graph; source audio is deliberately outside this graph."""
    main = composition["main_layer"]
    crop_x, crop_y, crop_w, crop_h = even_rect(main["crop"], source_width, source_height)
    dst_x, dst_y, dst_w, dst_h = even_rect(main["destination"], output_width, output_height)
    duration = float(composition["source_end"]) - float(composition["source_start"])

    graph = [
        f"color=c=black:s={output_width}x{output_height}:d={duration:.3f}[canvas]",
        ",".join([
            "[0:v]setpts=PTS-STARTPTS",
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
            *_fit_filters(dst_w, dst_h, main.get("fit", "cover")),
            "setsar=1[main]",
        ]),
        f"[canvas][main]overlay=x={dst_x}:y={dst_y}:shortest=1[base0]",
    ]
    current = "base0"
    overlay_inputs: list[str] = []

    def append_overlay(kind: str, layer: dict, input_index: int | None) -> None:
        nonlocal current
        if not layer or not layer.get("enabled", True):
            return
        if kind == "ad" and layer.get("mode") != "overlay":
            return
        if input_index is None:
            raise ValueError(f"Не указан входной файл для слоя {kind}")
        x, y, width, height = even_rect(layer["destination"], output_width, output_height)
        prepared = f"{kind}_video"
        next_label = f"base{len(overlay_inputs) + 1}"
        graph.append(",".join([
            f"[{input_index}:v]setpts=PTS-STARTPTS",
            *_fit_filters(width, height, layer.get("fit", "cover")),
            f"setsar=1[{prepared}]",
        ]))
        graph.append(
            f"[{current}][{prepared}]overlay=x={x}:y={y}:shortest=0:"
            f"enable='between(t,{float(layer['start']):.3f},{float(layer['end']):.3f})'[{next_label}]"
        )
        current = next_label
        overlay_inputs.append(kind)

    append_overlay("banner", composition.get("banner_layer"), banner_input)
    append_overlay("ad", composition.get("ad_layer"), ad_input)

    final_filters = [*post_filters, "format=yuv420p"]
    graph.append(f"[{current}]" + ",".join(final_filters) + "[composed]")
    return CompositionFilter(
        filter_complex=";".join(graph),
        video_map="[composed]",
        overlay_inputs=tuple(overlay_inputs),
    )
