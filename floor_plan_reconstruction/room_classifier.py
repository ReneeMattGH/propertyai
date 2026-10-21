"""Real local room classification with a pretrained vision-language model.

The classifier uses CLIP zero-shot prompts on CPU. It never returns a label
without a score, and it marks low-confidence or narrow-margin results as
ambiguous so the review UI asks the user to choose a room instead of guessing.
If the model cannot be loaded, classification is reported as unavailable rather
than replaced with hardcoded labels.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageOps

from photo_manifest import ROOM_TYPE_LABELS, ROOM_TYPES

DEFAULT_MODEL = os.getenv("ROOM_CLASSIFIER_MODEL", "openai/clip-vit-base-patch32")
MIN_CONFIDENCE = float(os.getenv("ROOM_CLASSIFIER_MIN_CONFIDENCE", "0.55"))
MIN_MARGIN = float(os.getenv("ROOM_CLASSIFIER_MIN_MARGIN", "0.12"))

PROMPTS: Dict[str, List[str]] = {
    "entrance": [
        "an interior photo of an entrance hall or foyer",
        "a front door area inside a house",
    ],
    "living_room": [
        "an interior photo of a living room",
        "a living room with seating and a lounge area",
        "a large empty room with windows and wooden floors",
    ],
    "kitchen": [
        "an interior photo of a kitchen",
        "a kitchen with cabinets, countertops, an oven and a sink",
    ],
    "bedroom": [
        "an interior photo of a bedroom",
        "a bedroom with a bed",
    ],
    "bathroom": [
        "an interior photo of a bathroom",
        "a bathroom with a toilet and sink",
        "a bathroom with a shower or bathtub",
    ],
    "dining_room": [
        "an interior photo of a dining room",
        "a room with a dining table and chairs",
    ],
    "hallway": [
        "an interior photo of a hallway or corridor",
        "a narrow corridor inside a house",
    ],
    "balcony": [
        "a photo of a balcony or terrace",
        "an outdoor seating area attached to a house",
    ],
    "utility": [
        "a laundry room with a washing machine and dryer",
        "a utility room with a water heater or electrical panel",
    ],
    "exterior": [
        "an exterior photo of a building",
        "an outdoor photo of a house, garden or street",
    ],
    "staircase": [
        "an interior photo of a staircase",
        "stairs inside a house",
    ],
    "unknown": [
        "a photo that does not clearly show a room",
        "an unclear or abstract photograph",
    ],
}


class RoomClassifier:
    """CPU zero-shot room-type classifier with honest failure reporting."""

    _lock = threading.Lock()
    _model = None
    _processor = None
    _load_error: Optional[str] = None
    _load_attempted = False

    def __init__(self, model_name: Optional[str] = None):
        self.model_name = model_name or DEFAULT_MODEL

    def _ensure_loaded(self):
        with self._lock:
            if self._model is not None and self._processor is not None:
                return
            if self._load_attempted and self._load_error:
                raise RuntimeError(self._load_error)
            self._load_attempted = True
            try:
                import torch
                from transformers import CLIPModel, CLIPProcessor

                torch.set_num_threads(min(4, os.cpu_count() or 2))
                self._model = CLIPModel.from_pretrained(self.model_name)
                self._processor = CLIPProcessor.from_pretrained(self.model_name)
                self._model.eval()
                self._load_error = None
            except Exception as exc:  # noqa: BLE001 - reported to the job
                self._model = None
                self._processor = None
                self._load_error = (
                    f"Local vision model {self.model_name} could not be loaded: {exc}. "
                    "Room types must be chosen manually."
                )
                raise RuntimeError(self._load_error) from exc

    def _unavailable(self, message: str) -> Dict:
        return {
            "status": "unavailable",
            "model": self.model_name,
            "predicted_room_type": None,
            "predicted_label": None,
            "confidence": None,
            "ambiguous": True,
            "candidates": [],
            "error": message,
        }

    def classify_paths(self, paths: List[str]) -> List[Dict]:
        if not paths:
            return []
        try:
            self._ensure_loaded()
        except RuntimeError as exc:
            return [self._unavailable(str(exc)) for _ in paths]

        import torch

        images = []
        readable_indices = []
        results: List[Optional[Dict]] = [None] * len(paths)
        for index, raw_path in enumerate(paths):
            path = Path(raw_path)
            try:
                with Image.open(path) as source:
                    image = ImageOps.exif_transpose(source).convert("RGB")
                    images.append(image)
                    readable_indices.append(index)
            except Exception as exc:  # noqa: BLE001
                results[index] = self._unavailable(f"Image could not be decoded: {exc}")

        if not images:
            return [item or self._unavailable("No readable images") for item in results]

        prompts: List[str] = []
        prompt_labels: List[str] = []
        for room_type in ROOM_TYPES:
            for prompt in PROMPTS.get(room_type, []):
                prompts.append(prompt)
                prompt_labels.append(room_type)

        try:
            inputs = self._processor(
                text=prompts,
                images=images,
                return_tensors="pt",
                padding=True,
                truncation=True,
            )
            with torch.no_grad():
                output = self._model(**inputs)
            scaled = output.logits_per_image
            per_image = scaled.softmax(dim=1)
            label_scores: List[Dict[str, float]] = []
            for row in per_image:
                scores: Dict[str, float] = {}
                for score, room_type in zip(row.tolist(), prompt_labels):
                    scores[room_type] = max(scores.get(room_type, 0.0), float(score))
                ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
                total = sum(value for _, value in ordered) or 1.0
                label_scores.append({label: value / total for label, value in ordered})
        except Exception as exc:  # noqa: BLE001
            message = f"Classification failed: {exc}"
            return [item or self._unavailable(message) for item in results]

        for position, image_index in enumerate(readable_indices):
            scores = label_scores[position]
            ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            top_label, top_score = ordered[0]
            second_score = ordered[1][1] if len(ordered) > 1 else 0.0
            margin = top_score - second_score
            ambiguous = (
                top_score < MIN_CONFIDENCE
                or margin < MIN_MARGIN
                or top_label == "unknown"
            )
            results[image_index] = {
                "status": "ambiguous" if ambiguous else "classified",
                "model": self.model_name,
                "predicted_room_type": top_label,
                "predicted_label": ROOM_TYPE_LABELS.get(top_label, top_label),
                "confidence": round(top_score, 4),
                "ambiguous": ambiguous,
                "candidates": [
                    {
                        "room_type": label,
                        "label": ROOM_TYPE_LABELS.get(label, label),
                        "confidence": round(value, 4),
                    }
                    for label, value in ordered[:4]
                ],
                "error": None,
                "thresholds": {
                    "min_confidence": MIN_CONFIDENCE,
                    "min_margin": MIN_MARGIN,
                },
            }
        return [item or self._unavailable("Classification did not return a result") for item in results]


def classify_photos(paths: List[str]) -> List[Dict]:
    return RoomClassifier().classify_paths(paths)
