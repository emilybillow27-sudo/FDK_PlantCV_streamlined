"""Portable frozen FDK pipeline for spaced wheat kernels on a white background.

Usage:
    python fdk_pipeline.py check
    python fdk_pipeline.py score --image data/raw/SDSU_validation/IMG_0468.jpg --sample-id SDSU_IMG_0468
    python fdk_pipeline.py score-folder --images-dir data/raw/SDSU_validation --site SDSU

Results live inside data/output/. The red/green image outlines detected masks.
Automatic scores include every retained detection; non-wheat flags are review cues only.
"""
import argparse
import csv
import hashlib
import json
import os
import warnings
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from PIL import Image, ImageOps
from skimage.color import rgb2lab
from skimage.measure import regionprops
from scipy.ndimage import label as connected_components

warnings.filterwarnings("ignore", message=".*major_axis_length.*deprecated.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*minor_axis_length.*deprecated.*", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
QC_MODEL_PATH = DATA / "models" / "segmentation_qc_v2.joblib"
FDK_MODEL_PATH = DATA / "models" / "fdk_classifier_468_labels.joblib"
YOLO_WEIGHTS = DATA / "models" / "best.pt"
WEED_MODEL_PATH = DATA / "models" / "weed_gate_sdsu_candidate.joblib"
SEGMENTATION_VERSION = "larger_repair_component_cleanup_v1"
REPAIR_MAX_AREA_RATIO = 2.50
CLEANUP_MIN_MAIN_FRACTION = 0.95
IOU_THRESHOLD = 0.40
CONTAINMENT_THRESHOLD = 0.80
QC_THRESHOLD = 0.975
BACKGROUND_B_MEDIAN_THRESHOLD = 5.0
TILE_SIZE = 512
STRIDE = 384
MIN_STRAIGHT_EDGE = 8
OUTLINE_WIDTH = 5
QC_FEATURES = """confidence area_px perimeter_px major_axis_px minor_axis_px aspect_ratio eccentricity solidity extent equivalent_diameter_px L_q10 L_q25 L_median L_q75 L_q90 L_iqr L_q80range a_q10 a_q25 a_median a_q75 a_q90 a_iqr a_q80range b_q10 b_q25 b_median b_q75 b_q90 b_iqr b_q80range""".split()
SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def load_and_crop_image(image_path):

    img = Image.open(image_path)

    img = ImageOps.exif_transpose(img)

    img = img.convert("RGB")

    img = np.array(img)

    height, width = img.shape[:2]

    x1 = int(width * 0.05)
    x2 = int(width * 0.95)

    y1 = int(height * 0.05)
    y2 = int(height * 0.95)

    return img[y1:y2, x1:x2]


def bbox_overlap(a, b):

    ax1, ay1, ax2, ay2 = a["bbox"]
    bx1, by1, bx2, by2 = b["bbox"]

    if ax2 <= bx1:
        return False

    if bx2 <= ax1:
        return False

    if ay2 <= by1:
        return False

    if by2 <= ay1:
        return False

    return True


def mask_overlap_stats(mask_a, mask_b):

    intersection = np.logical_and(mask_a, mask_b).sum()

    if intersection == 0:

        return 0.0, 0.0

    area_a = mask_a.sum()
    area_b = mask_b.sum()

    union = area_a + area_b - intersection

    if union == 0:

        iou = 0.0

    else:

        iou = intersection / union

    smaller_area = min(area_a, area_b)

    if smaller_area == 0:

        containment = 0.0

    else:

        containment = intersection / smaller_area

    return float(iou), float(containment)


def deduplicate_iou_only(detections):

    order = sorted(
        range(len(detections)),
        key=lambda i: detections[i]["confidence"],
        reverse=True,
    )

    retained_indices = []

    for idx in order:

        candidate = detections[idx]

        duplicate = False

        for kept_idx in retained_indices:

            kept = detections[kept_idx]

            if candidate["tile_id"] == kept["tile_id"]:
                continue

            if not bbox_overlap(candidate, kept):
                continue

            iou, _ = mask_overlap_stats(
                candidate["mask"],
                kept["mask"],
            )

            if iou > IOU_THRESHOLD:

                duplicate = True
                break

        if not duplicate:

            retained_indices.append(idx)

    return [
        detections[i]
        for i in retained_indices
    ]


def deduplicate_containment_larger_mask(detections):

    detections = list(detections)

    for det in detections:

        det["_mask_area"] = int(det["mask"].sum())

    order = sorted(
        range(len(detections)),
        key=lambda i: detections[i]["_mask_area"],
        reverse=True,
    )

    retained_indices = []

    removed_records = []

    for idx in order:

        candidate = detections[idx]

        duplicate = False

        for kept_idx in retained_indices:

            kept = detections[kept_idx]

            if candidate["tile_id"] == kept["tile_id"]:
                continue

            if not bbox_overlap(candidate, kept):
                continue

            iou, containment = mask_overlap_stats(
                candidate["mask"],
                kept["mask"],
            )

            # IoU duplicates should already have been handled by
            # Stage 1. We only use the new containment relation here.

            if (
                iou <= IOU_THRESHOLD
                and containment >= CONTAINMENT_THRESHOLD
            ):

                duplicate = True

                removed_records.append({
                    "removed_tile": candidate["tile_id"],
                    "removed_confidence": candidate["confidence"],
                    "removed_area": candidate["_mask_area"],
                    "retained_tile": kept["tile_id"],
                    "retained_confidence": kept["confidence"],
                    "retained_area": kept["_mask_area"],
                    "iou": iou,
                    "containment": containment,
                })

                break

        if not duplicate:

            retained_indices.append(idx)

    retained = [
        detections[i]
        for i in retained_indices
    ]

    for det in retained:

        det.pop("_mask_area", None)

    for det in detections:

        det.pop("_mask_area", None)

    return retained, removed_records


def extract_morphology(mask):

    props = regionprops(
        mask.astype(np.uint8)
    )

    if len(props) == 0:

        raise ValueError(
            "Empty mask encountered during morphology extraction."
        )

    p = props[0]

    area_px = float(p.area)

    perimeter_px = float(p.perimeter)

    major_axis_px = float(p.major_axis_length)

    minor_axis_px = float(p.minor_axis_length)

    if minor_axis_px > 0:

        aspect_ratio = major_axis_px / minor_axis_px

    else:

        aspect_ratio = np.nan

    eccentricity = float(p.eccentricity)

    solidity = float(p.solidity)

    extent = float(p.extent)

    equivalent_diameter_px = float(
        p.equivalent_diameter_area
    )

    return {
        "area_px": area_px,
        "perimeter_px": perimeter_px,
        "major_axis_px": major_axis_px,
        "minor_axis_px": minor_axis_px,
        "aspect_ratio": aspect_ratio,
        "eccentricity": eccentricity,
        "solidity": solidity,
        "extent": extent,
        "equivalent_diameter_px": equivalent_diameter_px,
    }


def summarize_channel(values, prefix):

    q10 = float(np.quantile(values, 0.10))
    q25 = float(np.quantile(values, 0.25))
    median = float(np.quantile(values, 0.50))
    q75 = float(np.quantile(values, 0.75))
    q90 = float(np.quantile(values, 0.90))

    return {
        f"{prefix}_q10": q10,
        f"{prefix}_q25": q25,
        f"{prefix}_median": median,
        f"{prefix}_q75": q75,
        f"{prefix}_q90": q90,
        f"{prefix}_iqr": q75 - q25,
        f"{prefix}_q80range": q90 - q10,
    }


def extract_lab_features(lab_image, mask):

    pixels = lab_image[mask]

    if pixels.shape[0] == 0:

        raise ValueError(
            "Empty mask encountered during LAB extraction."
        )

    features = {}

    features.update(
        summarize_channel(
            pixels[:, 0],
            "L",
        )
    )

    features.update(
        summarize_channel(
            pixels[:, 1],
            "a",
        )
    )

    features.update(
        summarize_channel(
            pixels[:, 2],
            "b",
        )
    )

    return features


def build_qc_features(det, lab_image):

    record = {
        "confidence": float(det["confidence"])
    }

    record.update(
        extract_morphology(
            det["mask"]
        )
    )

    record.update(
        extract_lab_features(
            lab_image,
            det["mask"],
        )
    )

    missing = [
        feature
        for feature in QC_FEATURES
        if feature not in record
    ]

    if missing:

        raise KeyError(
            f"Missing QC features: {missing}"
        )

    return record


def load_qc_bundle():

    if not QC_MODEL_PATH.exists():

        raise FileNotFoundError(
            f"Missing frozen QC model:\n{QC_MODEL_PATH}"
        )

    bundle = joblib.load(
        QC_MODEL_PATH
    )

    if not isinstance(bundle, dict):

        raise TypeError(
            "Expected QC joblib file to contain a dictionary bundle."
        )

    if "model" not in bundle:

        raise KeyError(
            f"'model' missing from QC bundle. "
            f"Keys: {sorted(bundle.keys())}"
        )

    model = bundle["model"]

    saved_features = bundle.get(
        "features",
        None,
    )

    saved_threshold = bundle.get(
        "threshold",
        None,
    )

    if saved_features is not None:

        if list(saved_features) != QC_FEATURES:

            raise ValueError(
                "QC feature order does not match the frozen "
                "31-feature specification."
            )

    if saved_threshold is not None:

        if not np.isclose(
            float(saved_threshold),
            QC_THRESHOLD,
        ):

            raise ValueError(
                f"QC threshold mismatch: "
                f"bundle={saved_threshold}, "
                f"script={QC_THRESHOLD}"
            )

    return model


def apply_qc_and_background_gate(
    detections,
    lab_image,
    qc_model,
):

    retained = []

    qc_rejected = []

    background_rejected = []

    all_records = []

    for detection_number, det in enumerate(
        detections,
        start=1,
    ):

        features = build_qc_features(
            det,
            lab_image,
        )

        X = np.array(
            [[features[name] for name in QC_FEATURES]],
            dtype=float,
        )

        probabilities = qc_model.predict_proba(X)[0]

        if len(probabilities) != 2:

            raise ValueError(
                "Frozen QC model did not return binary probabilities."
            )

        # The frozen QC model was trained with invalid = 1.
        # Therefore class 1 probability is P(invalid).

        classes = list(qc_model.classes_)

        if 1 not in classes:

            raise ValueError(
                f"Expected QC model class 1 = invalid. "
                f"Classes are {classes}"
            )

        invalid_index = classes.index(1)

        p_invalid = float(
            probabilities[invalid_index]
        )

        b_median = float(
            features["b_median"]
        )

        record = {
            "detection_number": detection_number,
            "tile_id": det["tile_id"],
            "confidence": float(det["confidence"]),
            "area_px": float(features["area_px"]),
            "b_median": b_median,
            "p_invalid": p_invalid,
        }

        if p_invalid >= QC_THRESHOLD:

            record["decision"] = "QC_EXCLUDED"

            qc_rejected.append(
                (det, record)
            )

        elif b_median < BACKGROUND_B_MEDIAN_THRESHOLD:

            record["decision"] = "BACKGROUND_EXCLUDED"

            background_rejected.append(
                (det, record)
            )

        else:

            record["decision"] = "RETAINED"

            retained.append(
                (det, record)
            )

        all_records.append(record)

    return (
        retained,
        qc_rejected,
        background_rejected,
        all_records,
    )

def tile_starts(length):
    if length <= TILE_SIZE:
        return [0]
    starts = list(range(0, length - TILE_SIZE + 1, STRIDE))
    if starts[-1] != length - TILE_SIZE:
        starts.append(length - TILE_SIZE)
    return starts


def border_cuts(det, start_x, start_y, image_shape):
    """Only count mask pixels on a real INTERNAL 512-pixel tile border."""
    x1, y1, x2, y2 = map(int, det["bbox"])
    height, width = image_shape[:2]
    mask = det["mask"]
    checks = (
        ("left", x1, start_x, start_x > 0, mask[y1:y2, x1]),
        ("right", x2, start_x + TILE_SIZE, start_x + TILE_SIZE < width,
         mask[y1:y2, x2 - 1]),
        ("top", y1, start_y, start_y > 0, mask[y1, x1:x2]),
        ("bottom", y2, start_y + TILE_SIZE, start_y + TILE_SIZE < height,
         mask[y2 - 1, x1:x2]),
    )
    return [
        name for name, coordinate, edge, internal, pixels in checks
        if internal and abs(coordinate - edge) <= 1
        and np.count_nonzero(pixels) >= MIN_STRAIGHT_EDGE
    ]


def bbox_and_mask_overlap(a, b):
    ax1, ay1, ax2, ay2 = map(int, a["bbox"])
    bx1, by1, bx2, by2 = map(int, b["bbox"])
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x1 >= x2 or y1 >= y2:
        return 0.0, 0.0
    common = np.count_nonzero(
        a["mask"][y1:y2, x1:x2] & b["mask"][y1:y2, x1:x2]
    )
    if not common:
        return 0.0, 0.0
    area_a, area_b = int(a["area"]), int(b["area"])
    return common / (area_a + area_b - common), common / area_a


def moved_past_border(candidate, old_x, old_y, old_edges):
    nx1, ny1, nx2, ny2 = map(int, candidate["bbox"])
    return any((
        name == "left" and nx1 <= old_x - 5
        or name == "right" and nx2 >= old_x + TILE_SIZE + 5
        or name == "top" and ny1 <= old_y - 5
        or name == "bottom" and ny2 >= old_y + TILE_SIZE + 5
    ) for name in old_edges)


def centered_tile(old, image_shape):
    x1, y1, x2, y2 = map(int, old["bbox"])
    height, width = image_shape[:2]
    if x2 - x1 > TILE_SIZE - 32 or y2 - y1 > TILE_SIZE - 32:
        return None
    x0 = max(0, min(width - TILE_SIZE, (x1 + x2) // 2 - TILE_SIZE // 2))
    y0 = max(0, min(height - TILE_SIZE, (y1 + y2) // 2 - TILE_SIZE // 2))
    if (
        x1 - x0 < 16 or x0 + TILE_SIZE - x2 < 16
        or y1 - y0 < 16 or y0 + TILE_SIZE - y2 < 16
    ):
        return None
    return x0, y0


def offset_tile(center, image_shape, edges):
    """Shift one more tile toward the side originally truncated."""
    x0, y0 = center
    height, width = image_shape[:2]
    direction = edges[0]
    shifts = {"left": (-96, 0), "right": (96, 0),
              "top": (0, -96), "bottom": (0, 96)}
    dx, dy = shifts[direction]
    shifted = (max(0, min(width - TILE_SIZE, x0 + dx)),
               max(0, min(height - TILE_SIZE, y0 + dy)))
    return shifted if shifted != center else None


def infer_tile(model, rgb, x0, y0, image_shape, device, old):
    tile_bgr = rgb[y0:y0 + TILE_SIZE, x0:x0 + TILE_SIZE, ::-1].copy()
    result = model.predict(
        source=tile_bgr, imgsz=TILE_SIZE, conf=0.10, iou=0.30,
        max_det=300, retina_masks=True, device=device, verbose=False,
    )[0]
    if result.boxes is None or result.masks is None:
        return []
    probabilities = result.boxes.conf.detach().cpu().numpy()
    masks = result.masks.data.detach().cpu().numpy() > 0.5
    candidates = []
    old_x1, old_y1, old_x2, old_y2 = map(int, old["bbox"])
    for local, confidence in zip(masks, probabilities):
        if local.shape != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"Unexpected YOLO mask shape: {local.shape}")
        yy, xx = np.nonzero(local)
        if len(xx) == 0:
            continue
        x1, x2 = x0 + int(xx.min()), x0 + int(xx.max()) + 1
        y1, y2 = y0 + int(yy.min()), y0 + int(yy.max()) + 1
        area = len(xx)
        if (x2 <= old_x1 or x1 >= old_x2 or y2 <= old_y1 or y1 >= old_y2
                or not 0.75 <= area / old["area"] <= REPAIR_MAX_AREA_RATIO):
            continue
        full = np.zeros(image_shape[:2], dtype=bool)
        full[y0:y0 + TILE_SIZE, x0:x0 + TILE_SIZE] = local
        candidates.append({
            "tile_id": f"centered_x{x0}_y{y0}",
            "confidence": float(confidence), "area": area,
            "bbox": (x1, y1, x2, y2), "mask": full,
        })
    return candidates


def tile_origin(det, xs, ys):
    col, row = int(det["tile_col"]) - 1, int(det["tile_row"]) - 1
    if not (0 <= col < len(xs) and 0 <= row < len(ys)):
        raise ValueError(f"Invalid one-based tile index: {det['tile_id']}")
    return xs[col], ys[row]


def check_candidate(old, candidate, candidate_pos, original_pos, edges,
                    revised, index, image_shape, lab, qc_model, frozen):
    if not 0.75 <= candidate["area"] / old["area"] <= REPAIR_MAX_AREA_RATIO:
        return "area", 0.0, 0.0
    iou, coverage = bbox_and_mask_overlap(old, candidate)
    if iou < 0.40:
        return "mask_iou", iou, coverage
    minimum_coverage = 0.85 if candidate["area"] / old["area"] > 1.60 else 0.65
    if coverage < minimum_coverage:
        return "old_coverage", iou, coverage
    if border_cuts(candidate, *candidate_pos, image_shape):
        return "candidate_still_cut", iou, coverage
    if not moved_past_border(candidate, *original_pos, edges):
        return "no_extension", iou, coverage
    if any(bbox_and_mask_overlap(candidate, other)[0] > 0.40
           for j, other in enumerate(revised) if j != index):
        return "neighbor_overlap", iou, coverage
    passed, rejected, background, _ = apply_qc_and_background_gate(
        [candidate], lab, qc_model
    )
    if not passed:
        return ("qc_excluded" if rejected else "background_excluded"), iou, coverage
    return "accepted", iou, coverage


def candidate_results(old, candidates, position, original_pos, edges,
                      revised, index, image_shape, lab, qc_model, frozen):
    reasons = Counter()
    choices = []
    for candidate in candidates:
        reason, iou, coverage = check_candidate(
            old, candidate, position(candidate), original_pos, edges,
            revised, index, image_shape, lab, qc_model, frozen
        )
        reasons[reason] += 1
        if reason == "accepted":
            choices.append((iou, coverage, candidate))
    return reasons, max(choices, key=lambda item: (
        item[0], item[1], item[2]["confidence"]
    )) if choices else None

def pack_masks(detections, image_shape, archive):
    pixels = []
    offsets = [0]
    bboxes = []
    for detection in detections:
        x1, y1, x2, y2 = map(int, detection["bbox"])
        crop = detection["mask"][y1:y2, x1:x2]
        if not crop.any() or int(crop.sum()) != int(detection["area"]):
            raise ValueError("Detection mask does not match its area")
        packed = np.packbits(crop.ravel()).astype(np.uint8)
        pixels.append(packed)
        offsets.append(offsets[-1] + len(packed))
        bboxes.append((x1, y1, x2, y2))
    np.savez_compressed(archive, pixels=np.concatenate(pixels),
                        offsets=np.asarray(offsets, dtype=np.int64),
                        bboxes=np.asarray(bboxes, dtype=np.int32),
                        image_shape=np.asarray(image_shape[:2], dtype=np.int32))


def restore_mask(archive, index):
    if index < 0:
        return None  # Manually added kernel.
    x1, y1, x2, y2 = map(int, archive["bboxes"][index])
    start, end = map(int, archive["offsets"][index:index + 2])
    crop = np.unpackbits(archive["pixels"][start:end])[:(x2-x1)*(y2-y1)]
    return crop.reshape((y2-y1, x2-x1)).astype(bool)


def detect_raw(model, rgb, repair, device):
    height, width = rgb.shape[:2]
    xs, ys = tile_starts(width), tile_starts(height)
    output = []
    for row, y0 in enumerate(ys, 1):
        for col, x0 in enumerate(xs, 1):
            local_rgb = rgb[y0:y0+512, x0:x0+512]
            tile = local_rgb[:, :, ::-1].copy()  # Ultralytics NumPy input is BGR.
            pred = model.predict(source=tile, imgsz=512, conf=.10, iou=.30,
                                 max_det=300, retina_masks=True, device=device,
                                 verbose=False)[0]
            if pred.boxes is None or pred.masks is None:
                continue
            masks = pred.masks.data.detach().cpu().numpy() > .5
            scores = pred.boxes.conf.detach().cpu().numpy()
            for mask, score in zip(masks, scores):
                if mask.shape != local_rgb.shape[:2]:
                    raise ValueError(f"Unexpected tile mask shape {mask.shape}")
                yy, xx = np.nonzero(mask)
                if not len(xx):
                    continue
                full = np.zeros((height, width), dtype=bool)
                full[y0:y0+mask.shape[0], x0:x0+mask.shape[1]] = mask
                output.append({"tile_id": f"r{row}_c{col}",
                               "tile_row": row, "tile_col": col,
                               "confidence": float(score), "area": len(xx),
                               "bbox": (int(x0+xx.min()), int(y0+yy.min()),
                                        int(x0+xx.max()+1), int(y0+yy.max()+1)),
                               "mask": full})
        print(f"YOLO row {row}/{len(ys)}: {len(output)} raw detections", flush=True)
    return output


def repair_masks(baseline, raw, rgb, lab, qc_model, frozen, repair, model, device):
    revised = list(baseline)
    changed = {}
    xs, ys = tile_starts(rgb.shape[1]), tile_starts(rgb.shape[0])
    edge_candidates = sum(bool(border_cuts(det, *tile_origin(det, xs, ys), rgb.shape))
                          for det in baseline)
    print(f"Tile-edge repair: {edge_candidates} possible cut masks to inspect", flush=True)
    checked = 0
    for index, old in enumerate(baseline):
        old_x, old_y = tile_origin(old, xs, ys)
        edges = border_cuts(old, old_x, old_y, rgb.shape)
        if not edges:
            continue
        checked += 1
        print(f"Tile-edge repair {checked}/{edge_candidates}, retained mask {index + 1}/{len(baseline)}", flush=True)
        pos = centered_tile(old, rgb.shape)
        if pos is None:
            continue
        x0, y0 = pos
        candidates = infer_tile(model, rgb, x0, y0, rgb.shape, device, old)
        _, choice = candidate_results(
            old, candidates, lambda _: pos, (old_x, old_y), edges, revised,
            index, rgb.shape, lab, qc_model, frozen)
        source = "centered"
        if choice is None:
            ox1, oy1, ox2, oy2 = map(int, old["bbox"])
            cached = [d for d in raw if d is not old
                      and d["tile_id"] != old["tile_id"]
                      and d["bbox"][0] < ox2 and d["bbox"][2] > ox1
                      and d["bbox"][1] < oy2 and d["bbox"][3] > oy1]
            _, choice = candidate_results(
                old, cached, lambda d: tile_origin(d, xs, ys),
                (old_x, old_y), edges, revised, index, rgb.shape,
                lab, qc_model, frozen)
            source = "cached"
        if choice is None:
            offset = offset_tile(pos, rgb.shape, edges)
            if offset is not None:
                ox1, oy1, ox2, oy2 = map(int, old["bbox"])
                sx, sy = offset
                if (ox1-sx >= 16 and sx+512-ox2 >= 16
                        and oy1-sy >= 16 and sy+512-oy2 >= 16):
                    candidates = infer_tile(model, rgb, sx, sy, rgb.shape, device, old)
                    _, choice = candidate_results(
                        old, candidates, lambda _: offset, (old_x, old_y), edges,
                        revised, index, rgb.shape, lab, qc_model, frozen)
                    source = "offset"
        if choice is not None:
            revised[index] = choice[2]
            changed[index] = source
    return revised, changed



def cleanup_mask_components(detections):
    """Remove small disconnected islands without removing or reordering detections."""
    revised, changes = [], []
    for index, det in enumerate(detections):
        x1, y1, x2, y2 = map(int, det["bbox"])
        local = det["mask"][y1:y2, x1:x2]
        components, count = connected_components(local, structure=np.ones((3, 3)))
        sizes = np.bincount(components.ravel())
        sizes[0] = 0
        area = int(local.sum())
        if not area:
            raise ValueError("Cannot clean an empty retained mask")
        largest = int(sizes.argmax())
        fraction = int(sizes[largest]) / area
        if count <= 1 or fraction < CLEANUP_MIN_MAIN_FRACTION:
            revised.append(det)
            continue
        main = components == largest
        yy, xx = np.nonzero(main)
        cleaned = np.zeros_like(det["mask"], dtype=bool)
        cleaned[y1:y2, x1:x2] = main
        bbox = (x1 + int(xx.min()), y1 + int(yy.min()),
                x1 + int(xx.max()) + 1, y1 + int(yy.max()) + 1)
        revised.append({**det, "mask": cleaned, "bbox": bbox, "area": int(main.sum())})
        changes.append({"index": index, "removed_pixels": area - int(main.sum()),
                        "largest_component_fraction": fraction})
    return revised, changes


def file_sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def check_models():
    for path in (YOLO_WEIGHTS, QC_MODEL_PATH, FDK_MODEL_PATH):
        if not path.is_file():
            raise FileNotFoundError(f"Required frozen asset missing: {path}")
    qc = joblib.load(QC_MODEL_PATH)
    fdk = joblib.load(FDK_MODEL_PATH)
    if list(fdk["features"]) != QC_FEATURES[1:]:
        raise ValueError("FDK feature order differs from the frozen 30 features")
    if not np.isclose(float(fdk["threshold"]), .5):
        raise ValueError("FDK threshold differs from the frozen 0.5")
    load_qc_bundle()
    print("Frozen YOLO, QC and FDK model assets are present; feature orders match.")
    return qc, fdk


def boundary(mask):
    from scipy.ndimage import binary_erosion
    return mask & ~binary_erosion(
        mask,
        iterations=OUTLINE_WIDTH,
    )


def render_stage(rgb, detections, path, color=(40, 120, 220)):
    """Save one-color mask outlines for an intermediate pipeline stage."""
    canvas = rgb.copy()

    for det in detections:
        x1, y1, x2, y2 = map(int, det["bbox"])
        local_mask = det["mask"][y1:y2, x1:x2]
        canvas[y1:y2, x1:x2][boundary(local_mask)] = color

    Image.fromarray(canvas).save(path)


def render_tile_grid(rgb, path):
    """Save the cropped input with the overlapping inference tiles shown."""
    from PIL import ImageDraw

    canvas = Image.fromarray(rgb.copy())
    draw = ImageDraw.Draw(canvas)
    height, width = rgb.shape[:2]

    for y0 in tile_starts(height):
        for x0 in tile_starts(width):
            draw.rectangle(
                (
                    x0,
                    y0,
                    min(x0 + TILE_SIZE - 1, width - 1),
                    min(y0 + TILE_SIZE - 1, height - 1),
                ),
                outline=(40, 120, 220),
                width=3,
            )

    canvas.save(path)


def render_qc_stage(
    rgb,
    retained,
    qc_rejected,
    background_rejected,
    path,
):
    """Save QC decisions: green retained, magenta QC, orange background."""
    canvas = rgb.copy()
    groups = (
        (retained, (30, 210, 50)),
        (qc_rejected, (220, 40, 180)),
        (background_rejected, (255, 150, 20)),
    )

    for records, color in groups:
        for det, _ in records:
            x1, y1, x2, y2 = map(int, det["bbox"])
            local_mask = det["mask"][y1:y2, x1:x2]
            canvas[y1:y2, x1:x2][boundary(local_mask)] = color

    Image.fromarray(canvas).save(path)


def render_overlay(rgb, detections, predictions, path):
    canvas = rgb.copy()
    for det, is_fdk in zip(detections, predictions):
        x1, y1, x2, y2 = map(int, det["bbox"])
        local_mask = det["mask"][y1:y2, x1:x2]
        canvas[y1:y2, x1:x2][boundary(local_mask)] = (
            (235, 20, 30) if is_fdk else (30, 210, 50)
        )
    Image.fromarray(canvas).save(path)


def score_image(source, sample, overwrite=False):
    from skimage.color import rgb2lab
    from ultralytics import YOLO
    import torch

    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED:
        raise FileNotFoundError(f"Unsupported or missing photo: {source}")
    if not sample or not all(c.isalnum() or c == "_" for c in sample):
        raise ValueError("Sample ID must contain letters, numbers and underscores")
    out = DATA / "output" / sample
    summary_path = out / "image_prediction.csv"
    if summary_path.exists() and not overwrite:
        print(f"Already scored, skipping {sample}: {summary_path}")
        return summary_path
    if out.exists() and any(out.iterdir()) and not overwrite:
        raise FileExistsError(f"Existing incomplete output: {out}; inspect or use --overwrite")
    _, bundle = check_models()
    rgb = load_and_crop_image(source)
    if min(rgb.shape[:2]) < TILE_SIZE:
        raise ValueError("Frozen 512-pixel tiling requires both cropped dimensions >= 512")

    # Save a visual record of each major processing stage. These files are
    # diagnostic/presentation outputs only and do not affect scoring.
    steps = out / "pipeline_steps"
    steps.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(steps / "01_cropped_input.png")
    render_tile_grid(rgb, steps / "02_overlapping_tiles.png")

    lab = rgb2lab(rgb)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = YOLO(str(YOLO_WEIGHTS))
    qc_model = load_qc_bundle()
    raw = detect_raw(model, rgb, None, device)
    render_stage(
        rgb,
        raw,
        steps / "03_raw_yolo_detections.png",
        color=(40, 120, 220),
    )
    print(f"{len(raw)} raw detections; deduplicating and applying QC", flush=True)
    iou = deduplicate_iou_only(raw)
    deduplicated, _ = deduplicate_containment_larger_mask(iou)
    render_stage(
        rgb,
        deduplicated,
        steps / "04_deduplicated_detections.png",
        color=(120, 70, 210),
    )
    accepted, qc_rejected, background_rejected, _ = apply_qc_and_background_gate(deduplicated, lab, qc_model)
    render_qc_stage(
        rgb,
        accepted,
        qc_rejected,
        background_rejected,
        steps / "05_qc_and_background_gates.png",
    )
    baseline = [d for d, _ in accepted]
    if not baseline:
        raise ValueError("No retained detections; inspect the input photo and background")
    print(f"{len(baseline)} retained masks; checking tile-edge repairs", flush=True)
    revised, changed = repair_masks(baseline, raw, rgb, lab, qc_model, None, None, model, device)
    revised, cleanup_changes = cleanup_mask_components(revised)
    print(f"Component cleanup: {len(cleanup_changes)} masks cleaned", flush=True)
    render_stage(
        rgb,
        revised,
        steps / "06_repaired_kernel_masks.png",
        color=(30, 180, 210),
    )
    print(f"Tile repair complete ({len(changed)} masks improved); extracting FDK features", flush=True)
    features = list(bundle["features"])
    records = []
    for i, det in enumerate(revised, 1):
        x1, y1, x2, y2 = map(int, det["bbox"])
        local_mask = det["mask"][y1:y2, x1:x2]
        if int(local_mask.sum()) != int(det["area"]):
            raise ValueError(f"Mask {i} area and bounding box differ")
        record = build_qc_features({"mask": local_mask, "confidence": 1.0},
                                   lab[y1:y2, x1:x2])
        records.append([record[name] for name in features])
    X = np.asarray(records, dtype=float)
    if not np.isfinite(X).all():
        raise ValueError("Undefined features in retained masks; no score written")
    probability = bundle["model"].predict_proba(X)[:, 1]
    predicted = probability >= .5
    gate_probability = None
    if WEED_MODEL_PATH.is_file():
        gate = joblib.load(WEED_MODEL_PATH)
        if list(gate["features"]) != features:
            raise ValueError("Non-wheat review model uses different features")
        gate_probability = gate["model"].predict_proba(X)[:, 1]
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (det, p, flag) in enumerate(zip(revised, probability, predicted), 1):
        row = {"sample_id": sample, "kernel_id": f"{sample}_K{i:04d}",
               "index": i - 1, "source": changed.get(i - 1, "original"),
               "bbox": ",".join(map(str, det["bbox"])), "area": int(det["area"]),
               "fdk_probability": float(p), "fdk_class": "FDK" if flag else "healthy",
               "included_in_primary_rate": 1,
               "possible_non_wheat_review_flag": "" if gate_probability is None else
                   int(gate_probability[i - 1] >= float(gate["threshold"])),
               "non_wheat_review_probability": "" if gate_probability is None else
                   float(gate_probability[i - 1])}
        rows.append(row)
    pack_masks(revised, rgb.shape, out / "kernel_masks.npz")
    write_csv(out / "kernel_predictions.csv", rows)
    (out / "component_cleanup.json").write_text(json.dumps(cleanup_changes, indent=2) + "\n")
    summary = {"sample_id": sample, "image_path": str(source),
               "detected_objects": len(revised), "fdk_calls": int(predicted.sum()),
               "fdk_percent_all_retained_objects": 100 * float(predicted.mean()),
               "mean_fdk_probability_percent": 100 * float(probability.mean()),
               "qc_excluded": len(qc_rejected), "background_excluded": len(background_rejected),
               "recentered": len(changed), "component_cleaned": len(cleanup_changes),
               "segmentation_version": SEGMENTATION_VERSION, "non_wheat_review_flags": "" if gate_probability is None else
                   int((gate_probability >= float(gate["threshold"])).sum())}
    print("Writing mask archive and outlined image", flush=True)
    render_overlay(rgb, revised, predicted, out / "kernels_outlined_red_FDK_green_healthy.png")
    render_overlay(
        rgb,
        revised,
        predicted,
        steps / "07_automatic_classification.png",
    )
    (out / "provenance.json").write_text(json.dumps({
        "source_sha256": file_sha256(source),
        "models_sha256": {name: file_sha256(path) for name, path in
                          (("yolo", YOLO_WEIGHTS), ("qc", QC_MODEL_PATH), ("fdk", FDK_MODEL_PATH))},
        "method": "frozen tile detection, IoU/containment dedup, QC, background gate, larger-mask tile-edge repair, 95% main-component cleanup, 30-feature FDK classifier",
        "segmentation_version": SEGMENTATION_VERSION,
        "pipeline_sha256": file_sha256(Path(__file__)),
        "scope": "Sharp, white-background, sufficiently spaced wheat kernels",
        "denominator": "All retained detections; review possible non-wheat, fragments and missing kernels before interpreting as wheat FDK percent",
    }, indent=2) + "\n")
    write_csv(summary_path, [summary])
    print(f"{sample}: {summary['fdk_calls']}/{len(revised)} = {summary['fdk_percent_all_retained_objects']:.2f}% FDK of retained objects; {len(changed)} repaired masks")
    print(f"Outputs: {out}")
    return summary_path


MANUAL_CATEGORIES = ("healthy", "FDK", "unsure", "weed_seed", "other_object")


def read_rows(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def import_legacy_labels(sample, prior_project):
    """Transfer completed legacy labels only if every saved model fingerprint matches."""
    prior_project = Path(prior_project).expanduser().resolve()
    old_base = prior_project / "results/fdk_classification/independent_validation"
    automatic_path = old_base / "automatic_predictions" / sample / "kernel_predictions.csv"
    legacy_instances = None
    geometry_only = False
    heldout_layout = False
    if automatic_path.is_file():
        old_predictions = read_rows(automatic_path)
        old_layout = "automatic_predictions"
    else:
        comparison_path = (old_base / "prospective_comparison" / sample / "locked_v6_0465" /
                           "locked_kernel_comparison.csv")
        if not comparison_path.is_file():
            comparison_path = old_base / "prospective_comparison" / sample / "kernel_comparison.csv"
        if not comparison_path.is_file():
            comparison_path = old_base / "model_comparison" / sample / "heldout_kernel_comparison.csv"
            heldout_layout = comparison_path.is_file()
        instances_path = old_base / "labels" / sample / "kernel_instances.csv"
        if comparison_path.is_file() and instances_path.is_file():
            all_comparisons = read_rows(comparison_path)
            old_predictions = (all_comparisons if heldout_layout else
                               [r for r in all_comparisons if r["model"] == "frozen_468"])
            legacy_instances = read_rows(instances_path)
            old_layout = ("model_comparison" if heldout_layout else
                          "locked_v6_0465" if comparison_path.name == "locked_kernel_comparison.csv" else
                          "prospective_comparison")
        else:
            old_manual = old_base / "independent_manual_count" / sample
            instances_path = old_manual / "kernel_instances.csv"
            if not instances_path.is_file() or not (old_manual / "blind_kernel_labels.csv").is_file():
                raise FileNotFoundError(f"No supported old predictions or completed manual instances for {sample}")
            legacy_instances = read_rows(instances_path)
            old_predictions = []
            geometry_only = True
            old_layout = "independent_manual_count"
            old_summary = read_rows(old_manual / "blind_label_summary.csv")
            if len(old_summary) != 1 or old_summary[0].get("status") != "complete" or int(old_summary[0].get("unlabeled", -1)) != 0:
                raise ValueError(f"The old independent manual review for {sample} is incomplete")
    labels_path = (old_base / "independent_manual_count" / sample / "blind_kernel_labels.csv"
                   if geometry_only else old_base / "labels" / sample / "blind_kernel_labels.csv")
    old_labels = read_rows(labels_path)
    current = read_rows(DATA / "output" / sample / "kernel_predictions.csv")
    if (not old_predictions and not geometry_only) or not current:
        raise ValueError("Empty scored prediction file")
    old = {r["kernel_id"]: r for r in old_predictions}
    new = {r["kernel_id"]: r for r in current}
    human = {r["kernel_id"]: r["label"] for r in old_labels}
    if (len(old) != len(old_predictions) or len(new) != len(current)
            or len(human) != len(old_labels) or (not geometry_only and set(old) != set(new))
            or set(new) != set(human)):
        raise ValueError("Legacy predictions, labels, and streamlined detections have different IDs or duplicates; no transfer")
    if any(r["sample_id"] != sample for r in (*old_predictions,*old_labels,*current)):
        raise ValueError("Sample ID mismatch; no transfer")
    if set(human.values()) - set(MANUAL_CATEGORIES):
        raise ValueError("Legacy labels contain unsupported categories; no transfer")
    instances = None
    if legacy_instances is not None:
        instances = {r["kernel_id"]:r for r in legacy_instances}
        if len(instances) != len(legacy_instances) or set(instances) != set(new):
            raise ValueError("Legacy instance geometry does not cover all scored IDs; no transfer")
    differences = []
    for kid in new:
        b = new[kid]
        mismatch = False
        if not geometry_only:
            a = old[kid]
            old_class = a["frozen_fdk_class"] if instances is None else a["model_class"]
            old_probability = (a["frozen_fdk_probability"] if instances is None else
                               a["model_p_fdk"] if heldout_layout else a["fdk_probability"])
            mismatch = (old_class != b["fdk_class"] or
                        abs(float(old_probability)-float(b["fdk_probability"])) > 1e-10)
        if instances is None:
            mismatch |= a["possible_non_wheat_review_flag"] != b["possible_non_wheat_review_flag"]
        else:
            geometry=instances[kid]
            mismatch |= (geometry["sample_id"] != sample or
                         geometry["bbox"] != b["bbox"] or
                         int(geometry["area"]) != int(b["area"]) or
                         int(geometry["index"]) != int(b["index"]) or
                         geometry["source"] != b["source"])
            if not geometry_only:
                mismatch |= (a["manual_label"] if heldout_layout else a["blind_label"]) != human[kid]
        if mismatch:
            differences.append(kid)
    if differences:
        raise ValueError(f"Saved per-kernel model fingerprints differ at {len(differences)} IDs "
                         f"(first: {differences[:5]}); no labels transferred")
    original_path = Path(read_rows(DATA / "output" / sample / "image_prediction.csv")[0]["image_path"])
    prior_crop = old_base / "blind_crops" / f"{sample}_BLIND_CROP.png"
    if geometry_only and not prior_crop.is_file():
        prior_crop = prior_project / "data" / "raw" / "SDSU_validation" / original_path.name
        if not prior_crop.is_file():
            raise FileNotFoundError("Geometry-only import requires the old blind crop or matching old original photo")
    if prior_crop.is_file():
        old_rgb = np.asarray(Image.open(prior_crop).convert("RGB"))
        current_rgb = load_and_crop_image(original_path)
        if geometry_only and prior_crop.name == original_path.name:
            old_rgb = load_and_crop_image(prior_crop)
        if old_rgb.shape != current_rgb.shape or not np.array_equal(old_rgb,current_rgb):
            raise ValueError("Legacy blind crop differs from the scored photo; no labels transferred")
    output = DATA / "output" / sample / "manual"
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Streamlined manual review already exists: {output}; no overwrite")
    counts = Counter(human.values())
    wheat = counts["healthy"] + counts["FDK"]
    if not wheat:
        raise ValueError("No labeled wheat kernels")
    write_csv(output / "labels.csv", [{"sample_id":sample,"kernel_id":r["kernel_id"],
                                        "label":human[r["kernel_id"]]} for r in current])
    (output / "review_state.json").write_text(json.dumps({"overview_checked": True,
        "imported_from": str(prior_project),
        "verification": (f"all IDs, complete instance geometry and original crop pixels match; "
                         "legacy model predictions unavailable" if geometry_only else
                         f"all IDs and {old_layout} model fingerprints match; "
                         "instance geometry and blind crop pixels checked when available")})+"\n")
    write_csv(output / "review_summary.csv", [{"sample_id":sample,"objects":len(new),
        "healthy":counts["healthy"],"FDK":counts["FDK"],"unsure":counts["unsure"],
        "weed_seed":counts["weed_seed"],"other_object":counts["other_object"],
        "manually_added":0,"unlabeled":0,"overview_checked":1,
        "manual_wheat_fdk_percent":100*counts["FDK"]/wheat,"status":"complete"}])
    checks = ("full ID, geometry and image pixel checks (legacy probabilities unavailable)" if geometry_only
              else "full ID, geometry (when available), class and probability checks")
    print(f"Transferred {len(new)} legacy labels from {old_layout} after {checks}; "
          f"manual FDK {counts['FDK']}/{wheat} = {100*counts['FDK']/wheat:.2f}%")
    print(f"Saved: {output / 'labels.csv'}")


def validation_report():
    """Summarize saved complete reviews with explicit reference provenance."""
    output_root = DATA / "output"
    rows = []
    for folder in sorted(output_root.iterdir()):
        if not folder.is_dir():
            continue
        sample = folder.name
        manual = folder / "manual"
        summary_path = manual / "review_summary.csv"
        prediction_path = folder / "kernel_predictions.csv"
        if not summary_path.is_file() or not prediction_path.is_file():
            continue
        summaries = read_rows(summary_path)
        if len(summaries) != 1 or summaries[0]["status"] != "complete":
            continue
        predictions = read_rows(prediction_path)
        labels_rows = read_rows(manual / "labels.csv")
        labels = {r["kernel_id"]: r["label"] for r in labels_rows}
        pred = {r["kernel_id"]: r for r in predictions}
        if len(labels) != len(labels_rows) or len(pred) != len(predictions) or set(pred) - set(labels):
            raise ValueError(f"Duplicate or missing IDs in {sample}; no report written")
        reference = "completed_initial_review"
        for subfolder, label in (("disagreement_audit", "selected_second_pass_hybrid"),
                                 ("review_reconciliation", "selected_third_pass_hybrid"),
                                 ("false_positive_spotcheck", "selected_spotcheck_hybrid")):
            queue_path = manual / subfolder / "queue.csv"
            judgment_path = manual / subfolder / "judgments.csv"
            if not queue_path.is_file() or not judgment_path.is_file():
                continue
            queue, judged = read_rows(queue_path), read_rows(judgment_path)
            updates = {r["kernel_id"]: r["second_label"] for r in judged}
            if (len(updates) != len(judged) or set(updates) != {r["kernel_id"] for r in queue}
                    or set(updates) - set(labels)):
                raise ValueError(f"Incomplete or inconsistent {subfolder} for {sample}; no report written")
            labels.update(updates)
            reference = ("selected_third_pass_plus_spotcheck_hybrid"
                         if subfolder == "false_positive_spotcheck" and
                         reference == "selected_third_pass_hybrid" else label)
        for review_dir in sorted(manual.glob("manual_sdsu_candidate_v*_error_review")):
            queue_path,judgment_path=review_dir / "queue.csv",review_dir / "judgments.csv"
            if not queue_path.is_file() or not judgment_path.is_file():
                raise ValueError(f"Incomplete candidate review for {sample}; no report written")
            queue,judged=read_rows(queue_path),read_rows(judgment_path)
            updates={r["kernel_id"]:r["second_label"] for r in judged}
            if (len(updates)!=len(judged) or set(updates)!={r["kernel_id"] for r in queue}
                    or set(updates)-set(labels)):
                raise ValueError(f"Incomplete candidate review for {sample}; no report written")
            labels.update(updates)
            reference=f"selected_{review_dir.name}_hybrid"
        count = Counter(labels.values())
        wheat = count["healthy"] + count["FDK"]
        matched = [kid for kid in pred if labels[kid] in ("healthy", "FDK")]
        if not wheat or not matched or len(predictions) == 0:
            raise ValueError(f"No scored wheat in {sample}; no report written")
        tp = sum(labels[k] == "FDK" and pred[k]["fdk_class"] == "FDK" for k in matched)
        fn = sum(labels[k] == "FDK" and pred[k]["fdk_class"] == "healthy" for k in matched)
        fp = sum(labels[k] == "healthy" and pred[k]["fdk_class"] == "FDK" for k in matched)
        tn = sum(labels[k] == "healthy" and pred[k]["fdk_class"] == "healthy" for k in matched)
        all_fdk = sum(r["fdk_class"] == "FDK" for r in predictions)
        rows.append({"sample_id":sample,"reference":reference,
            "retained_objects":len(predictions),"automatic_FDK_calls":all_fdk,
            "automatic_FDK_percent_all_objects":100*all_fdk/len(predictions),
            "manual_FDK":count["FDK"],"manual_healthy":count["healthy"],
            "manual_unsure":count["unsure"],"manual_weed_seed":count["weed_seed"],
            "manual_other_object":count["other_object"],
            "manual_partial_detection":count["partial_kernel_detection"],
            "manual_additions_without_model_mask":len(set(labels)-set(pred)),
            "manual_wheat_count":wheat,"manual_FDK_percent_wheat":100*count["FDK"]/wheat,
            "same_detected_wheat_count":len(matched),
            "manual_FDK_percent_same_wheat":100*(tp+fn)/len(matched),
            "model_FDK_percent_same_wheat":100*(tp+fp)/len(matched),
            "TP":tp,"FN":fn,"FP":fp,"TN":tn})
    if not rows:
        raise ValueError("No completed scored manual reviews under data/output")
    destination = output_root / "validation_summary.csv"
    write_csv(destination,rows)
    print(f"Saved {len(rows)} reviewed photos: {destination}")
    for r in rows:
        print(f"{r['sample_id']} ({r['reference']}): automatic all objects "
              f"{r['automatic_FDK_percent_all_objects']:.2f}%; same wheat model "
              f"{r['model_FDK_percent_same_wheat']:.2f}% vs manual "
              f"{r['manual_FDK_percent_same_wheat']:.2f}% "
              f"(n={r['same_detected_wheat_count']}, unsure={r['manual_unsure']}, "
              f"weeds={r['manual_weed_seed']}, other={r['manual_other_object']}, "
              f"partial={r['manual_partial_detection']})")
    print("Selected-review hybrids retain initial labels outside reviewed subsets; "
          "automatic rates include all retained objects. Do not pool denominators.")


def review_image(sample):
    """Review saved detections without displaying model calls."""
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageDraw, ImageFilter, ImageTk

    out = DATA / "output" / sample
    prediction_file = out / "kernel_predictions.csv"
    summary_file = out / "image_prediction.csv"
    mask_file = out / "kernel_masks.npz"
    if not all(p.is_file() for p in (prediction_file, summary_file, mask_file)):
        raise FileNotFoundError(f"Score {sample} before manual review: {out}")
    predictions = read_rows(prediction_file)
    summary = read_rows(summary_file)
    if len(summary) != 1 or summary[0]["sample_id"] != sample:
        raise ValueError("Saved score and requested sample differ")
    source = Path(summary[0]["image_path"])
    provenance = json.loads((out / "provenance.json").read_text())
    if file_sha256(source) != provenance["source_sha256"]:
        raise ValueError("Original photo changed since scoring; review stopped")
    rgb = load_and_crop_image(source)
    image = Image.fromarray(rgb)
    with np.load(mask_file, allow_pickle=False) as archive:
        masks = {k: archive[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if (tuple(masks["image_shape"]) != rgb.shape[:2]
            or len(masks["bboxes"]) != len(predictions)
            or len(masks["offsets"]) != len(predictions) + 1
            or int(masks["offsets"][-1]) != len(masks["pixels"])):
        raise ValueError("Saved masks differ from the scored original")
    entries = []
    for i, row in enumerate(predictions):
        if (row["sample_id"] != sample or int(row["index"]) != i
                or row["kernel_id"] != f"{sample}_K{i + 1:04d}"
                or tuple(map(int, row["bbox"].split(","))) != tuple(map(int, masks["bboxes"][i]))):
            raise ValueError(f"Saved kernel order or geometry differs at index {i}")
        entries.append({"kernel_id": row["kernel_id"], "index": i, "bbox": row["bbox"],
                        "source": row["source"]})
    manual = out / "manual"
    manual.mkdir(exist_ok=True)
    labels_file = manual / "labels.csv"
    additions_file = manual / "added_kernels.csv"
    state_file = manual / "review_state.json"
    if additions_file.exists():
        entries.extend(read_rows(additions_file))
    if len({r["kernel_id"] for r in entries}) != len(entries):
        raise ValueError("Duplicate manual kernel IDs")
    labels = {r["kernel_id"]: r["label"] for r in read_rows(labels_file)} if labels_file.exists() else {}
    if set(labels.values()) - set(MANUAL_CATEGORIES) or set(labels) - {r["kernel_id"] for r in entries}:
        raise ValueError("Manual labels contain an unknown category or kernel ID")
    state = json.loads(state_file.read_text()) if state_file.exists() else {"overview_checked": False}

    def bbox(row):
        return tuple(map(int, row["bbox"].split(",")))

    def save():
        ordered = [{"sample_id": sample, "kernel_id": r["kernel_id"],
                    "label": labels[r["kernel_id"]]} for r in entries if r["kernel_id"] in labels]
        # An empty file is not necessary until the first label.
        if ordered:
            write_csv(labels_file, ordered)
        counts = Counter(labels.values())
        wheat = counts["healthy"] + counts["FDK"]
        ready = len(labels) == len(entries) and bool(state["overview_checked"]) and wheat > 0
        write_csv(manual / "review_summary.csv", [{
            "sample_id": sample, "objects": len(entries),
            "healthy": counts["healthy"], "FDK": counts["FDK"],
            "unsure": counts["unsure"], "weed_seed": counts["weed_seed"],
            "other_object": counts["other_object"],
            "manually_added": sum(r["source"] == "manually_added" for r in entries),
            "unlabeled": len(entries) - len(labels),
            "overview_checked": int(bool(state["overview_checked"])),
            "manual_wheat_fdk_percent": 100 * counts["FDK"] / wheat if ready else "",
            "status": "complete" if ready else "incomplete",
        }])

    root = tk.Tk()
    root.title(f"Manual wheat kernel review — {sample}")
    frame = tk.Frame(root)
    frame.pack(padx=12, pady=12)
    title = tk.Label(frame, font=("Arial", 14), anchor="w")
    title.grid(row=0, column=0, columnspan=2, sticky="ew")
    detail = tk.Label(frame)
    detail.grid(row=1, column=0, padx=(0, 12), pady=8)
    overview = tk.Label(frame, cursor="crosshair")
    overview.grid(row=1, column=1, pady=8)
    status = tk.Label(frame, font=("Arial", 11), anchor="w")
    status.grid(row=2, column=0, columnspan=2, sticky="ew")
    actions = tk.Frame(frame)
    actions.grid(row=3, column=0, columnspan=2, pady=8)
    pos = [next((i for i, r in enumerate(entries) if r["kernel_id"] not in labels), 0)]
    add_mode = [False]
    scale = min(800 / image.width, 640 / image.height, 1.0)
    overview_base = image.resize((max(1, round(image.width * scale)),
                                  max(1, round(image.height * scale))), Image.Resampling.LANCZOS)

    def update():
        row = entries[pos[0]]
        x1, y1, x2, y2 = bbox(row)
        margin = max(40, int(max(x2 - x1, y2 - y1) * .55))
        region = (max(0, x1 - margin), max(0, y1 - margin),
                  min(image.width, x2 + margin), min(image.height, y2 + margin))
        crop = image.crop(region).copy()
        index = int(row["index"])
        if index >= 0:
            local = restore_mask(masks, index)
            mask_image = Image.fromarray((local * 255).astype("uint8"))
            expanded = mask_image.filter(ImageFilter.MaxFilter(5))
            border = np.asarray(expanded) > np.asarray(mask_image)
            pixels = np.asarray(crop).copy()
            oy, ox = y1 - region[1], x1 - region[0]
            pixels[oy:oy + local.shape[0], ox:ox + local.shape[1]][border] = (200, 40, 210)
            crop = Image.fromarray(pixels)
        draw = ImageDraw.Draw(crop)
        draw.rectangle((x1 - region[0], y1 - region[1],
                        x2 - region[0], y2 - region[1]), outline="#e0a000", width=2)
        factor = min(560 / crop.width, 480 / crop.height, 4)
        crop = crop.resize((max(1, round(crop.width * factor)),
                            max(1, round(crop.height * factor))), Image.Resampling.NEAREST)
        detail.image = ImageTk.PhotoImage(crop)
        detail.config(image=detail.image)
        shown = overview_base.copy()
        ImageDraw.Draw(shown).rectangle((x1 * scale, y1 * scale, x2 * scale, y2 * scale),
                                        outline="#cf23c8", width=3)
        overview.image = ImageTk.PhotoImage(shown)
        overview.config(image=overview.image)
        counts = Counter(labels.values())
        title.config(text=f"{sample}   {pos[0] + 1}/{len(entries)}   {row['kernel_id']}")
        status.config(text=f"Current: {labels.get(row['kernel_id'], 'unlabeled')}   "
                           f"H {counts['healthy']}  FDK {counts['FDK']}  U {counts['unsure']}  "
                           f"weed {counts['weed_seed']}  other {counts['other_object']}   "
                           f"unlabeled {len(entries) - len(labels)}")

    def mark(category):
        labels[entries[pos[0]]["kernel_id"]] = category
        save()
        if pos[0] + 1 < len(entries):
            pos[0] += 1
        update()

    def move(delta):
        pos[0] = max(0, min(len(entries) - 1, pos[0] + delta))
        update()

    def add_missing(event):
        if not add_mode[0]:
            return
        add_mode[0] = False
        x, y = int(event.x / scale), int(event.y / scale)
        if not (0 <= x < image.width and 0 <= y < image.height):
            return
        if any(x1 <= x < x2 and y1 <= y < y2 for x1, y1, x2, y2 in map(bbox, entries)):
            if not messagebox.askyesno("Nearby detection", "A detected box covers this point. Add another kernel?"):
                return
        original_boxes = [bbox(r) for r in entries if int(r["index"]) >= 0]
        widths = sorted(b[2] - b[0] for b in original_boxes)
        heights = sorted(b[3] - b[1] for b in original_boxes)
        hw, hh = max(12, widths[len(widths) // 2] // 2), max(12, heights[len(heights) // 2] // 2)
        next_number = 1 + max((int(r["kernel_id"].rsplit("_M", 1)[1])
                               for r in entries if r["kernel_id"].startswith(sample + "_M")), default=0)
        row = {"kernel_id": f"{sample}_M{next_number:04d}", "index": -1,
               "bbox": ",".join(map(str, (max(0, x - hw), max(0, y - hh),
                                        min(image.width, x + hw), min(image.height, y + hh)))),
               "source": "manually_added"}
        entries.append(row)
        write_csv(additions_file, [r for r in entries if r["source"] == "manually_added"])
        state["overview_checked"] = False
        state_file.write_text(json.dumps(state) + "\n")
        save()
        pos[0] = len(entries) - 1
        update()

    def check_overview():
        state["overview_checked"] = True
        state_file.write_text(json.dumps(state) + "\n")
        save()
        messagebox.showinfo("Overview saved", "Any missed whole kernels can be added by clicking their centers.")
        update()

    for text, key, value in (("Healthy H", "h", "healthy"), ("FDK F", "f", "FDK"),
                             ("Unsure U", "u", "unsure"), ("Weed W", "w", "weed_seed"),
                             ("Other O", "o", "other_object")):
        tk.Button(actions, text=text, command=lambda v=value: mark(v)).pack(side="left", padx=2)
        root.bind(key, lambda event, v=value: mark(v))
    tk.Button(actions, text="Previous B", command=lambda: move(-1)).pack(side="left", padx=2)
    tk.Button(actions, text="Next N", command=lambda: move(1)).pack(side="left", padx=2)
    root.bind("b", lambda event: move(-1))
    root.bind("n", lambda event: move(1))
    tk.Button(frame, text="Add missed kernel: click in overview",
              command=lambda: add_mode.__setitem__(0, True)).grid(row=4, column=0, pady=4)
    tk.Button(frame, text="Full image checked", command=check_overview).grid(row=4, column=1, pady=4)
    overview.bind("<Button-1>", add_missing)
    save()
    update()
    root.mainloop()
    print(f"Saved {len(labels)}/{len(entries)} labels to {labels_file}")
    print(f"Next: python fdk_pipeline.py compare --sample-id {sample}")


def compare_manual(sample):
    out = DATA / "output" / sample
    manual = out / "manual"
    summary = read_rows(manual / "review_summary.csv")
    if len(summary) != 1 or summary[0]["status"] != "complete":
        raise ValueError("Finish all manual labels and click Full image checked before comparison")
    predictions = read_rows(out / "kernel_predictions.csv")
    labels = read_rows(manual / "labels.csv")
    additions = read_rows(manual / "added_kernels.csv") if (manual / "added_kernels.csv").exists() else []
    expected = {r["kernel_id"] for r in predictions} | {r["kernel_id"] for r in additions}
    human = {r["kernel_id"]: r["label"] for r in labels}
    if len(human) != len(labels) or set(human) != expected:
        raise ValueError("Manual label IDs differ from scored masks and additions")
    pred = {r["kernel_id"]: r for r in predictions}
    counts = Counter(human.values())
    wheat = counts["healthy"] + counts["FDK"]
    matched = [k for k in pred if human[k] in ("healthy", "FDK")]
    if not matched:
        raise ValueError("No detected wheat kernels were labeled; matched comparison unavailable")
    tp = sum(human[k] == "FDK" and pred[k]["fdk_class"] == "FDK" for k in matched)
    fn = sum(human[k] == "FDK" and pred[k]["fdk_class"] == "healthy" for k in matched)
    fp = sum(human[k] == "healthy" and pred[k]["fdk_class"] == "FDK" for k in matched)
    tn = sum(human[k] == "healthy" and pred[k]["fdk_class"] == "healthy" for k in matched)
    rows = [{"sample_id": sample, "kernel_id": k, "manual_label": human[k],
             "model_class": pred[k]["fdk_class"] if k in pred else "",
             "model_fdk_probability": pred[k]["fdk_probability"] if k in pred else "",
             "manual_addition": int(k not in pred),
             "included_in_matched_wheat": int(k in matched)} for k in human]
    write_csv(manual / "kernel_comparison.csv", rows)
    result = {"sample_id": sample, "manual_FDK": counts["FDK"], "manual_healthy": counts["healthy"],
              "manual_unsure": counts["unsure"], "manual_weed_seed": counts["weed_seed"],
              "manual_other_object": counts["other_object"], "manual_additions": len(additions),
              "manual_wheat_FDK_percent": 100 * counts["FDK"] / wheat,
              "matched_detected_wheat": len(matched),
              "manual_FDK_percent_matched": 100 * (tp + fn) / len(matched) if matched else "",
              "model_FDK_percent_matched": 100 * (tp + fp) / len(matched) if matched else "",
              "TP": tp, "FN": fn, "FP": fp, "TN": tn,
              "model_FDK_percent_all_objects": 100 * sum(r["fdk_class"] == "FDK" for r in predictions) / len(predictions)}
    write_csv(manual / "image_comparison.csv", [result])
    print(f"Manual wheat: {counts['FDK']}/{wheat} = {result['manual_wheat_FDK_percent']:.2f}% FDK "
          f"({counts['unsure']} unsure, {counts['weed_seed']} weeds, {counts['other_object']} other objects)")
    print(f"On {len(matched)} same detected wheat: manual {result['manual_FDK_percent_matched']:.2f}%, "
          f"model {result['model_FDK_percent_matched']:.2f}%; TP/FN/FP/TN {tp}/{fn}/{fp}/{tn}")
    print(f"Saved: {manual / 'image_comparison.csv'}")


def disagreement_audit(sample, control_per_class=8, mode="model", candidate_name=None):
    """Masked second pass through model disagreements or changed human labels."""
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageDraw, ImageFilter, ImageTk

    out = DATA / "output" / sample
    manual = out / "manual"
    summary = read_rows(manual / "review_summary.csv")
    if len(summary) != 1 or summary[0]["status"] != "complete":
        raise ValueError("Complete the initial manual review before auditing disagreements")
    predictions = read_rows(out / "kernel_predictions.csv")
    if mode == "candidate":
        candidate_name = candidate_name or "manual_sdsu_candidate_v2"
        candidate_directory(candidate_name)
        comparison_folder = manual / f"{candidate_name}_independent_comparison"
        comparison = read_rows(comparison_folder / "kernel_predictions.csv")
        candidate_calls = {r["kernel_id"]:r["candidate_call"] for r in comparison}
        if (len(candidate_calls) != len(comparison) or
                set(candidate_calls) != {r["kernel_id"] for r in predictions}):
            raise ValueError("Candidate evaluation and scored detections differ")
        predictions = [{**r,"fdk_class":candidate_calls[r["kernel_id"]]} for r in predictions]
    pred = {r["kernel_id"]: r for r in predictions}
    original = {r["kernel_id"]: r["label"] for r in read_rows(manual / "labels.csv")}
    if control_per_class < 0 or mode not in ("model", "human", "fp", "candidate"):
        raise ValueError("Invalid audit mode or control count")
    audit = manual / (f"{candidate_name}_error_review" if mode == "candidate" else
                      {"model": "disagreement_audit", "human": "review_reconciliation",
                       "fp": "false_positive_spotcheck"}[mode])
    audit.mkdir(exist_ok=True)
    queue_file = audit / "queue.csv"
    if queue_file.exists():
        queue = read_rows(queue_file)
        if any(r["kernel_id"] not in pred or original.get(r["kernel_id"]) != r["original_label"]
               for r in queue):
            raise ValueError("Manual labels changed after the audit queue was made; preserve or inspect its saved queue")
    elif mode == "fp":
        reconciliation = manual / "review_reconciliation"
        reviewed = read_rows(reconciliation / "judgments.csv")
        reviewed_queue = read_rows(reconciliation / "queue.csv")
        if {r["kernel_id"] for r in reviewed} != {r["kernel_id"] for r in reviewed_queue}:
            raise ValueError("Complete reconciliation before sampling false positives")
        revised = dict(original)
        revised.update({r["kernel_id"]: r["second_label"] for r in reviewed})
        groups = {"FP": [], "TN": []}
        for row in predictions:
            kid = row["kernel_id"]
            if revised.get(kid) == "healthy":
                group = "FP" if row["fdk_class"] == "FDK" else "TN"
                groups[group].append(kid)
        if not groups["FP"] or not groups["TN"]:
            raise ValueError("Both false positives and true negatives are required")
        rng = np.random.default_rng(20260927)
        count = min(control_per_class, len(groups["FP"]), len(groups["TN"]))
        if count == 0:
            raise ValueError("Select at least one kernel per class")
        selected = [(str(kid), group) for group in ("FP", "TN")
                    for kid in rng.choice(groups[group], size=count, replace=False)]
        rng.shuffle(selected)
        queue = [{"sample_id": sample, "kernel_id": kid, "comparison_group": group,
                  "original_label": revised[kid], "model_class": pred[kid]["fdk_class"]}
                 for kid, group in selected]
        write_csv(queue_file, queue)
    elif mode == "human":
        comparison = read_rows(manual / "review_comparison" / "kernel_label_changes.csv")
        if {r["kernel_id"] for r in comparison} != set(original):
            raise ValueError("Two-review comparison does not cover the current detected kernels")
        changed = [r for r in comparison if r["changed"] == "1"]
        stable = [r for r in comparison if r["changed"] == "0"]
        if not changed:
            raise ValueError("No changed human labels to reconcile")
        rng = np.random.default_rng(20260927)
        selected = [(r, "changed") for r in changed]
        for category in ("healthy", "FDK", "unsure"):
            group = [r for r in stable if r["current_label"] == category]
            choice = rng.choice(len(group), size=min(control_per_class, len(group)),
                                replace=False) if group else []
            selected.extend((group[int(i)], "stable_" + category) for i in choice)
        rng.shuffle(selected)
        queue = [{"sample_id": sample, "kernel_id": r["kernel_id"],
                  "comparison_group": group, "original_label": r["current_label"],
                  "prior_label": r["prior_label"], "current_label": r["current_label"],
                  "model_class": pred[r["kernel_id"]]["fdk_class"]}
                 for r, group in selected]
        write_csv(queue_file, queue)
    else:
        groups = {"FP": [], "FN": [], "TP": [], "TN": []}
        for row in predictions:
            kid = row["kernel_id"]
            label = original.get(kid)
            call = row["fdk_class"]
            if label not in ("healthy", "FDK"):
                continue
            group = ("T" if label == call else "F") + ("P" if call == "FDK" else "N")
            groups[group].append(kid)
        rng = np.random.default_rng(20260927)
        selected = [(kid, group) for group in ("FP", "FN")
                    for kid in groups[group]]
        for group in ("TP", "TN"):
            controls = rng.choice(groups[group],
                                  size=min(control_per_class, len(groups[group])),
                                  replace=False) if groups[group] else []
            selected.extend((str(kid), group) for kid in controls)
        rng.shuffle(selected)
        queue = [{"sample_id": sample, "kernel_id": kid, "comparison_group": group,
                  "original_label": original[kid], "model_class": pred[kid]["fdk_class"]}
                 for kid, group in selected]
        if not queue:
            raise ValueError("No detected wheat kernels available for disagreement review")
        write_csv(queue_file, queue)
    judgments_file = audit / "judgments.csv"
    judgments = {r["kernel_id"]: r["second_label"] for r in read_rows(judgments_file)} if judgments_file.exists() else {}
    allowed = set(MANUAL_CATEGORIES) | {"partial_kernel_detection"}
    if set(judgments) - {r["kernel_id"] for r in queue} or set(judgments.values()) - allowed:
        raise ValueError("Audit judgments contain an unknown kernel or category")
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    provenance = json.loads((out / "provenance.json").read_text())
    if file_sha256(source) != provenance["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    image = Image.fromarray(load_and_crop_image(source))
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archived:
        masks = {k: archived[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"][::-1]) != image.size:
        raise ValueError("Mask archive and scored photo differ")
    root = tk.Tk()
    root.title(f"{'Candidate disagreement review' if mode == 'candidate' else 'Healthy-kernel spot check' if mode == 'fp' else 'Human-label reconciliation' if mode == 'human' else 'Model disagreement review'} — {sample}")
    frame = tk.Frame(root)
    frame.pack(padx=12, pady=12)
    title = tk.Label(frame, font=("Arial", 14))
    title.grid(row=0, column=0, columnspan=2)
    detail = tk.Label(frame)
    detail.grid(row=1, column=0, padx=10, pady=8)
    overview = tk.Label(frame)
    overview.grid(row=1, column=1, pady=8)
    status = tk.Label(frame, font=("Arial", 11))
    status.grid(row=2, column=0, columnspan=2)
    actions = tk.Frame(frame)
    actions.grid(row=3, column=0, columnspan=2, pady=8)
    scale = min(800 / image.width, 640 / image.height, 1.0)
    overview_base = image.resize((max(1, round(image.width * scale)),
                                  max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    pos = [next((i for i, r in enumerate(queue) if r["kernel_id"] not in judgments), 0)]

    def update():
        item = queue[pos[0]]
        row = pred[item["kernel_id"]]
        i = int(row["index"])
        x1, y1, x2, y2 = map(int, row["bbox"].split(","))
        margin = max(40, int(max(x2 - x1, y2 - y1) * .55))
        region = (max(0, x1 - margin), max(0, y1 - margin),
                  min(image.width, x2 + margin), min(image.height, y2 + margin))
        crop = image.crop(region).copy()
        local = restore_mask(masks, i)
        mask_img = Image.fromarray((local * 255).astype("uint8"))
        border = np.asarray(mask_img.filter(ImageFilter.MaxFilter(5))) > np.asarray(mask_img)
        pixels = np.asarray(crop).copy()
        oy, ox = y1 - region[1], x1 - region[0]
        pixels[oy:oy + local.shape[0], ox:ox + local.shape[1]][border] = (200, 40, 210)
        crop = Image.fromarray(pixels)
        ImageDraw.Draw(crop).rectangle((x1 - region[0], y1 - region[1],
                                        x2 - region[0], y2 - region[1]),
                                       outline="#e0a000", width=2)
        factor = min(560 / crop.width, 480 / crop.height, 4)
        crop = crop.resize((max(1, round(crop.width * factor)),
                            max(1, round(crop.height * factor))), Image.Resampling.NEAREST)
        detail.image = ImageTk.PhotoImage(crop)
        detail.config(image=detail.image)
        shown = overview_base.copy()
        ImageDraw.Draw(shown).rectangle((x1 * scale, y1 * scale, x2 * scale, y2 * scale),
                                        outline="#cf23c8", width=3)
        overview.image = ImageTk.PhotoImage(shown)
        overview.config(image=overview.image)
        title.config(text=f"{sample}   {pos[0] + 1}/{len(queue)}   {item['kernel_id']}")
        status.config(text=f"Second-pass label: {judgments.get(item['kernel_id'], 'unlabeled')}   "
                           f"Completed {len(judgments)}/{len(queue)}")

    def mark(value):
        judgments[queue[pos[0]]["kernel_id"]] = value
        ordered = [{"sample_id": sample, "kernel_id": r["kernel_id"],
                    "second_label": judgments[r["kernel_id"]]}
                   for r in queue if r["kernel_id"] in judgments]
        write_csv(judgments_file, ordered)
        if pos[0] < len(queue) - 1:
            pos[0] += 1
        update()

    def move(step):
        pos[0] = max(0, min(len(queue) - 1, pos[0] + step))
        update()

    for caption, key, value in (("Healthy H", "h", "healthy"), ("FDK F", "f", "FDK"),
                                ("Unsure U", "u", "unsure"), ("Weed W", "w", "weed_seed"),
                                ("Other O", "o", "other_object"),
                                ("Partial P", "p", "partial_kernel_detection")):
        tk.Button(actions, text=caption, command=lambda v=value: mark(v)).pack(side="left", padx=2)
        root.bind(key, lambda event, v=value: mark(v))
    tk.Button(actions, text="Previous B", command=lambda: move(-1)).pack(side="left", padx=2)
    tk.Button(actions, text="Next N", command=lambda: move(1)).pack(side="left", padx=2)
    root.bind("b", lambda event: move(-1))
    root.bind("n", lambda event: move(1))
    update()
    root.mainloop()
    print(f"Saved {len(judgments)}/{len(queue)} judgments: {judgments_file}")
    if len(judgments) == len(queue):
        audit_report(sample, mode, candidate_name)
    else:
        command = {"human": "reconcile", "model": "audit", "fp": "review-fp",
                   "candidate": "audit-candidate"}[mode]
        print(f"Resume: python fdk_pipeline.py {command} --sample-id {sample}")


def audit_report(sample, mode="model", candidate_name=None):
    audit = DATA / "output" / sample / "manual" / (f"{candidate_name}_error_review" if
        mode == "candidate" else {
        "human": "review_reconciliation", "model": "disagreement_audit",
        "fp": "false_positive_spotcheck"}[mode])
    queue = read_rows(audit / "queue.csv")
    judgments = {r["kernel_id"]: r["second_label"] for r in read_rows(audit / "judgments.csv")}
    if set(judgments) != {r["kernel_id"] for r in queue}:
        raise ValueError(f"Audit incomplete: {len(judgments)}/{len(queue)} reviewed")
    rows = [{**r, "second_label": judgments[r["kernel_id"]],
             "changed_from_first_review": int(judgments[r["kernel_id"]] != r["original_label"])}
            for r in queue]
    write_csv(audit / "adjudicated_subset.csv", rows)
    if mode == "fp":
        for group in ("FP", "TN"):
            selected = [r for r in rows if r["comparison_group"] == group]
            print(f"{group}: {len(selected)} sampled; blind recheck labels "
                  f"{dict(Counter(r['second_label'] for r in selected))}")
        print("Selected kernels only; do not extrapolate these counts to the whole photo.")
    elif mode == "human":
        changed = [r for r in rows if r["comparison_group"] == "changed"]
        prior = sum(r["second_label"] == r["prior_label"] for r in changed)
        current = sum(r["second_label"] == r["current_label"] for r in changed)
        neither = sum(r["second_label"] not in (r["prior_label"], r["current_label"]) for r in changed)
        print(f"Changed human labels: {len(changed)}; third review agrees with prior {prior}, "
              f"current {current}, neither {neither}")
        for before, after in sorted({(r["prior_label"], r["current_label"]) for r in changed}):
            subset = [r for r in changed if (r["prior_label"], r["current_label"]) == (before, after)]
            print(f"  {before} -> {after}: {len(subset)}; third labels "
                  f"{dict(Counter(r['second_label'] for r in subset))}")
        controls = [r for r in rows if r["comparison_group"].startswith("stable_")]
        print(f"Unchanged controls: {len(controls)}; third review changed "
              f"{sum(r['second_label'] != r['current_label'] for r in controls)}")
        sample_out = DATA / "output" / sample
        current_rows = read_rows(sample_out / "manual" / "labels.csv")
        revised = {r["kernel_id"]: r["label"] for r in current_rows}
        if len(revised) != len(current_rows) or set(judgments) - set(revised):
            raise ValueError("Current manual labels and third-review IDs differ")
        revised.update(judgments)
        predicted_rows = read_rows(sample_out / "kernel_predictions.csv")
        predicted = {r["kernel_id"]: r["fdk_class"] for r in predicted_rows}
        if len(predicted) != len(predicted_rows) or set(predicted) - set(revised):
            raise ValueError("Model predictions and current manual kernel IDs differ")
        matched = [kid for kid in predicted if revised.get(kid) in ("healthy", "FDK")]
        if not matched:
            raise ValueError("No detected wheat kernels remain after reconciliation")
        tp = sum(revised[kid] == "FDK" and predicted[kid] == "FDK" for kid in matched)
        fn = sum(revised[kid] == "FDK" and predicted[kid] == "healthy" for kid in matched)
        fp = sum(revised[kid] == "healthy" and predicted[kid] == "FDK" for kid in matched)
        tn = sum(revised[kid] == "healthy" and predicted[kid] == "healthy" for kid in matched)
        wheat_all = sum(label in ("healthy", "FDK") for label in revised.values())
        manual_fdk_all = sum(label == "FDK" for label in revised.values())
        if not wheat_all:
            raise ValueError("No manually identified wheat kernels remain")
        result = {
            "sample_id": sample, "reconciled_kernels": len(rows),
            "unchanged_second_review_labels": len(revised) - len(rows),
            "manual_wheat_including_additions": wheat_all,
            "manual_FDK_including_additions": manual_fdk_all,
            "manual_FDK_percent_including_additions": 100 * manual_fdk_all / wheat_all,
            "matched_detected_wheat": len(matched),
            "manual_FDK_matched": tp + fn, "model_FDK_matched": tp + fp,
            "manual_FDK_percent_matched": 100 * (tp + fn) / len(matched),
            "model_FDK_percent_matched": 100 * (tp + fp) / len(matched),
            "TP": tp, "FN": fn, "FP": fp, "TN": tn,
            "interpretation": "Provisional hybrid: third review on selected changes/controls; "
                              "second review elsewhere. Not an independent whole-photo reference.",
        }
        write_csv(audit / "provisional_reconciled_comparison.csv", [result])
        print(f"Provisional hybrid on {len(matched)} same detected wheat: "
              f"manual {tp + fn}/{len(matched)} = {result['manual_FDK_percent_matched']:.2f}%, "
              f"model {tp + fp}/{len(matched)} = {result['model_FDK_percent_matched']:.2f}%; "
              f"TP/FN/FP/TN {tp}/{fn}/{fp}/{tn}")
        print("Third judgments apply only to the selected 82; the other labels remain from the newer review.")
    else:
        for group in ("FP", "FN", "TP", "TN"):
            selected = [r for r in rows if r["comparison_group"] == group]
            if selected:
                print(f"{group}: {len(selected)} reviewed; second-pass labels "
                      f"{dict(Counter(r['second_label'] for r in selected))}; "
                      f"{sum(int(r['changed_from_first_review']) for r in selected)} changed")
    print("Selected review only; earlier labels and model predictions remain unchanged.")
    print(f"Saved: {audit / 'adjudicated_subset.csv'}")


def compare_reviews(sample, prior_file):
    """Compare two human reviews by stable kernel ID without changing either."""
    current_file = DATA / "output" / sample / "manual" / "labels.csv"
    prior_file = Path(prior_file).expanduser().resolve()
    if not prior_file.is_file():
        raise FileNotFoundError(prior_file)
    prior_rows, current_rows = read_rows(prior_file), read_rows(current_file)
    prior = {r["kernel_id"]: r["label"] for r in prior_rows}
    current = {r["kernel_id"]: r["label"] for r in current_rows}
    if len(prior) != len(prior_rows) or len(current) != len(current_rows):
        raise ValueError("Duplicate kernel IDs in a manual review")
    common = sorted(set(prior) & set(current))
    if not common:
        raise ValueError("No shared kernel IDs. Confirm both files refer to the same saved masks.")
    out = DATA / "output" / sample / "manual" / "review_comparison"
    out.mkdir(exist_ok=True)
    rows = [{"sample_id": sample, "kernel_id": kid,
             "prior_label": prior[kid], "current_label": current[kid],
             "changed": int(prior[kid] != current[kid])}
            for kid in common]
    write_csv(out / "kernel_label_changes.csv", rows)
    transitions = Counter((r["prior_label"], r["current_label"]) for r in rows)
    write_csv(out / "label_transition_counts.csv", [
        {"prior_label": before, "current_label": after, "kernels": number}
        for (before, after), number in sorted(transitions.items())
    ])
    print(f"Shared IDs: {len(common)}; prior-only: {len(set(prior) - set(current))}; "
          f"current-only: {len(set(current) - set(prior))}")
    print(f"Changed labels: {sum(r['changed'] for r in rows)}")
    for (before, after), number in sorted(transitions.items()):
        if before != after:
            print(f"  {before} -> {after}: {number}")
    if set(prior) != set(current):
        print("Different ID sets: compare only shared detections; inspect unmatched IDs before interpreting totals.")
    print(f"Saved: {out / 'kernel_label_changes.csv'}")


def inspect_fp_cues(sample):
    """Compare blind-confirmed healthy false positives with healthy controls."""
    import tkinter as tk
    from PIL import ImageFilter, ImageTk

    out = DATA / "output" / sample
    audit = out / "manual" / "false_positive_spotcheck"
    queue = read_rows(audit / "queue.csv")
    judged = read_rows(audit / "judgments.csv")
    judgments = {r["kernel_id"]: r["second_label"] for r in judged}
    if len(judgments) != len(judged) or set(judgments) != {r["kernel_id"] for r in queue}:
        raise ValueError("Complete the blind FP spot check before inspecting cues")
    predictions = read_rows(out / "kernel_predictions.csv")
    pred = {r["kernel_id"]: r for r in predictions}
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    provenance = json.loads((out / "provenance.json").read_text())
    if file_sha256(source) != provenance["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    rgb = load_and_crop_image(source)
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archive:
        masks = {k: archive[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"]) != rgb.shape[:2]:
        raise ValueError("Mask archive and scored photo differ")
    selected = [r for r in queue if r["comparison_group"] in ("FP", "TN")
                and judgments[r["kernel_id"]] == "healthy"]
    records = []
    for item in selected:
        p = pred[item["kernel_id"]]
        i = int(p["index"])
        x1, y1, x2, y2 = tuple(map(int, p["bbox"].split(",")))
        if tuple(map(int, masks["bboxes"][i])) != (x1, y1, x2, y2):
            raise ValueError(f"Mask/bbox mismatch for {item['kernel_id']}")
        mask = restore_mask(masks, i)
        lab = rgb2lab(rgb[y1:y2, x1:x2])
        feats = build_qc_features({"confidence": 1.0, "mask": mask}, lab)
        records.append({"sample_id": sample, "kernel_id": item["kernel_id"],
                        "review_group": item["comparison_group"],
                        "blind_recheck": judgments[item["kernel_id"]],
                        "model_probability": p["fdk_probability"],
                        "bbox": p["bbox"], "index": i,
                        **{f: feats[f] for f in QC_FEATURES if f != "confidence"}})
    fp = [r for r in records if r["review_group"] == "FP"]
    tn = [r for r in records if r["review_group"] == "TN"]
    if not fp or not tn:
        raise ValueError("Need blind-confirmed healthy examples from both groups")
    features = ("L_q25", "L_q75", "a_q90", "b_iqr", "area_px", "minor_axis_px")
    scales = {f: max(1e-6, float(np.std([r[f] for r in records]))) for f in features}
    available = list(tn)
    pairs = []
    for f in sorted(fp, key=lambda r: r["kernel_id"]):
        pool = available or tn  # Reuse a control only when there are more FP than controls.
        control = min(pool, key=lambda r: (sum(((r[k] - f[k]) / scales[k]) ** 2
                                               for k in features), r["kernel_id"]))
        if control in available:
            available.remove(control)
        pairs.append((f, control))
    folder = audit / "visual_cue_comparison"
    folder.mkdir(exist_ok=True)
    write_csv(folder / "reviewed_healthy_features.csv", records)
    write_csv(folder / "matched_pairs.csv", [
        {"pair": n, "false_positive_id": f["kernel_id"], "control_id": c["kernel_id"],
         "matching_features": ",".join(features)} for n, (f, c) in enumerate(pairs, 1)])

    original = Image.fromarray(rgb)
    def tile(record):
        x1, y1, x2, y2 = map(int, record["bbox"].split(","))
        pad = max(28, int(max(x2 - x1, y2 - y1) * .35))
        region = (max(0, x1-pad), max(0, y1-pad), min(original.width, x2+pad),
                  min(original.height, y2+pad))
        crop = original.crop(region)
        local = restore_mask(masks, record["index"])
        outline = np.asarray(Image.fromarray((local * 255).astype('uint8')).filter(
            ImageFilter.MaxFilter(5))) > (local * 255)
        pixels = np.asarray(crop).copy()
        oy, ox = y1-region[1], x1-region[0]
        pixels[oy:oy+local.shape[0], ox:ox+local.shape[1]][outline] = (220, 35, 190)
        crop = Image.fromarray(pixels)
        crop.thumbnail((330, 220), Image.Resampling.LANCZOS)
        return crop

    notes_path = folder / "pair_observations.csv"
    old_notes = read_rows(notes_path) if notes_path.exists() else []
    notes = {int(r["pair"]): r for r in old_notes}
    if set(notes) - set(range(1, len(pairs)+1)):
        raise ValueError("Saved observations refer to a different pair queue")
    root = tk.Tk()
    root.title(f"Healthy kernel comparison — {sample}")
    heading = tk.Label(root, font=("Arial", 14))
    heading.pack(pady=8)
    panels = tk.Frame(root)
    panels.pack(padx=10)
    views = []
    for side in ("Model called FDK", "Model called healthy"):
        box = tk.Frame(panels)
        box.pack(side="left", padx=12)
        tk.Label(box, text=side, font=("Arial", 12)).pack()
        name = tk.Label(box)
        name.pack()
        picture = tk.Label(box, width=340, height=230)
        picture.pack()
        features_label = tk.Label(box, justify="left")
        features_label.pack()
        views.append((name, picture, features_label))
    tk.Label(root, text="What visual difference do you see? Leave blank if none is clear.").pack(pady=(12, 3))
    observation = tk.Entry(root, width=88)
    observation.pack(padx=10)
    status = tk.Label(root)
    status.pack(pady=6)
    controls = tk.Frame(root)
    controls.pack(pady=8)
    pos = [0]

    def save():
        number = pos[0] + 1
        f, c = pairs[pos[0]]
        notes[number] = {"pair": number, "false_positive_id": f["kernel_id"],
                         "control_id": c["kernel_id"], "observation": observation.get().strip()}
        write_csv(notes_path, [notes[n] for n in sorted(notes)])
        status.config(text=f"Saved observations for {len(notes)}/{len(pairs)} pairs")

    def show():
        f, c = pairs[pos[0]]
        heading.config(text=f"Pair {pos[0]+1}/{len(pairs)} — both labeled healthy on blind recheck")
        for rec, (name, picture, features_label) in zip((f, c), views):
            name.config(text=rec["kernel_id"])
            picture.image = ImageTk.PhotoImage(tile(rec))
            picture.config(image=picture.image)
            features_label.config(text=f"FDK probability {float(rec['model_probability']):.3f}\n"
                                  f"L25 {rec['L_q25']:.1f}; L75 {rec['L_q75']:.1f}; "
                                  f"a90 {rec['a_q90']:.1f}\n"
                                  f"Area {rec['area_px']:.0f}; width {rec['minor_axis_px']:.1f}")
        observation.delete(0, tk.END)
        observation.insert(0, notes.get(pos[0]+1, {}).get("observation", ""))
        status.config(text=f"Saved observations for {len(notes)}/{len(pairs)} pairs")

    def move(step):
        save()
        pos[0] = max(0, min(len(pairs)-1, pos[0]+step))
        show()

    tk.Button(controls, text="Previous", command=lambda: move(-1)).pack(side="left", padx=5)
    tk.Button(controls, text="Save note", command=save).pack(side="left", padx=5)
    tk.Button(controls, text="Next", command=lambda: move(1)).pack(side="left", padx=5)
    root.protocol("WM_DELETE_WINDOW", lambda: (save(), root.destroy()))
    show()
    root.mainloop()
    print(f"Confirmed healthy: {len(fp)} model-FDK and {len(tn)} model-healthy controls")
    for feature in features:
        print(f"  {feature}: FP median {np.median([r[feature] for r in fp]):.2f}; "
              f"control median {np.median([r[feature] for r in tn]):.2f}")
    print(f"Saved paired GUI observations and feature tables: {folder}")
    print("The controls are feature-matched from the same photo; inspect appearance before inferring a cause.")


def highlight_report(sample):
    """Exploratory glare measurements on blind-confirmed healthy spot-check kernels."""
    out = DATA / "output" / sample
    folder = out / "manual" / "false_positive_spotcheck"
    queue = read_rows(folder / "queue.csv")
    judgments = {r["kernel_id"]: r["second_label"] for r in read_rows(folder / "judgments.csv")}
    if set(judgments) != {r["kernel_id"] for r in queue}:
        raise ValueError("Complete the blind FP spot check first")
    predictions = read_rows(out / "kernel_predictions.csv")
    pred = {r["kernel_id"]: r for r in predictions}
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    if file_sha256(source) != json.loads((out / "provenance.json").read_text())["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    rgb = load_and_crop_image(source)
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archive:
        masks = {k: archive[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if tuple(masks["image_shape"]) != rgb.shape[:2] or len(masks["bboxes"]) != len(predictions):
        raise ValueError("Saved mask/photo dimensions disagree")
    result = []
    for item in queue:
        kid = item["kernel_id"]
        if judgments[kid] != "healthy":
            continue
        p = pred[kid]
        i = int(p["index"])
        bbox = tuple(map(int, p["bbox"].split(",")))
        if tuple(map(int, masks["bboxes"][i])) != bbox:
            raise ValueError(f"Mask/bbox mismatch for {kid}")
        x1,y1,x2,y2 = bbox
        local = restore_mask(masks, i)
        pixels = rgb[y1:y2,x1:x2][local]
        lab = rgb2lab(rgb[y1:y2,x1:x2])[local]
        lightness = lab[:,0]
        chroma = np.hypot(lab[:,1], lab[:,2])
        median = float(np.median(lightness))
        # Two fixed definitions, chosen before viewing group summaries.
        bright = lightness >= 70
        local_peak = lightness >= median + 18
        result.append({"sample_id": sample, "kernel_id": kid,
                       "group": item["comparison_group"],
                       "fdk_probability": p["fdk_probability"],
                       "L_median": median, "L_q90": float(np.quantile(lightness,.9)),
                       "bright_fraction_L70": float(np.mean(bright)),
                       "local_peak_fraction_Lplus18": float(np.mean(local_peak)),
                       "bright_low_chroma_fraction": float(np.mean(bright & (chroma < 12))),
                       "near_white_RGB_fraction": float(np.mean(np.min(pixels,axis=1) >= 180))})
    if not result:
        raise ValueError("No blind-confirmed healthy kernels found")
    destination = folder / "visual_cue_comparison" / "highlight_measurements.csv"
    write_csv(destination, result)
    print(f"Saved exploratory highlight measurements: {destination}")
    for key in ("L_median", "L_q90", "bright_fraction_L70",
                "local_peak_fraction_Lplus18", "bright_low_chroma_fraction",
                "near_white_RGB_fraction"):
        groups = {g: [float(r[key]) for r in result if r["group"] == g] for g in ("FP","TN")}
        if all(groups.values()):
            print(f"{key}: healthy FP median {np.median(groups['FP']):.3f}; "
                  f"healthy control median {np.median(groups['TN']):.3f}")
    print("These pixel metrics are glare proxies, not proof of a lighting cause; no model changed.")


def brightness_development_report(sample):
    """Read-only model-group brightness comparison on a labeled development photo."""
    if sample == "SDSU_IMG_0555":
        raise ValueError("0555 is the held-out diagnostic photo; use a separate development photo")
    out = DATA / "output" / sample
    manual = out / "manual"
    summary = read_rows(manual / "review_summary.csv")
    if len(summary) != 1 or summary[0]["status"] != "complete":
        raise ValueError("Finish the blind full-photo manual review first")
    labels_rows = read_rows(manual / "labels.csv")
    labels = {r["kernel_id"]: r["label"] for r in labels_rows}
    if len(labels) != len(labels_rows):
        raise ValueError("Duplicate manual kernel IDs")
    second = manual / "disagreement_audit"
    if (second / "queue.csv").exists() and (second / "judgments.csv").exists():
        queue = read_rows(second / "queue.csv")
        reviewed = read_rows(second / "judgments.csv")
        if {r["kernel_id"] for r in reviewed} == {r["kernel_id"] for r in queue}:
            labels.update({r["kernel_id"]: r["second_label"] for r in reviewed})
            print(f"Using {len(reviewed)} completed second-pass judgments where available")
        else:
            print("Second-pass audit incomplete; using original manual labels")
    predictions = read_rows(out / "kernel_predictions.csv")
    if any(r["kernel_id"] not in labels for r in predictions):
        raise ValueError("Manual labels do not cover the saved predictions")
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    if file_sha256(source) != json.loads((out / "provenance.json").read_text())["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    rgb = load_and_crop_image(source)
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archive:
        masks = {k: archive[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if tuple(masks["image_shape"]) != rgb.shape[:2] or len(masks["bboxes"]) != len(predictions):
        raise ValueError("Saved mask/photo dimensions disagree")
    records = []
    for p in predictions:
        kid = p["kernel_id"]
        human = labels[kid]
        if human not in ("healthy", "FDK"):
            continue
        call = p["fdk_class"]
        group = ("T" if human == call else "F") + ("P" if call == "FDK" else "N")
        i = int(p["index"])
        x1,y1,x2,y2 = tuple(map(int,p["bbox"].split(",")))
        if tuple(map(int,masks["bboxes"][i])) != (x1,y1,x2,y2):
            raise ValueError(f"Mask/bbox mismatch for {kid}")
        local = restore_mask(masks,i)
        lab = rgb2lab(rgb[y1:y2,x1:x2])[local]
        lightness = lab[:,0]
        records.append({"sample_id": sample,"kernel_id": kid,"comparison_group": group,
                        "manual_label": human,"model_call": call,
                        "fdk_probability": p["fdk_probability"],
                        "L_q25": float(np.quantile(lightness,.25)),
                        "L_median": float(np.median(lightness)),
                        "L_q75": float(np.quantile(lightness,.75)),
                        "L_q90": float(np.quantile(lightness,.9)),
                        "bright_fraction_L70": float(np.mean(lightness >= 70)),
                        "local_peak_fraction_Lplus18": float(np.mean(lightness >= np.median(lightness)+18))})
    destination = out / "manual" / "development_brightness_check.csv"
    write_csv(destination, records)
    print(f"Saved: {destination}")
    for group in ("TN","FP","TP","FN"):
        subset = [r for r in records if r["comparison_group"] == group]
        if subset:
            print(f"{group} ({len(subset)}): median L25 {np.median([r['L_q25'] for r in subset]):.2f}, "
                  f"L50 {np.median([r['L_median'] for r in subset]):.2f}, "
                  f"L75 {np.median([r['L_q75'] for r in subset]):.2f}, "
                  f"bright fraction {np.median([r['bright_fraction_L70'] for r in subset]):.3f}")
    print("Exploratory group comparison; no threshold or model was changed.")


def texture_development_test(sample):
    """Spatial held-out folds within one development photo; never save a model."""
    from scipy.ndimage import binary_erosion, gaussian_filter, sobel
    from sklearn.model_selection import GroupKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression

    if sample == "SDSU_IMG_0555":
        raise ValueError("The inspected 0555 photo is held out from candidate training")
    out = DATA / "output" / sample
    manual = out / "manual"
    summary = read_rows(manual / "review_summary.csv")
    if len(summary) != 1 or summary[0]["status"] != "complete":
        raise ValueError("Complete blind manual labeling first")
    label_rows = read_rows(manual / "labels.csv")
    labels = {r["kernel_id"]: r["label"] for r in label_rows}
    if len(labels) != len(label_rows):
        raise ValueError("Duplicate manual IDs")
    audit = manual / "disagreement_audit"
    if (audit / "queue.csv").exists() and (audit / "judgments.csv").exists():
        queue, reviewed = read_rows(audit / "queue.csv"), read_rows(audit / "judgments.csv")
        if {r["kernel_id"] for r in queue} == {r["kernel_id"] for r in reviewed}:
            labels.update({r["kernel_id"]: r["second_label"] for r in reviewed})
            print(f"Using {len(reviewed)} completed second-pass judgments")
    predictions = read_rows(out / "kernel_predictions.csv")
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    if file_sha256(source) != json.loads((out / "provenance.json").read_text())["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    rgb = load_and_crop_image(source)
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archive:
        masks = {k: archive[k] for k in ("pixels", "offsets", "bboxes", "image_shape")}
    if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"]) != rgb.shape[:2]:
        raise ValueError("Saved masks differ from scored photo")
    base_features = QC_FEATURES[1:]
    extras = ("L_inner_std", "L_inner_gradient_q75", "L_inner_residual_q75",
              "a_inner_std", "b_inner_std")
    records = []
    for row in predictions:
        kid = row["kernel_id"]
        if labels.get(kid) not in ("healthy", "FDK"):
            continue
        i = int(row["index"])
        bbox = tuple(map(int, row["bbox"].split(",")))
        if bbox != tuple(map(int, masks["bboxes"][i])):
            raise ValueError(f"Saved mask misalignment at {kid}")
        x1,y1,x2,y2 = bbox
        mask = restore_mask(masks, i)
        lab = rgb2lab(rgb[y1:y2,x1:x2])
        original = build_qc_features({"mask": mask,"confidence": 1.0},lab)
        inner = binary_erosion(mask, iterations=3)
        if int(inner.sum()) < 20:
            inner = mask
        L = lab[:,:,0]
        # Restrict comparisons to mask interior to avoid measuring the white background edge.
        gradients = np.hypot(sobel(L,axis=0)/8, sobel(L,axis=1)/8)
        residual = np.abs(L - gaussian_filter(L, sigma=2))
        derived = {"L_inner_std": float(np.std(L[inner])),
                   "L_inner_gradient_q75": float(np.quantile(gradients[inner],.75)),
                   "L_inner_residual_q75": float(np.quantile(residual[inner],.75)),
                   "a_inner_std": float(np.std(lab[:,:,1][inner])),
                   "b_inner_std": float(np.std(lab[:,:,2][inner]))}
        cx,cy = (x1+x2)/2,(y1+y2)/2
        region = min(3,int(cx*4/rgb.shape[1])) + 4*min(3,int(cy*4/rgb.shape[0]))
        records.append({"sample_id":sample,"kernel_id":kid,"manual_label":labels[kid],
                        "frozen_call":row["fdk_class"],"region":region,
                        **{k:original[k] for k in base_features},**derived})
    if not records:
        raise ValueError("No confirmed wheat labels")
    X0 = np.array([[r[k] for k in base_features] for r in records],float)
    X1 = np.array([[r[k] for k in (*base_features,*extras)] for r in records],float)
    y = np.array([r["manual_label"] == "FDK" for r in records],int)
    groups = np.array([r["region"] for r in records])
    if len(np.unique(groups)) < 4 or min(np.bincount(y)) < 8 or not np.isfinite(X1).all():
        raise ValueError("Insufficient spatial groups, classes, or finite features for this experiment")
    folds = list(GroupKFold(n_splits=4).split(X0,y,groups))
    if any(len(np.unique(y[train])) < 2 for train,_ in folds):
        raise ValueError("A spatial training fold has only one class")
    output = []
    for name,X in (("original_30_retrained",X0),("original_30_plus_5_texture",X1)):
        for fold,(train,test) in enumerate(folds,1):
            model = make_pipeline(StandardScaler(),LogisticRegression(max_iter=2000))
            model.fit(X[train],y[train])
            probability = model.predict_proba(X[test])[:,1]
            for idx,p in zip(test,probability):
                output.append({"model":name,"fold":fold,"sample_id":sample,
                               "kernel_id":records[idx]["kernel_id"],"manual_FDK":int(y[idx]),
                               "probability":float(p),"predicted_FDK":int(p>=.5)})
    destination = manual / "texture_spatial_fold_predictions.csv"
    write_csv(destination,output)
    for name in ("original_30_retrained","original_30_plus_5_texture"):
        subset = [r for r in output if r["model"] == name]
        truth=np.array([r["manual_FDK"] for r in subset]); predicted=np.array([r["predicted_FDK"] for r in subset])
        tp=int(np.sum((truth==1)&(predicted==1))); fn=int(np.sum((truth==1)&(predicted==0)))
        fp=int(np.sum((truth==0)&(predicted==1))); tn=int(np.sum((truth==0)&(predicted==0)))
        print(f"{name}: predicted {100*np.mean(predicted):.2f}% vs manual {100*np.mean(truth):.2f}%; "
              f"TP/FN/FP/TN {tp}/{fn}/{fp}/{tn}; balanced accuracy "
              f"{(tp/(tp+fn)+tn/(tn+fp))/2:.3f}")
    print(f"Saved fold predictions: {destination}")
    print("Within-photo exploratory comparison only. The frozen model and held-out photos were untouched.")


def candidate_directory(name):
    if (not name or not all(c.isalnum() or c == "_" for c in name)
            or not name.startswith("manual_sdsu_candidate_v")):
        raise ValueError("Candidate name must look like manual_sdsu_candidate_v2")
    return DATA / "output" / "model_development" / name


RELATIVE_SOURCE_FEATURES = ("L_q25", "L_median", "a_q90", "a_q80range",
                            "area_px", "minor_axis_px")
RELATIVE_FEATURES = [f"relative_{feature}" for feature in RELATIVE_SOURCE_FEATURES]
TEXTURE_FEATURES = ["L_inner_std", "L_inner_gradient_q75", "L_inner_residual_q75",
                    "a_inner_std", "b_inner_std"]
CREASE_FEATURES = ["crease_contrast", "crease_continuity"]


def kernel_texture_features(mask, lab):
    """Measure interior variation on the same Lab crop and saved kernel mask."""
    from scipy.ndimage import binary_erosion, gaussian_filter, sobel
    inner=binary_erosion(mask,iterations=3)
    if int(inner.sum()) < 20:
        inner=mask
    L=lab[:,:,0]
    gradient=np.hypot(sobel(L,axis=0)/8,sobel(L,axis=1)/8)
    residual=np.abs(L-gaussian_filter(L,sigma=2))
    return {"L_inner_std":float(np.std(L[inner])),
            "L_inner_gradient_q75":float(np.quantile(gradient[inner],.75)),
            "L_inner_residual_q75":float(np.quantile(residual[inner],.75)),
            "a_inner_std":float(np.std(lab[:,:,1][inner])),
            "b_inner_std":float(np.std(lab[:,:,2][inner]))}


def kernel_crease_features(mask, lab):
    """Measure a dark longitudinal center line relative to its flanking interior."""
    from scipy.ndimage import binary_erosion
    inner=binary_erosion(mask,iterations=2)
    if int(inner.sum()) < 50:
        inner=mask
    yy,xx=np.nonzero(inner)
    if len(xx) < 50:
        return {name:0.0 for name in CREASE_FEATURES}
    coordinates=np.column_stack((xx,yy)).astype(float)
    centered=coordinates-coordinates.mean(axis=0)
    eigenvalues,eigenvectors=np.linalg.eigh(np.cov(centered,rowvar=False))
    major=eigenvectors[:,np.argmax(eigenvalues)]
    minor=np.array((-major[1],major[0]))
    along=centered@major
    across=centered@minor
    long_scale=float(np.quantile(np.abs(along),.95))
    width=float(np.quantile(np.abs(across),.90))
    if long_scale < 5 or width < 3:
        return {name:0.0 for name in CREASE_FEATURES}
    brightness=lab[:,:,0][inner]
    contrasts=[]
    for position in np.linspace(-.72,.72,11):
        slice_mask=np.abs(along-position*long_scale) < .10*long_scale
        center=slice_mask & (np.abs(across)<.18*width)
        flank=slice_mask & (np.abs(across)>.38*width) & (np.abs(across)<.78*width)
        if center.sum()<4 or flank.sum()<8:
            continue
        contrasts.append(float(np.median(brightness[flank])-np.median(brightness[center])))
    if len(contrasts)<5:
        return {name:0.0 for name in CREASE_FEATURES}
    return {"crease_contrast":float(np.median(contrasts)),
            "crease_continuity":float(np.mean(np.asarray(contrasts)>3.0))}


def add_photo_relative_features(records):
    """Compare each detection to all detections in its photo, without manual labels."""
    if not records:
        raise ValueError("No detections for photo-relative feature calculation")
    for feature in RELATIVE_SOURCE_FEATURES:
        values=np.asarray([r[feature] for r in records],float)
        if not np.isfinite(values).all():
            raise ValueError(f"Undefined photo-relative source feature: {feature}")
        median=float(np.median(values))
        scale=max(float(np.quantile(values,.75)-np.quantile(values,.25)),1e-6)
        for record,value in zip(records,values):
            record[f"relative_{feature}"]=float((value-median)/scale)
    return records


def train_manual_candidate(samples, candidate_name="manual_sdsu_candidate_v1", positive_weight=1.0,
                           model_type="logistic"):
    """Train a separate SDSU candidate with image-held-out checks and reviewed labels."""
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier

    samples = list(dict.fromkeys(samples))
    if not np.isfinite(positive_weight) or not 1.0 <= positive_weight <= 2.0:
        raise ValueError("FDK positive weight must be between 1.0 and 2.0")
    if model_type not in ("logistic", "boosted", "relative_logistic", "relative_texture_logistic", "relative_crease_logistic", "relative_texture_review_weighted"):
        raise ValueError("Unsupported model type")
    if len(samples) < 3:
        raise ValueError("At least three independently labeled photos are needed for image-held-out comparison")
    destination=candidate_directory(candidate_name)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Existing candidate is locked: {destination}; use a new candidate name")
    relative = model_type in ("relative_logistic", "relative_texture_logistic", "relative_crease_logistic", "relative_texture_review_weighted")
    texture = model_type in ("relative_texture_logistic", "relative_texture_review_weighted")
    crease = model_type == "relative_crease_logistic"
    review_weighted = model_type == "relative_texture_review_weighted"
    features = (QC_FEATURES[1:] + (RELATIVE_FEATURES if relative else [])
                + (TEXTURE_FEATURES if texture else []) + (CREASE_FEATURES if crease else []))
    all_records = []
    review_sources = {}
    for sample in samples:
        out = DATA / "output" / sample
        manual = out / "manual"
        summary = read_rows(manual / "review_summary.csv")
        if len(summary) != 1 or summary[0]["status"] != "complete":
            raise ValueError(f"Complete blind labels for {sample} first")
        initial_rows = read_rows(manual / "labels.csv")
        labels = {r["kernel_id"]:r["label"] for r in initial_rows}
        if len(labels) != len(initial_rows):
            raise ValueError(f"Duplicate label IDs in {sample}")
        applied = []
        changed_ids = set()
        for subfolder in ("disagreement_audit", "review_reconciliation", "false_positive_spotcheck"):
            qpath = manual / subfolder / "queue.csv"
            jpath = manual / subfolder / "judgments.csv"
            if not qpath.is_file() and not jpath.is_file():
                continue
            if not qpath.is_file() or not jpath.is_file():
                raise ValueError(f"Incomplete {subfolder} review in {sample}")
            queue, judged = read_rows(qpath),read_rows(jpath)
            updates = {r["kernel_id"]:r["second_label"] for r in judged}
            if (len(updates) != len(judged) or set(updates) != {r["kernel_id"] for r in queue}
                    or set(updates)-set(labels)):
                raise ValueError(f"Incomplete or mismatched {subfolder} review in {sample}")
            changed_ids.update(kid for kid,label in updates.items() if label != labels[kid])
            labels.update(updates)
            applied.append(f"{subfolder}:{len(updates)}")
        for review_dir in sorted(manual.glob("manual_sdsu_candidate_v*_error_review")):
            qpath,jpath=review_dir / "queue.csv",review_dir / "judgments.csv"
            if not qpath.is_file() or not jpath.is_file():
                raise ValueError(f"Incomplete candidate review: {review_dir}")
            queue,judged=read_rows(qpath),read_rows(jpath)
            updates={r["kernel_id"]:r["second_label"] for r in judged}
            if (len(updates)!=len(judged) or set(updates)!={r["kernel_id"] for r in queue}
                    or set(updates)-set(labels)):
                raise ValueError(f"Incomplete or mismatched candidate review: {review_dir}")
            changed_ids.update(kid for kid,label in updates.items() if label != labels[kid])
            labels.update(updates)
            applied.append(f"{review_dir.name}:{len(updates)}")
        predictions = read_rows(out / "kernel_predictions.csv")
        if len({r["kernel_id"] for r in predictions}) != len(predictions) or any(
                r["kernel_id"] not in labels for r in predictions):
            raise ValueError(f"Missing or duplicate scored kernel IDs in {sample}")
        source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
        provenance = json.loads((out / "provenance.json").read_text())
        if file_sha256(source) != provenance["source_sha256"]:
            raise ValueError(f"Original photo changed since scoring {sample}")
        rgb = load_and_crop_image(source)
        lab = rgb2lab(rgb)
        with np.load(out / "kernel_masks.npz", allow_pickle=False) as archived:
            masks = {k:archived[k] for k in ("pixels","offsets","bboxes","image_shape")}
        if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"]) != rgb.shape[:2]:
            raise ValueError(f"Saved masks mismatch photo {sample}")
        accepted = 0
        photo_records = []
        for row in predictions:
            kid = row["kernel_id"]
            if not relative and labels[kid] not in ("healthy","FDK"):
                continue
            i = int(row["index"])
            x1,y1,x2,y2 = tuple(map(int,row["bbox"].split(",")))
            if tuple(map(int,masks["bboxes"][i])) != (x1,y1,x2,y2):
                raise ValueError(f"Mask alignment differs for {kid}")
            mask = restore_mask(masks,i)
            crop_lab=lab[y1:y2,x1:x2]
            record = build_qc_features({"mask":mask,"confidence":1.0},crop_lab)
            photo_records.append({"sample_id":sample,"kernel_id":kid,"manual_label":labels[kid],
                                  "review_label_changed":kid in changed_ids,
                                  "frozen_probability":float(row["fdk_probability"]),
                                  **{feature:record[feature] for feature in QC_FEATURES[1:]},
                                  **(kernel_texture_features(mask,crop_lab) if texture else {}),
                                  **(kernel_crease_features(mask,crop_lab) if crease else {})})
        if relative:
            add_photo_relative_features(photo_records)
        selected=[r for r in photo_records if r["manual_label"] in ("healthy","FDK")]
        all_records.extend(selected)
        accepted=len(selected)
        if accepted < 20:
            raise ValueError(f"Too few intact labeled wheat kernels in {sample}")
        review_sources[sample] = {"source_sha256":provenance["source_sha256"],
                                  "reviews_applied":applied,"wheat_count":accepted,
                                  "changed_label_wheat":sum(r["review_label_changed"] for r in selected)}
        print(f"{sample}: {accepted} intact labeled wheat; "
              f"{review_sources[sample]['changed_label_wheat']} changed-label wheat; "
              f"reviews {applied or ['initial_only']}")
    X = np.asarray([[r[k] for k in features] for r in all_records],float)
    y = np.asarray([r["manual_label"] == "FDK" for r in all_records],int)
    group = np.asarray([r["sample_id"] for r in all_records])
    changed = np.asarray([r["review_label_changed"] for r in all_records],bool)
    if not np.isfinite(X).all():
        raise ValueError("Undefined training features")
    def fit(indices):
        if len(np.unique(y[indices])) < 2:
            raise ValueError("Training fold contains only one class")
        weights=np.where(y[indices] == 1, positive_weight, 1.0)
        if review_weighted:
            weights=weights*np.where(changed[indices],0.5,1.0)
        if model_type in ("logistic", "relative_logistic", "relative_texture_logistic", "relative_crease_logistic", "relative_texture_review_weighted"):
            model = make_pipeline(StandardScaler(),LogisticRegression(max_iter=2000))
            weight_arg = {"logisticregression__sample_weight":weights}
        else:
            model = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=7,
                learning_rate=.05, l2_regularization=10, random_state=0)
            weight_arg = {"sample_weight":weights}
        model.fit(X[indices],y[indices],**weight_arg)
        return model
    predictions = []
    summary = []
    for held in samples:
        train = np.flatnonzero(group != held)
        test = np.flatnonzero(group == held)
        model = fit(train)
        candidate_p = model.predict_proba(X[test])[:,1]
        for idx,p in zip(test,candidate_p):
            predictions.append({"sample_id":held,"kernel_id":all_records[idx]["kernel_id"],
                                "manual_FDK":int(y[idx]),
                                "frozen_probability":all_records[idx]["frozen_probability"],
                                "candidate_probability":float(p),
                                "frozen_FDK":int(all_records[idx]["frozen_probability"] >= .5),
                                "candidate_FDK":int(p >= .5)})
        for model_name,key in (("frozen_468","frozen_FDK"),("manual_candidate_LOIO","candidate_FDK")):
            actual=y[test]
            calls=np.array([r[key] for r in predictions if r["sample_id"] == held],int)
            tp=int(np.sum((actual==1)&(calls==1)))
            fn=int(np.sum((actual==1)&(calls==0)))
            fp=int(np.sum((actual==0)&(calls==1)))
            tn=int(np.sum((actual==0)&(calls==0)))
            summary.append({"sample_id":held,"model":model_name,"wheat_count":len(test),
                            "manual_FDK":int(actual.sum()),"predicted_FDK":int(calls.sum()),
                            "manual_percent":100*float(actual.mean()),
                            "model_percent":100*float(calls.mean()),
                            "TP":tp,"FN":fn,"FP":fp,"TN":tn,
                            "balanced_accuracy":(tp/(tp+fn)+tn/(tn+fp))/2})
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination / "image_held_out_predictions.csv",predictions)
    write_csv(destination / "image_held_out_summary.csv",summary)
    final=fit(np.arange(len(y)))
    bundle={"model":final,"features":features,"threshold":.5,
            "model_type":model_type,
            "changed_label_training_weight":0.5 if review_weighted else 1.0,
            "changed_label_wheat_count":int(changed.sum()),
            "positive_weight":positive_weight,
            "candidate_name":candidate_name,
            "training_samples":samples,"training_wheat":len(y),"review_sources":review_sources,
            "status":"experimental; every listed sample used in final fit"}
    model_path=destination / "candidate.joblib"
    temp=model_path.with_suffix(".joblib.tmp")
    joblib.dump(bundle,temp)
    temp.replace(model_path)
    for row in summary:
        print(f"{row['sample_id']} {row['model']}: {row['model_percent']:.2f}% vs "
              f"manual {row['manual_percent']:.2f}%; TP/FN/FP/TN "
              f"{row['TP']}/{row['FN']}/{row['FP']}/{row['TN']}")
    for name in ("frozen_468","manual_candidate_LOIO"):
        subset=[r for r in summary if r["model"] == name]
        mae=np.mean([abs(r["model_percent"]-r["manual_percent"]) for r in subset])
        ba=np.mean([r["balanced_accuracy"] for r in subset])
        print(f"{name}: equal-image mean absolute rate error {mae:.2f} points; "
              f"balanced accuracy {ba:.3f}")
    print(f"Saved experimental candidate and image-held-out results: {destination}")
    print(f"Model: {model_type}; FDK training weight: {positive_weight:.2f} "
          "(healthy weight 1.00); cutoff remains 0.50")
    if review_weighted:
        print(f"Reduced weight to 0.50 for {int(changed.sum())}/{len(y)} wheat kernels "
              "whose label changed during a completed review")
    print("All listed photos train the final candidate. LOIO results are development checks, not new prospective validation.")


def evaluate_manual_candidate(sample, candidate_name="manual_sdsu_candidate_v1"):
    """Evaluate locked candidate on a fully labeled photo absent from its training set."""
    candidate_path = candidate_directory(candidate_name) / "candidate.joblib"
    bundle = joblib.load(candidate_path)
    if sample in bundle["training_samples"]:
        raise ValueError(f"{sample} trained this candidate; choose an image outside its training set")
    allowed_features=(QC_FEATURES[1:], QC_FEATURES[1:] + RELATIVE_FEATURES,
                      QC_FEATURES[1:] + RELATIVE_FEATURES + TEXTURE_FEATURES,
                      QC_FEATURES[1:] + RELATIVE_FEATURES + CREASE_FEATURES)
    if list(bundle["features"]) not in allowed_features or float(bundle["threshold"]) != .5:
        raise ValueError("Candidate feature order or threshold differs from this pipeline")
    out = DATA / "output" / sample
    manual = out / "manual"
    summary = read_rows(manual / "review_summary.csv")
    if len(summary) != 1 or summary[0]["status"] != "complete":
        raise ValueError("Complete the blind manual GUI review before revealing candidate predictions")
    initial = read_rows(manual / "labels.csv")
    labels = {r["kernel_id"]:r["label"] for r in initial}
    predictions = read_rows(out / "kernel_predictions.csv")
    if len(labels) != len(initial) or any(r["kernel_id"] not in labels for r in predictions):
        raise ValueError("Missing or duplicate manual label IDs")
    source = Path(read_rows(out / "image_prediction.csv")[0]["image_path"])
    provenance = json.loads((out / "provenance.json").read_text())
    if file_sha256(source) != provenance["source_sha256"]:
        raise ValueError("Original photo changed since scoring")
    rgb = load_and_crop_image(source)
    lab = rgb2lab(rgb)
    with np.load(out / "kernel_masks.npz", allow_pickle=False) as archive:
        masks = {k:archive[k] for k in ("pixels","offsets","bboxes","image_shape")}
    if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"]) != rgb.shape[:2]:
        raise ValueError("Saved mask/photo mismatch")
    feature_rows=[]
    for row in predictions:
        i=int(row["index"])
        x1,y1,x2,y2=tuple(map(int,row["bbox"].split(",")))
        if tuple(map(int,masks["bboxes"][i])) != (x1,y1,x2,y2):
            raise ValueError(f"Saved mask alignment differs for {row['kernel_id']}")
        mask=restore_mask(masks,i)
        crop_lab=lab[y1:y2,x1:x2]
        features=build_qc_features({"mask":mask,"confidence":1.0},crop_lab)
        feature_rows.append({**{k:features[k] for k in QC_FEATURES[1:]},
                             **(kernel_texture_features(mask,crop_lab) if
                                list(bundle["features"]) == allowed_features[2] else {}),
                             **(kernel_crease_features(mask,crop_lab) if
                                list(bundle["features"]) == allowed_features[3] else {})})
    if list(bundle["features"]) in allowed_features[1:]:
        add_photo_relative_features(feature_rows)
    X=np.asarray([[record[k] for k in bundle["features"]] for record in feature_rows],float)
    if not np.isfinite(X).all():
        raise ValueError("Undefined candidate features")
    probability=bundle["model"].predict_proba(X)[:,1]
    evaluation=[]
    for row,p in zip(predictions,probability):
        kid=row["kernel_id"]
        evaluation.append({"sample_id":sample,"kernel_id":kid,"manual_label":labels[kid],
                           "frozen_probability":row["fdk_probability"],
                           "candidate_probability":float(p),
                           "frozen_call":row["fdk_class"],
                           "candidate_call":"FDK" if p>=.5 else "healthy"})
    wheat=[r for r in evaluation if r["manual_label"] in ("healthy","FDK")]
    if not wheat:
        raise ValueError("No manually confirmed wheat")
    comparison_folder = ("candidate_v1_independent_comparison" if
                         candidate_name == "manual_sdsu_candidate_v1" else
                         f"{candidate_name}_independent_comparison")
    destination=out / "manual" / comparison_folder
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Candidate comparison already saved: {destination}")
    destination.mkdir(parents=True)
    write_csv(destination / "kernel_predictions.csv",evaluation)
    comparisons=[]
    for name,key in (("frozen_468","frozen_call"),(candidate_name,"candidate_call")):
        tp=sum(r["manual_label"]=="FDK" and r[key]=="FDK" for r in wheat)
        fn=sum(r["manual_label"]=="FDK" and r[key]=="healthy" for r in wheat)
        fp=sum(r["manual_label"]=="healthy" and r[key]=="FDK" for r in wheat)
        tn=sum(r["manual_label"]=="healthy" and r[key]=="healthy" for r in wheat)
        comparisons.append({"sample_id":sample,"model":name,"confirmed_wheat":len(wheat),
                            "manual_FDK":tp+fn,"model_FDK":tp+fp,
                            "manual_percent":100*(tp+fn)/len(wheat),
                            "model_percent_on_same_wheat":100*(tp+fp)/len(wheat),
                            "automatic_all_object_percent":100*sum(r[key]=="FDK" for r in evaluation)/len(evaluation),
                            "TP":tp,"FN":fn,"FP":fp,"TN":tn,
                            "unsure":sum(r["manual_label"]=="unsure" for r in evaluation),
                            "weed_seed":sum(r["manual_label"]=="weed_seed" for r in evaluation),
                            "other_object":sum(r["manual_label"]=="other_object" for r in evaluation)})
    write_csv(destination / "comparison.csv",comparisons)
    for row in comparisons:
        print(f"{row['model']}: {row['model_percent_on_same_wheat']:.2f}% "
              f"vs manual {row['manual_percent']:.2f}% on {len(wheat)} same wheat; "
              f"TP/FN/FP/TN {row['TP']}/{row['FN']}/{row['FP']}/{row['TN']}")
    print(f"Saved locked comparison: {destination}")
    print("Do not retrain the candidate on this photo until after recording the comparison.")


def candidate_threshold_development(candidate_name):
    """Explore fixed cutoffs on saved image-held-out development probabilities."""
    folder = candidate_directory(candidate_name)
    bundle = joblib.load(folder / "candidate.joblib")
    rows = read_rows(folder / "image_held_out_predictions.csv")
    samples = bundle["training_samples"]
    if not rows or set(r["sample_id"] for r in rows) != set(samples):
        raise ValueError("Image-held-out predictions missing training photos")
    if len({(r["sample_id"],r["kernel_id"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate saved fold predictions")
    output = []
    for cutoff in (.35,.40,.45,.50):
        for sample in samples:
            subset=[r for r in rows if r["sample_id"] == sample]
            truth=np.array([int(r["manual_FDK"]) for r in subset],int)
            probability=np.array([float(r["candidate_probability"]) for r in subset],float)
            if (not len(truth) or set(truth) != {0,1} or not np.isfinite(probability).all()
                    or np.any((probability < 0) | (probability > 1))):
                raise ValueError(f"Invalid image-held-out probabilities or labels: {sample}")
            calls=probability >= cutoff
            tp=int(np.sum((truth==1)&calls)); fn=int(np.sum((truth==1)&~calls))
            fp=int(np.sum((truth==0)&calls)); tn=int(np.sum((truth==0)&~calls))
            output.append({"candidate":candidate_name,"sample_id":sample,"cutoff":cutoff,
                           "confirmed_wheat":len(truth),"manual_FDK":int(truth.sum()),
                           "predicted_FDK":int(calls.sum()),"manual_percent":100*truth.mean(),
                           "model_percent":100*calls.mean(),"TP":tp,"FN":fn,"FP":fp,"TN":tn,
                           "sensitivity":tp/(tp+fn),"specificity":tn/(tn+fp),
                           "balanced_accuracy":(tp/(tp+fn)+tn/(tn+fp))/2,
                           "absolute_rate_error":100*abs(calls.mean()-truth.mean())})
    destination=folder / "threshold_development.csv"
    write_csv(destination,output)
    for cutoff in (.35,.40,.45,.50):
        group=[r for r in output if r["cutoff"]==cutoff]
        print(f"cutoff {cutoff:.2f}: equal-image mean absolute rate error "
              f"{np.mean([r['absolute_rate_error'] for r in group]):.2f} pp; "
              f"sensitivity {np.mean([r['sensitivity'] for r in group]):.3f}; "
              f"specificity {np.mean([r['specificity'] for r in group]):.3f}; "
              f"balanced accuracy {np.mean([r['balanced_accuracy'] for r in group]):.3f}")
        for r in group:
            print(f"  {r['sample_id']}: {r['model_percent']:.2f}% vs manual "
                  f"{r['manual_percent']:.2f}%; TP/FN/FP/TN "
                  f"{r['TP']}/{r['FN']}/{r['FP']}/{r['TN']}")
    print(f"Saved exploratory cutoff comparison: {destination}")
    print("Cutoffs were inspected on development folds; the locked model still uses 0.50. "
          "Do not treat a cutoff chosen here as independently validated.")


def score_saved_candidate(sample, candidate_name):
    """Score saved masks with an experimental candidate, preserving frozen files."""
    model_path=candidate_directory(candidate_name) / "candidate.joblib"
    bundle=joblib.load(model_path)
    allowed=(QC_FEATURES[1:], QC_FEATURES[1:] + RELATIVE_FEATURES,
             QC_FEATURES[1:] + RELATIVE_FEATURES + TEXTURE_FEATURES,
             QC_FEATURES[1:] + RELATIVE_FEATURES + CREASE_FEATURES)
    if list(bundle["features"]) not in allowed or float(bundle["threshold"]) != .5:
        raise ValueError("Unsupported candidate features or cutoff")
    out=DATA / "output" / sample
    destination=out / "candidate_scores" / candidate_name
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Candidate score already exists: {destination}")
    predictions=read_rows(out / "kernel_predictions.csv")
    if (not predictions or len({r["kernel_id"] for r in predictions}) != len(predictions)
            or {int(r["index"]) for r in predictions} != set(range(len(predictions)))):
        raise ValueError("Missing or duplicate frozen scored kernels")
    image_rows=read_rows(out / "image_prediction.csv")
    if len(image_rows) != 1 or image_rows[0]["sample_id"] != sample:
        raise ValueError("Saved photo summary does not match sample")
    source=Path(image_rows[0]["image_path"])
    provenance=json.loads((out / "provenance.json").read_text())
    if file_sha256(source) != provenance["source_sha256"]:
        raise ValueError("Original photo changed since frozen scoring")
    rgb=load_and_crop_image(source)
    lab=rgb2lab(rgb)
    with np.load(out / "kernel_masks.npz",allow_pickle=False) as archive:
        masks={k:archive[k] for k in ("pixels","offsets","bboxes","image_shape")}
    if len(masks["bboxes"]) != len(predictions) or tuple(masks["image_shape"]) != rgb.shape[:2]:
        raise ValueError("Saved masks do not match scored photo")
    feature_rows=[]
    local_masks=[]
    for row in predictions:
        index=int(row["index"])
        x1,y1,x2,y2=tuple(map(int,row["bbox"].split(",")))
        if index < 0 or index >= len(predictions) or tuple(map(int,masks["bboxes"][index])) != (x1,y1,x2,y2):
            raise ValueError(f"Mask alignment differs for {row['kernel_id']}")
        mask=restore_mask(masks,index)
        local_masks.append(mask)
        crop_lab=lab[y1:y2,x1:x2]
        values=build_qc_features({"mask":mask,"confidence":1.0},crop_lab)
        feature_rows.append({**{k:values[k] for k in QC_FEATURES[1:]},
                             **(kernel_texture_features(mask,crop_lab) if
                                list(bundle["features"]) == allowed[2] else {}),
                             **(kernel_crease_features(mask,crop_lab) if
                                list(bundle["features"]) == allowed[3] else {})})
    if list(bundle["features"]) in allowed[1:]:
        add_photo_relative_features(feature_rows)
    X=np.asarray([[record[k] for k in bundle["features"]] for record in feature_rows],float)
    if not np.isfinite(X).all():
        raise ValueError("Undefined candidate features; no score written")
    probability=bundle["model"].predict_proba(X)[:,1]
    if not np.isfinite(probability).all():
        raise ValueError("Undefined candidate probabilities; no score written")
    calls=probability >= .5
    rows=[]
    canvas=rgb.copy()
    for row,mask,p,called in zip(predictions,local_masks,probability,calls):
        x1,y1,x2,y2=tuple(map(int,row["bbox"].split(",")))
        canvas[y1:y2,x1:x2][boundary(mask)]=(235,20,30) if called else (30,210,50)
        rows.append({"sample_id":sample,"kernel_id":row["kernel_id"],
                     "bbox":row["bbox"],"frozen_probability":row["fdk_probability"],
                     "frozen_call":row["fdk_class"],"candidate_probability":float(p),
                     "candidate_call":"FDK" if called else "healthy"})
    status="training_photo" if sample in bundle["training_samples"] else "unlabeled_photo"
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination / "kernel_predictions.csv",rows)
    write_csv(destination / "image_prediction.csv",[{"sample_id":sample,
        "candidate_name":candidate_name,"status":status,"retained_objects":len(rows),
        "candidate_fdk_calls":int(calls.sum()),
        "candidate_fdk_percent_all_objects":100*float(calls.mean()),
        "frozen_fdk_percent_all_objects":100*sum(r["fdk_class"]=="FDK" for r in predictions)/len(rows),
        "denominator":"All retained detections; unsure and non-wheat not excluded"}])
    Image.fromarray(canvas).save(destination / "kernels_outlined_red_FDK_green_healthy.png")
    (destination / "provenance.json").write_text(json.dumps({
        "source_sha256":provenance["source_sha256"],
        "candidate_sha256":file_sha256(model_path),"candidate_name":candidate_name,
        "training_photo":sample in bundle["training_samples"],
        "scope":"Experimental candidate; all-object rate is not a verified wheat FDK phenotype"
    },indent=2)+"\n")
    print(f"{sample}: candidate {int(calls.sum())}/{len(rows)} = {100*calls.mean():.2f}% "
          f"of all retained objects; frozen {100*sum(r['fdk_class']=='FDK' for r in predictions)/len(rows):.2f}%")
    print(f"{status}; no manual exclusions applied. Saved: {destination}")


def diagnose_candidate_misses(baseline_name, candidate_name, samples):
    """Compare saved image-held-out calls and crop their existing masks."""
    from PIL import ImageDraw
    baseline_path=candidate_directory(baseline_name) / "image_held_out_predictions.csv"
    candidate_path=candidate_directory(candidate_name) / "image_held_out_predictions.csv"
    baseline=read_rows(baseline_path)
    candidate=read_rows(candidate_path)
    old={(r["sample_id"],r["kernel_id"]):r for r in baseline}
    new={(r["sample_id"],r["kernel_id"]):r for r in candidate}
    if (len(old)!=len(baseline) or len(new)!=len(candidate) or
            set(old)!=set(new)):
        raise ValueError("Candidate image-held-out predictions have different IDs or duplicates")
    destination=candidate_directory(candidate_name) / f"diagnostics_vs_{baseline_name}"
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"Diagnostic already exists: {destination}")
    all_rows=[]
    sheets=[]
    for sample in samples:
        source_rows=read_rows(DATA / "output" / sample / "image_prediction.csv")
        if len(source_rows)!=1 or source_rows[0]["sample_id"]!=sample:
            raise ValueError(f"Saved photo summary mismatch: {sample}")
        source=Path(source_rows[0]["image_path"])
        if file_sha256(source)!=json.loads((DATA / "output" / sample / "provenance.json").read_text())["source_sha256"]:
            raise ValueError(f"Original photo changed: {sample}")
        rgb=load_and_crop_image(source)
        detections=read_rows(DATA / "output" / sample / "kernel_predictions.csv")
        detection_by_id={r["kernel_id"]:r for r in detections}
        if len(detection_by_id)!=len(detections):
            raise ValueError(f"Duplicate saved detection ID: {sample}")
        with np.load(DATA / "output" / sample / "kernel_masks.npz",allow_pickle=False) as archive:
            masks={k:archive[k] for k in ("pixels","offsets","bboxes","image_shape")}
        if len(masks["bboxes"])!=len(detections) or tuple(masks["image_shape"])!=rgb.shape[:2]:
            raise ValueError(f"Saved masks mismatch photo: {sample}")
        rows=[]
        for (sid,kid),a in old.items():
            if sid!=sample:
                continue
            b=new[(sid,kid)]
            if a["manual_FDK"]!=b["manual_FDK"] or kid not in detection_by_id:
                raise ValueError(f"Held-out label or detection mismatch at {kid}")
            det=detection_by_id[kid]
            i=int(det["index"])
            bbox=tuple(map(int,det["bbox"].split(",")))
            if i<0 or i>=len(detections) or tuple(map(int,masks["bboxes"][i]))!=bbox:
                raise ValueError(f"Mask alignment mismatch at {kid}")
            truth=int(b["manual_FDK"])
            earlier=float(a["candidate_probability"])
            latest=float(b["candidate_probability"])
            if not np.isfinite([earlier,latest]).all():
                raise ValueError(f"Undefined probability at {kid}")
            if truth:
                status=("shared_FDK_miss" if earlier<.5 and latest<.5 else
                        "recovered_FDK" if earlier<.5 else
                        "new_FDK_miss" if latest<.5 else "both_correct_FDK")
            else:
                status=("shared_healthy_FP" if earlier>=.5 and latest>=.5 else
                        "resolved_healthy_FP" if earlier>=.5 else
                        "new_healthy_FP" if latest>=.5 else "both_correct_healthy")
            x1,y1,x2,y2=bbox
            local_mask=restore_mask(masks,i)
            lab_crop=rgb2lab(rgb[y1:y2,x1:x2])
            base=build_qc_features({"mask":local_mask,"confidence":1.0},lab_crop)
            texture=kernel_texture_features(local_mask,lab_crop)
            rows.append({"sample_id":sample,"kernel_id":kid,"status":status,
                         "manual_label":"FDK" if truth else "healthy",
                         "baseline_probability":earlier,"candidate_probability":latest,
                         "bbox":det["bbox"],"area_px":base["area_px"],
                         "minor_axis_px":base["minor_axis_px"],"solidity":base["solidity"],
                         "L_q25":base["L_q25"],"L_median":base["L_median"],
                         "a_q90":base["a_q90"],"a_q80range":base["a_q80range"],
                         **texture})
        if len(rows)!=sum(key[0]==sample for key in old):
            raise ValueError(f"Missing held-out detections for {sample}")
        for status in ("shared_FDK_miss","recovered_FDK","new_FDK_miss",
                       "shared_healthy_FP","resolved_healthy_FP","new_healthy_FP"):
            selected=[r for r in rows if r["status"]==status]
            print(f"{sample} {status}: {len(selected)}")
            if not selected:
                continue
            selected=sorted(selected,key=lambda r:r["candidate_probability"])
            width,height=4*225,((len(selected)+3)//4)*245
            sheet=Image.new("RGB",(width,height),(245,245,245))
            draw=ImageDraw.Draw(sheet)
            for j,r in enumerate(selected):
                x1,y1,x2,y2=tuple(map(int,r["bbox"].split(",")))
                pad=14
                left,top=max(0,x1-pad),max(0,y1-pad)
                right,bottom=min(rgb.shape[1],x2+pad),min(rgb.shape[0],y2+pad)
                crop_pixels=rgb[top:bottom,left:right].copy()
                index=int(detection_by_id[r["kernel_id"]]["index"])
                edge=boundary(restore_mask(masks,index))
                crop_pixels[y1-top:y2-top,x1-left:x2-left][edge]=(245,0,220)
                crop=Image.fromarray(crop_pixels)
                crop.thumbnail((205,190))
                px=(j%4)*225+(225-crop.width)//2
                py=(j//4)*245+28
                sheet.paste(crop,(px,py))
                draw.text(((j%4)*225+5,(j//4)*245+3),r["kernel_id"].split("_K")[-1],fill=(0,0,0))
                draw.text(((j%4)*225+5,(j//4)*245+221),
                          f"p {r['baseline_probability']:.2f} -> {r['candidate_probability']:.2f}",fill=(0,0,0))
            sheets.append((f"{sample}_{status}.png",sheet))
        all_rows.extend(rows)
    destination.mkdir(parents=True,exist_ok=True)
    write_csv(destination / "heldout_error_features.csv",all_rows)
    for filename,sheet in sheets:
        sheet.save(destination / filename)
    (destination / "README.txt").write_text(
        "These are previously saved whole-image-held-out predictions. Crops show the original "
        "photo with magenta mask outlines; IDs and probabilities identify comparisons. "
        "Manual labels include completed selected reviews where available. "
        "Sheets are diagnostic and do not alter labels, masks, or model bundles.\n")
    print(f"Saved read-only error crops and features: {destination}")


def compare_three_candidates(names, samples):
    """Align saved image-held-out calls from three locked candidate runs."""
    if len(names)!=3 or len(set(names))!=3 or not samples:
        raise ValueError("Supply three distinct candidate names and at least one sample")
    samples=list(dict.fromkeys(samples))
    bundles=[]
    indexed=[]
    for name in names:
        folder=candidate_directory(name)
        bundle=joblib.load(folder / "candidate.joblib")
        if any(sample not in bundle["training_samples"] for sample in samples):
            raise ValueError(f"{name} must contain every requested photo in its image-held-out run")
        rows=read_rows(folder / "image_held_out_predictions.csv")
        by_id={(r["sample_id"],r["kernel_id"]):r for r in rows}
        if len(rows)!=len(by_id):
            raise ValueError(f"Duplicate image-held-out IDs for {name}")
        bundles.append(bundle)
        indexed.append(by_id)
    destination=(DATA / "output" / "model_development" / "candidate_comparisons" /
                 ("_vs_".join(names)) / ("_".join(samples)))
    if destination.exists():
        raise FileExistsError(f"Candidate comparison already exists: {destination}")
    output=[]
    summary=[]
    for sample in samples:
        keys=sorted(k for k in indexed[0] if k[0]==sample)
        if not keys or any({k for k in candidate if k[0]==sample}!=set(keys) for candidate in indexed[1:]):
            raise ValueError(f"Different or missing held-out kernel IDs: {sample}")
        counts={name:{"TP":0,"FN":0,"FP":0,"TN":0} for name in names}
        for key in keys:
            records=[candidate[key] for candidate in indexed]
            truths=[int(record["manual_FDK"]) for record in records]
            if len(set(truths))!=1:
                raise ValueError(f"Labels changed between candidate runs: {key}")
            truth=truths[0]
            calls=[int(record["candidate_FDK"]) for record in records]
            for name,call in zip(names,calls):
                counts[name][("TP" if call else "FN") if truth else ("FP" if call else "TN")]+=1
            output.append({"sample_id":key[0],"kernel_id":key[1],"manual_label":"FDK" if truth else "healthy",
                           **{f"{name}_probability":float(record["candidate_probability"])
                              for name,record in zip(names,records)},
                           **{f"{name}_call":"FDK" if call else "healthy"
                              for name,call in zip(names,calls)},
                           "call_pattern":"".join(map(str,calls))})
        for name in names:
            c=counts[name]
            total=len(keys)
            summary.append({"sample_id":sample,"candidate":name,"wheat_count":total,
                            "manual_FDK":c["TP"]+c["FN"],"predicted_FDK":c["TP"]+c["FP"],
                            "manual_percent":100*(c["TP"]+c["FN"])/total,
                            "model_percent":100*(c["TP"]+c["FP"])/total,**c})
        changed=sum(len(set(int(indexed[i][key]["candidate_FDK"]) for i in range(3)))>1 for key in keys)
        print(f"{sample}: {changed}/{len(keys)} known-wheat calls differ among candidates")
        for row in summary[-3:]:
            print(f"  {row['candidate']}: {row['model_percent']:.2f}% vs manual {row['manual_percent']:.2f}%; "
                  f"TP/FN/FP/TN {row['TP']}/{row['FN']}/{row['FP']}/{row['TN']}")
    destination.mkdir(parents=True)
    write_csv(destination / "kernel_comparison.csv",output)
    write_csv(destination / "image_summary.csv",summary)
    print(f"Saved aligned image-held-out comparison: {destination}")
    print("Development folds only; no model, labels, threshold, or frozen output changed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Check frozen model bundles and feature order")
    sub.add_parser("validation-report", help="Summarize completed manual reviews and model rates with distinct denominators")
    training = sub.add_parser("train-candidate", help="Train a separate classifier from reviewed manual wheat labels")
    training.add_argument("--sample-ids", nargs="+", required=True)
    training.add_argument("--candidate-name", default="manual_sdsu_candidate_v1")
    training.add_argument("--fdk-weight", type=float, default=1.0,
                          help="Experimental FDK label weight from 1.0 to 2.0; healthy stays 1.0")
    training.add_argument("--model-type", choices=("logistic","boosted","relative_logistic","relative_texture_logistic","relative_crease_logistic","relative_texture_review_weighted"), default="logistic",
                          help="Test photo-relative texture or lower training weight for changed human labels")
    cutoff = sub.add_parser("threshold-development", help="Inspect fixed cutoffs on saved image-held-out candidate probabilities")
    cutoff.add_argument("--candidate-name", required=True)
    candidate_eval = sub.add_parser("evaluate-candidate", help="Compare the saved candidate on a new blind-labeled photo")
    candidate_eval.add_argument("--sample-id", required=True)
    candidate_eval.add_argument("--candidate-name", default="manual_sdsu_candidate_v1")
    candidate_score = sub.add_parser("score-candidate", help="Score saved masks with a candidate without changing frozen results")
    candidate_score.add_argument("--sample-id", required=True)
    candidate_score.add_argument("--candidate-name", required=True)
    misses = sub.add_parser("diagnose-candidate-misses", help="Compare two saved image-held-out candidates and export error crops")
    misses.add_argument("--baseline-name", required=True)
    misses.add_argument("--candidate-name", required=True)
    misses.add_argument("--sample-ids", nargs="+", required=True)
    three = sub.add_parser("compare-three-candidates", help="Align three saved image-held-out candidate runs kernel by kernel")
    three.add_argument("--candidate-names", nargs=3, required=True)
    three.add_argument("--sample-ids", nargs="+", required=True)
    candidate_audit = sub.add_parser("audit-candidate", help="Blind second pass through candidate errors and agreement controls")
    candidate_audit.add_argument("--sample-id", required=True)
    candidate_audit.add_argument("--candidate-name", required=True)
    candidate_audit.add_argument("--controls-per-class", type=int, default=6)
    candidate_audit_report = sub.add_parser("audit-candidate-report", help="Summarize the completed candidate second pass")
    candidate_audit_report.add_argument("--sample-id", required=True)
    candidate_audit_report.add_argument("--candidate-name", required=True)
    one = sub.add_parser("score", help="Score one original photo")
    one.add_argument("--image", type=Path, required=True)
    one.add_argument("--sample-id", required=True)
    one.add_argument("--overwrite", action="store_true")
    many = sub.add_parser("score-folder", help="Score every supported photo in a folder")
    many.add_argument("--images-dir", type=Path, required=True)
    many.add_argument("--site", default="", help="Sample ID prefix, e.g. SDSU")
    many.add_argument("--overwrite", action="store_true")
    review = sub.add_parser("review", help="Manually label one scored photo with the kernel GUI")
    review.add_argument("--sample-id", required=True)
    legacy = sub.add_parser("import-legacy-labels", help="Verify and transfer matching old manual labels")
    legacy.add_argument("--sample-id", required=True)
    legacy.add_argument("--prior-project", type=Path, required=True)
    compare = sub.add_parser("compare", help="Compare completed manual labels with saved model predictions")
    compare.add_argument("--sample-id", required=True)
    audit = sub.add_parser("audit", help="Second-pass review of all disagreements and sampled agreement controls")
    audit.add_argument("--sample-id", required=True)
    audit.add_argument("--controls-per-class", type=int, default=8)
    audit_summary = sub.add_parser("audit-report", help="Summarize a completed second-pass review")
    audit_summary.add_argument("--sample-id", required=True)
    reviews = sub.add_parser("compare-reviews", help="Compare current manual labels with a prior label CSV by kernel ID")
    reviews.add_argument("--sample-id", required=True)
    reviews.add_argument("--prior-labels", type=Path, required=True)
    reconcile = sub.add_parser("reconcile", help="Third review of changed human labels and unchanged controls")
    reconcile.add_argument("--sample-id", required=True)
    reconcile.add_argument("--controls-per-label", type=int, default=4)
    reconcile_summary = sub.add_parser("reconcile-report", help="Summarize the completed third review")
    reconcile_summary.add_argument("--sample-id", required=True)
    fp_review = sub.add_parser("review-fp", help="Blind spot check sampled healthy false positives and controls")
    fp_review.add_argument("--sample-id", required=True)
    fp_review.add_argument("--per-group", type=int, default=20)
    fp_summary = sub.add_parser("review-fp-report", help="Summarize the healthy-kernel spot check")
    fp_summary.add_argument("--sample-id", required=True)
    inspect = sub.add_parser("inspect-fp", help="Compare confirmed healthy false positives with healthy controls in a GUI")
    inspect.add_argument("--sample-id", required=True)
    highlights = sub.add_parser("highlight-report", help="Measure glare proxies on the saved blind-confirmed healthy kernels")
    highlights.add_argument("--sample-id", required=True)
    brightness = sub.add_parser("brightness-development", help="Compare model groups on a labeled development photo")
    brightness.add_argument("--sample-id", required=True)
    texture = sub.add_parser("texture-development", help="Within-photo spatial-fold test of five texture features")
    texture.add_argument("--sample-id", required=True)
    args = parser.parse_args()
    if args.command == "check":
        check_models()
    elif args.command == "validation-report":
        validation_report()
    elif args.command == "train-candidate":
        train_manual_candidate(args.sample_ids,args.candidate_name,args.fdk_weight,args.model_type)
    elif args.command == "threshold-development":
        candidate_threshold_development(args.candidate_name)
    elif args.command == "evaluate-candidate":
        evaluate_manual_candidate(args.sample_id,args.candidate_name)
    elif args.command == "score-candidate":
        score_saved_candidate(args.sample_id,args.candidate_name)
    elif args.command == "diagnose-candidate-misses":
        diagnose_candidate_misses(args.baseline_name,args.candidate_name,args.sample_ids)
    elif args.command == "compare-three-candidates":
        compare_three_candidates(args.candidate_names,args.sample_ids)
    elif args.command == "audit-candidate":
        disagreement_audit(args.sample_id,args.controls_per_class,"candidate",args.candidate_name)
    elif args.command == "audit-candidate-report":
        audit_report(args.sample_id,"candidate",args.candidate_name)
    elif args.command == "score":
        score_image(args.image, args.sample_id, args.overwrite)
    elif args.command == "review":
        review_image(args.sample_id)
    elif args.command == "import-legacy-labels":
        import_legacy_labels(args.sample_id,args.prior_project)
    elif args.command == "compare":
        compare_manual(args.sample_id)
    elif args.command == "audit":
        disagreement_audit(args.sample_id, args.controls_per_class)
    elif args.command == "audit-report":
        audit_report(args.sample_id)
    elif args.command == "compare-reviews":
        compare_reviews(args.sample_id, args.prior_labels)
    elif args.command == "reconcile":
        disagreement_audit(args.sample_id, args.controls_per_label, mode="human")
    elif args.command == "reconcile-report":
        audit_report(args.sample_id, mode="human")
    elif args.command == "review-fp":
        disagreement_audit(args.sample_id, args.per_group, mode="fp")
    elif args.command == "review-fp-report":
        audit_report(args.sample_id, mode="fp")
    elif args.command == "inspect-fp":
        inspect_fp_cues(args.sample_id)
    elif args.command == "highlight-report":
        highlight_report(args.sample_id)
    elif args.command == "brightness-development":
        brightness_development_report(args.sample_id)
    elif args.command == "texture-development":
        texture_development_test(args.sample_id)
    else:
        images = sorted(p for p in args.images_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED)
        if not images:
            raise ValueError(f"No supported photos in {args.images_dir}")
        ids = ["_".join(x for x in (args.site, p.stem.replace(" ", "_")) if x) for p in images]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate sample IDs across photos; rename originals or use score individually")
        for image, sample in zip(images, ids):
            score_image(image, sample, args.overwrite)


if __name__ == "__main__":
    main()
