"""Load and validate config.yaml into typed pydantic models.

Location: $GAZE_APP_CONFIG if set, else <app root>/config.yaml. When the app runs as a packaged
executable (PyInstaller), <app root> is the folder of the executable, so data and sessions stay
next to it and are writable.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import ValidationError, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.files import read_text
from app.models.aoi import ELEMENT_AOIS


def _app_root() -> Path:
    if getattr(sys, "frozen", False):          # packaged build
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _app_root()
DEFAULT_CONFIG_PATH = Path(os.environ.get("GAZE_APP_CONFIG", PROJECT_ROOT / "config.yaml"))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rect(_Strict):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class AppInfo(_Strict):
    name: str = "Gaze-Adaptive Assembly Study"
    version: str = "2.0.0"
    locale: str = Field(default="en", pattern=r"^[A-Za-z0-9]{1,10}$")


class ScreenConfig(_Strict):
    width: int = Field(default=1920, gt=0)
    height: int = Field(default=1080, gt=0)
    fullscreen: bool = True
    monitor_index: int = Field(default=0, ge=0)


class InputConfig(_Strict):
    next_key: str = "Space"
    back_key: Optional[str] = None
    debounce_ms: int = Field(default=300, ge=0, le=5000)


class ParticipantIdConfig(_Strict):
    pattern: str = r"^[A-Z0-9]{7}$"
    length: int = Field(default=7, ge=1, le=32)
    example: str = "P000042"
    allow_duplicates: bool = False

    @model_validator(mode="after")
    def _consistent(self) -> "ParticipantIdConfig":
        try:
            rx = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError(f"participant_id.pattern is not a valid regex: {exc}") from exc
        if not rx.fullmatch(self.example) or len(self.example) != self.length:
            raise ValueError("participant_id.example must match the pattern and the length")
        # IDs become folder names: only allow safe characters regardless of the pattern
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.example):
            raise ValueError("participant IDs may only use letters, digits, '_' and '-'")
        return self

    def is_valid(self, pid: str) -> bool:
        return (len(pid) == self.length and re.fullmatch(self.pattern, pid) is not None
                and re.fullmatch(r"[A-Za-z0-9_-]+", pid) is not None)


class FormQuestion(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    label: str
    type: Literal["text", "number", "choice"]
    required: bool = True
    options: list[str] = Field(default_factory=list)
    min: Optional[int] = None
    max: Optional[int] = None
    max_length: int = Field(default=120, ge=1, le=2000)

    @model_validator(mode="after")
    def _choice_has_options(self) -> "FormQuestion":
        if self.type == "choice" and not self.options:
            raise ValueError(f"question '{self.id}': choice needs options")
        return self


class IntakeConfig(_Strict):
    questions: list[FormQuestion] = Field(default_factory=list)


class CalibrationConfig(_Strict):
    enabled: bool = True
    points: list[tuple[float, float]] = Field(default_factory=lambda: [(0.5, 0.5), (0.1, 0.1), (0.9, 0.1),
                                                                        (0.1, 0.9), (0.9, 0.9)])
    settle_s: float = Field(default=0.8, ge=0)
    sample_s: float = Field(default=1.2, gt=0)
    min_samples: int = Field(default=10, ge=1)
    max_error_deg: float = Field(default=1.0, gt=0)
    screen_width_cm: float = Field(default=53.1, gt=0)
    viewing_distance_cm: float = Field(default=65.0, gt=0)

    @field_validator("points")
    @classmethod
    def _in_range(cls, v: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if not v:
            raise ValueError("calibration.points must not be empty")
        for x, y in v:
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError("calibration points are normalised screen coordinates (0..1)")
        return v


class LoopConfig(_Strict):
    feedback_every_n_repetitions: int = Field(default=1, ge=1)
    history_k: int = Field(default=3, ge=1, le=50)
    max_repetitions: Optional[int] = Field(default=None, ge=1)
    analysing_min_display_s: float = Field(default=1.5, ge=0, le=30)
    notice_display_s: float = Field(default=3.0, ge=0, le=60)


class FeedbackConfig(_Strict):
    difficulty_options: list[str] = Field(default_factory=lambda: ["easy", "medium", "hard"], min_length=2)


class GamificationConfig(_Strict):
    # observer: streak = consecutive steps the observer marked "correct" (reset on "error")
    # repetitions: streak = repetitions in a row without pressing "Back"
    streak_mode: Literal["observer", "repetitions"] = "observer"
    show_completion_time: bool = True
    show_points: bool = True
    points_per_step: int = Field(default=10, ge=0, le=1000)
    points_correct_bonus: int = Field(default=5, ge=0, le=1000)    # step verified correct
    cheer_streaks: list[int] = Field(default_factory=lambda: [3, 5, 10])   # streak milestones praised
    speak_s: float = Field(default=1.6, ge=0, le=10)       # the guide "talks" this long after a new step
    cheer_s: float = Field(default=2.5, ge=0.5, le=10)
    flash_ms: int = Field(default=900, ge=100, le=5000)
    show_personal_best: bool = True


class PostSurveyConfig(_Strict):
    enabled: bool = True
    survey: str = Field(default="sus", pattern=r"^[A-Za-z0-9_-]+$")


class UIConfig(_Strict):
    show_hidden_placeholder: bool = True
    pixmap_cache_size: int = Field(default=64, ge=4, le=1024)


class MouseConfig(_Strict):
    rate_hz: int = Field(default=60, gt=0, le=1000)


class GazepointFields(_Strict):
    time: str
    x: str
    y: str
    valid: str
    fixation_id: Optional[str] = None
    counter: Optional[str] = None


class GazepointConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=4242, ge=1, le=65535)
    reconnect_s: float = Field(default=2.0, gt=0)
    enable_commands: list[str]
    fields: GazepointFields


class MockServerConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=4343, ge=1, le=65535)
    rate_hz: int = Field(default=60, gt=0, le=1000)

    @field_validator("host")
    @classmethod
    def _local_only(cls, v: str) -> str:
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("the mock server only binds to the local machine")
        return v


class GazeConfig(_Strict):
    mouse: MouseConfig
    gazepoint: GazepointConfig
    mock_server: MockServerConfig


class GeminiConfig(_Strict):
    model: str
    api_key_env: str = "GEMINI_API_KEY"
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_s: int = Field(default=60, gt=0, le=600)


class OllamaConfig(_Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=11434, ge=1, le=65535)
    model: str = "qwen2.5:7b"
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_s: int = Field(default=120, gt=0, le=1200)


class LLMConfig(_Strict):
    gemini: GeminiConfig
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    generation_max_retries: int = Field(default=1, ge=0, le=5)
    max_text_chars: int = Field(default=220, ge=40, le=2000)


class CameraConfig(_Strict):
    """Board camera (USB webcam): a photo after every step, checked against the step's reference image."""
    device_index: int = Field(default=0, ge=0, le=32)       # index in the system's camera list
    resolution: tuple[int, int] = (1920, 1080)              # requested; the camera may pick the closest
    jpeg_quality: int = Field(default=90, ge=10, le=100)
    settle_ms: int = Field(default=150, ge=0, le=5000)      # wait after "Next" before the photo (hand leaves)
    mock_error_every_n: int = Field(default=0, ge=0)        # mock camera: every n-th photo shows an error


