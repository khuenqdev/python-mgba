from .ai.engines import *
from .ai.vision import *
from .ai.ocr import *
from .ai.local import *
from .ai.glossary import GlossaryAndCacheStore, resolve_game_id
from .ai.glossary_dialog import TranslationGlossaryDialog, AITranslationConfigDialog
from .ai.auto_trigger import AutoTranslateDetector
from .ai.neural_inpaint import (
    is_migan_available,
    download_migan_model,
    get_migan_model_path,
    run_migan_inpaint,
)
from .ai.inpaint import InpaintPatch, build_inpaint_patches, check_textbox_still_present
from .ai.worker import TranslationWorker