class VerificationConfig(_Strict):
    """Automatic step check: photo vs. reference image by a vision model (or pixel similarity: mock)."""
    threshold: float = Field(default=0.8, ge=0, le=1)       # below this confidence -> observer review
    gemini_model: str = "gemini-2.5-flash"
    ollama_model: str = "qwen2.5vl:7b"
    max_image_px: int = Field(default=1024, ge=256, le=4096)  # longest side sent to the model
    timeout_s: int = Field(default=60, gt=0, le=600)
    drain_timeout_s: int = Field(default=30, ge=0, le=600)  # at session end: wait for open checks
    mock_similarity: float = Field(default=0.92, ge=0, le=1)  # mock: similarity needed for "correct"


class LimitsConfig(_Strict):
    max_media_mb: int = Field(default=200, ge=1, le=4096)
    image_extensions: list[str] = Field(default_factory=lambda: ["png", "jpg", "jpeg", "webp", "bmp"])
    video_extensions: list[str] = Field(default_factory=lambda: ["mp4", "mov", "webm", "avi", "mkv"])


class LoggingConfig(_Strict):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_kb: int = Field(default=2048, ge=16)
    backups: int = Field(default=5, ge=0, le=50)


class PathsConfig(_Strict):
    instruction_sets: Path
    media: Path
    media_catalog: Path
    rules: Path
    locale: Path
    surveys: Path
    prompts: Path = Path("data/prompts")
    theme: Path
    sessions: Path
    logs: Path
    exports: Path = Path("exports")      # app-level downloads (AOI layout, ...)


class PreprocessConfig(_Strict):
    # Gaze runs on one AOI shorter than this are ignored in the transition sequence
    # (filters border flicker). 0 = every run counts.
    min_visit_ms: int = Field(default=0, ge=0, le=5000)


class AppConfig(_Strict):
    app: AppInfo = Field(default_factory=AppInfo)
    screen: ScreenConfig
    aoi: dict[str, Rect]
    input: InputConfig
    participant_id: ParticipantIdConfig = Field(default_factory=ParticipantIdConfig)
    intake: IntakeConfig = Field(default_factory=IntakeConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    loop: LoopConfig
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    gamification: GamificationConfig = Field(default_factory=GamificationConfig)
    post_survey: PostSurveyConfig = Field(default_factory=PostSurveyConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    gaze: GazeConfig
    llm: LLMConfig
    camera: CameraConfig = Field(default_factory=CameraConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    paths: PathsConfig

    @model_validator(mode="after")
    def _check_aois(self) -> "AppConfig":
        missing = set(ELEMENT_AOIS) - set(self.aoi)
        unknown = set(self.aoi) - set(ELEMENT_AOIS)
        if missing or unknown:
            raise ValueError(f"AOI config mismatch: missing={sorted(missing)} unknown={sorted(unknown)}")
        for name, r in self.aoi.items():
            if r.x + r.w > self.screen.width or r.y + r.h > self.screen.height:
                raise ValueError(f"AOI '{name}' exceeds screen bounds")
        names = list(self.aoi)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                ra, rb = self.aoi[a], self.aoi[b]
                if ra.x < rb.x + rb.w and rb.x < ra.x + ra.w and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h:
                    raise ValueError(f"AOIs '{a}' and '{b}' overlap")
        return self

    def resolve(self, p: Path) -> Path:
        """Resolve a configured path relative to the app root."""
        return p if p.is_absolute() else PROJECT_ROOT / p


class ConfigError(ValueError):
    """config.yaml missing, unreadable or invalid (message lists every problem)."""


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    path = Path(path)
    try:
        raw = yaml.safe_load(read_text(path, max_bytes=2 * 1024 * 1024))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: must contain a mapping at the top level")
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ConfigError(f"{path}: {problems}") from exc
