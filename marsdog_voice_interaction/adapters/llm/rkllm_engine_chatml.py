# adapters/llm/rkllm_engine.py
#
# RKLLM Runtime 1.3.0 ctypes wrapper.
#
# 优化点：
# - 显式禁用自动聊天模板，避免扭曲 prompt
# - 收集最后一次推理的性能统计 (last_perf)
# - 保留所有原有功能（LoRA、prompt cache、频率设置等）
#
# 适用平台：RK3588 / RK3576 / RK3562 / RV1126B

from __future__ import annotations

import argparse
import ctypes
import os
import resource
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from marsdog_voice_interaction.messages.intent_protocol import parse_intent_tag


# ============================================================
# 1. 路径解析
# ============================================================

MODULE_DIR = Path(__file__).resolve().parent


def _find_project_root(start: Path) -> Path:
    """向上搜索项目根目录。"""
    current = start.resolve()
    for path in [current, *current.parents]:
        if (path / "pyproject.toml").is_file():
            return path
        if (path / "package.xml").is_file():
            return path
        if (path / "lib" / "librkllmrt.so").is_file():
            return path
    return Path.cwd().resolve()


PROJECT_ROOT = _find_project_root(MODULE_DIR)


def resolve_runtime_library(lib_path: str | None = None) -> Path:
    """解析 librkllmrt.so 路径。"""
    candidates: list[Path] = []
    if lib_path:
        candidates.append(Path(lib_path))
    env_path = os.getenv("RKLLM_LIB_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend([
        PROJECT_ROOT / "lib" / "librkllmrt.so",
        Path.cwd() / "lib" / "librkllmrt.so",
    ])
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate.is_file():
            return candidate
    searched = "\n".join(f"  - {item}" for item in candidates)
    raise FileNotFoundError(
        "Cannot find librkllmrt.so.\nSearched:\n" + searched
    )


# ============================================================
# 2. C 结构体与常量 (Runtime 1.3.0)
# ============================================================

RKLLM_Handle_t = ctypes.c_void_p


class LLMCallState:
    RKLLM_RUN_NORMAL = 0
    RKLLM_RUN_WAITING = 1
    RKLLM_RUN_FINISH = 2
    RKLLM_RUN_ERROR = 3


class RKLLMInputType:
    RKLLM_INPUT_PROMPT = 0
    RKLLM_INPUT_TOKEN = 1
    RKLLM_INPUT_EMBED = 2
    RKLLM_INPUT_MULTIMODAL = 3


class RKLLMInferMode:
    RKLLM_INFER_GENERATE = 0
    RKLLM_INFER_GET_LAST_HIDDEN_LAYER = 1
    RKLLM_INFER_GET_LOGITS = 2


class RKLLMExtendParam(ctypes.Structure):
    _fields_ = [
        ("base_domain_id", ctypes.c_int32),
        ("embed_flash", ctypes.c_int8),
        ("enabled_cpus_num", ctypes.c_int8),
        ("enabled_cpus_mask", ctypes.c_uint32),
        ("n_batch", ctypes.c_uint8),
        ("use_cross_attn", ctypes.c_int8),
        ("reserved", ctypes.c_uint8 * 104),
    ]


class RKLLMParam(ctypes.Structure):
    _fields_ = [
        ("model_path", ctypes.c_char_p),
        ("max_context_len", ctypes.c_int32),
        ("max_new_tokens", ctypes.c_int32),
        ("top_k", ctypes.c_int32),
        ("n_keep", ctypes.c_int32),
        ("top_p", ctypes.c_float),
        ("temperature", ctypes.c_float),
        ("repeat_penalty", ctypes.c_float),
        ("frequency_penalty", ctypes.c_float),
        ("presence_penalty", ctypes.c_float),
        ("mirostat", ctypes.c_int32),
        ("mirostat_tau", ctypes.c_float),
        ("mirostat_eta", ctypes.c_float),
        ("skip_special_token", ctypes.c_bool),
        ("ignore_eos_token", ctypes.c_bool),
        ("is_async", ctypes.c_bool),
        ("extend_param", RKLLMExtendParam),
    ]


class RKLLMLoraAdapter(ctypes.Structure):
    _fields_ = [
        ("lora_adapter_path", ctypes.c_char_p),
        ("lora_adapter_name", ctypes.c_char_p),
        ("scale", ctypes.c_float),
    ]


class RKLLMEmbedInput(ctypes.Structure):
    _fields_ = [
        ("embed", ctypes.POINTER(ctypes.c_float)),
        ("n_tokens", ctypes.c_size_t),
    ]


class RKLLMTokenInput(ctypes.Structure):
    _fields_ = [
        ("input_ids", ctypes.POINTER(ctypes.c_int32)),
        ("n_tokens", ctypes.c_size_t),
    ]


class RKLLMImageInput(ctypes.Structure):
    _fields_ = [
        ("image_embed", ctypes.POINTER(ctypes.c_float)),
        ("n_image_tokens", ctypes.c_size_t),
        ("n_image", ctypes.c_size_t),
        ("image_start", ctypes.c_char_p),
        ("image_end", ctypes.c_char_p),
        ("image_content", ctypes.c_char_p),
        ("image_width", ctypes.c_size_t),
        ("image_height", ctypes.c_size_t),
    ]


class RKLLMVideoInput(ctypes.Structure):
    _fields_ = [
        ("video_embed", ctypes.POINTER(ctypes.c_float)),
        ("n_frame_tokens", ctypes.c_size_t),
        ("n_frame_per_video", ctypes.c_size_t),
        ("n_video", ctypes.c_size_t),
        ("video_start", ctypes.c_char_p),
        ("video_end", ctypes.c_char_p),
        ("video_content", ctypes.c_char_p),
        ("frame_width", ctypes.c_size_t),
        ("frame_height", ctypes.c_size_t),
    ]


class RKLLMMultiModalInput(ctypes.Structure):
    _fields_ = [
        ("prompt", ctypes.c_char_p),
        ("image", RKLLMImageInput),
        ("video", RKLLMVideoInput),
    ]


class RKLLMInputUnion(ctypes.Union):
    _fields_ = [
        ("prompt_input", ctypes.c_char_p),
        ("embed_input", RKLLMEmbedInput),
        ("token_input", RKLLMTokenInput),
        ("multimodal_input", RKLLMMultiModalInput),
    ]


class RKLLMInput(ctypes.Structure):
    _anonymous_ = ("input_data",)
    _fields_ = [
        ("role", ctypes.c_char_p),
        ("enable_thinking", ctypes.c_bool),
        ("input_type", ctypes.c_int),
        ("input_data", RKLLMInputUnion),
    ]


class RKLLMLoraParam(ctypes.Structure):
    _fields_ = [("lora_adapter_name", ctypes.c_char_p)]


class RKLLMPromptCacheParam(ctypes.Structure):
    _fields_ = [
        ("save_prompt_cache", ctypes.c_int),
        ("prompt_cache_path", ctypes.c_char_p),
    ]


class RKLLMSamplingParam(ctypes.Structure):
    _fields_ = [
        ("top_k", ctypes.c_int32),
        ("top_p", ctypes.c_float),
        ("temperature", ctypes.c_float),
        ("repeat_penalty", ctypes.c_float),
        ("frequency_penalty", ctypes.c_float),
        ("presence_penalty", ctypes.c_float),
        ("mirostat", ctypes.c_int32),
        ("mirostat_tau", ctypes.c_float),
        ("mirostat_eta", ctypes.c_float),
    ]


class RKLLMInferParam(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_int),
        ("lora_params", ctypes.POINTER(RKLLMLoraParam)),
        ("prompt_cache_params", ctypes.POINTER(RKLLMPromptCacheParam)),
        ("sampling_params", ctypes.POINTER(RKLLMSamplingParam)),
        ("keep_history", ctypes.c_int),
        ("max_new_tokens", ctypes.c_int32),
    ]


class RKLLMResultLastHiddenLayer(ctypes.Structure):
    _fields_ = [
        ("hidden_states", ctypes.POINTER(ctypes.c_float)),
        ("embd_size", ctypes.c_int),
        ("num_tokens", ctypes.c_int),
    ]


class RKLLMResultLogits(ctypes.Structure):
    _fields_ = [
        ("logits", ctypes.POINTER(ctypes.c_float)),
        ("vocab_size", ctypes.c_int),
        ("num_tokens", ctypes.c_int),
    ]


class RKLLMPerfStat(ctypes.Structure):
    _fields_ = [
        ("prefill_time_ms", ctypes.c_float),
        ("prefill_tokens", ctypes.c_int),
        ("generate_time_ms", ctypes.c_float),
        ("generate_tokens", ctypes.c_int),
        ("memory_usage_mb", ctypes.c_float),
    ]


class RKLLMResult(ctypes.Structure):
    _fields_ = [
        ("text", ctypes.c_char_p),
        ("token_id", ctypes.c_int),
        ("last_hidden_layer", RKLLMResultLastHiddenLayer),
        ("logits", RKLLMResultLogits),
        ("perf", RKLLMPerfStat),
    ]


# ============================================================
# 3. 回调与全局状态
# ============================================================

_runtime_call_lock = threading.RLock()
_output_lock = threading.Lock()

_output_chunks: list[str] = []
_global_state: int = -1
_stream_enabled: bool = False
_callback_error: Optional[str] = None
_last_perf: Optional[RKLLMPerfStat] = None  # 新增：收集性能数据


def _reset_callback_state(stream_print: bool) -> None:
    global _output_chunks, _global_state, _stream_enabled, _callback_error, _last_perf
    with _output_lock:
        _output_chunks = []
    _global_state = -1
    _stream_enabled = bool(stream_print)
    _callback_error = None
    _last_perf = None


def _get_callback_text() -> str:
    with _output_lock:
        return "".join(_output_chunks)


def callback_impl(
    result: ctypes.POINTER(RKLLMResult),
    userdata: ctypes.c_void_p,
    state: int,
) -> int:
    del userdata
    global _global_state, _callback_error, _last_perf

    _global_state = int(state)

    try:
        if state == LLMCallState.RKLLM_RUN_NORMAL:
            if result and result.contents.text:
                text = result.contents.text.decode("utf-8", errors="ignore")
                with _output_lock:
                    _output_chunks.append(text)
                if _stream_enabled:
                    print(text, end="", flush=True)

        elif state == LLMCallState.RKLLM_RUN_FINISH:
            # 保存最后一次的性能统计
            if result:
                _last_perf = result.contents.perf
            if _stream_enabled:
                print(flush=True)

        elif state == LLMCallState.RKLLM_RUN_ERROR:
            _callback_error = "RKLLM callback reported RKLLM_RUN_ERROR"
            if _stream_enabled:
                print("\n[RKLLM ERROR]", flush=True)

    except Exception as exc:
        _callback_error = f"RKLLM callback exception: {exc}"

    return 0


LLMResultCallbackType = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.POINTER(RKLLMResult), ctypes.c_void_p, ctypes.c_int
)
LLMTokenizerCallbackType = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p,
    ctypes.c_int32, ctypes.POINTER(ctypes.c_int32), ctypes.c_int32
)
LLMGetEmbedCallbackType = ctypes.CFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_int32), ctypes.c_uint64,
    ctypes.c_void_p, ctypes.c_uint64
)


class RKLLMCallback(ctypes.Structure):
    _fields_ = [
        ("result_callback", LLMResultCallbackType),
        ("result_userdata", ctypes.c_void_p),
        ("tokenizer_callback", LLMTokenizerCallbackType),
        ("tokenizer_userdata", ctypes.c_void_p),
        ("embed_callback", LLMGetEmbedCallbackType),
        ("embed_userdata", ctypes.c_void_p),
    ]


# 必须保留全局引用防止 GC
_CALLBACK = LLMResultCallbackType(callback_impl)


# ============================================================
# 4. 分类任务 prompt 与输出解析
# ============================================================

DEFAULT_SYSTEM_PROMPT = (
    "Classify the owner's MasDog utterance. "
    "Return exactly one label in SOCIAL|INTENT|CONTROL format and nothing else."
)


def build_classification_prompt(
    utterance: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> str:
    """按微调数据中的 Qwen ChatML 格式构造分类 prompt。"""
    utterance = utterance.strip()
    system_prompt = system_prompt.strip()
    if not utterance:
        raise ValueError("utterance must be non-empty")
    if not system_prompt:
        raise ValueError("system_prompt must be non-empty")

    return (
        "<|im_start|>system\n"
        f"{system_prompt}<|im_end|>\n"
        "<|im_start|>user\n"
        f"{utterance}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def parse_classification_output(text: str) -> str:
    """校验完整三轴输出；不从解释性文本中截取标签。"""
    parsed = parse_intent_tag(text)
    if parsed is None:
        raise ValueError(f"invalid Model Intent classification: {text!r}")
    social, intent, control = parsed
    return f"{social}|{intent}|{control}"


# ============================================================
# 5. RKLLMEngine 主类
# ============================================================

class RKLLMEngine:
    """RKLLM 同步推理封装（优化版）。"""

    def __init__(
        self,
        model_path: str,
        platform: str = "rk3588",
        max_context_len: int = 2048,
        max_new_tokens: int = 256,
        top_k: int = 1,
        top_p: float = 1.0,
        temperature: float = 0.0,
        repeat_penalty: float = 1.05,
        lora_model_path: Optional[str] = None,
        prompt_cache_path: Optional[str] = None,
        lib_path: Optional[str] = None,
        verbose: bool = False,
    ) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.platform = platform.lower()
        self.verbose = verbose

        if not self.model_path.is_file():
            raise FileNotFoundError(f"Model not found: {self.model_path}")

        self.lib_path = resolve_runtime_library(lib_path)
        self._lib = ctypes.CDLL(str(self.lib_path))

        self.handle = RKLLM_Handle_t()
        self._released = False

        # 保存字符串引用
        self._model_path_bytes = str(self.model_path).encode("utf-8")
        self._lora_path_bytes: Optional[bytes] = None
        self._lora_name_bytes: Optional[bytes] = None

        self._bind_runtime_functions()

        # 构建初始化参数
        rkllm_param = self._build_model_param(
            max_context_len=max_context_len,
            max_new_tokens=max_new_tokens,
            top_k=top_k,
            top_p=top_p,
            temperature=temperature,
            repeat_penalty=repeat_penalty,
        )

        # 回调结构
        self._callback_bundle = RKLLMCallback()
        self._callback_bundle.result_callback = _CALLBACK
        self._callback_bundle.result_userdata = None
        self._callback_bundle.tokenizer_callback = LLMTokenizerCallbackType()
        self._callback_bundle.tokenizer_userdata = None
        self._callback_bundle.embed_callback = LLMGetEmbedCallbackType()
        self._callback_bundle.embed_userdata = None

        ret = self._rkllm_init(
            ctypes.byref(self.handle),
            ctypes.byref(rkllm_param),
            ctypes.byref(self._callback_bundle),
        )
        if ret != 0:
            raise RuntimeError(f"rkllm_init failed, ret={ret}")
        if not self.handle.value:
            raise RuntimeError("rkllm_init returned empty handle")

        # ========== 关键修改：禁用自动聊天模板 ==========
        self._disable_chat_template()

        if self.verbose:
            print(f"[RKLLM] init success: model={self.model_path}, lib={self.lib_path}")

        # LoRA 加载
        self._lora_param: Optional[RKLLMLoraParam] = None
        self._lora_param_pointer = None
        if lora_model_path:
            self._load_lora(lora_model_path)

        # 推理参数对象
        self.infer_param = self._build_infer_param()

        # prompt cache
        if prompt_cache_path:
            self._load_prompt_cache(prompt_cache_path)

    def _bind_runtime_functions(self) -> None:
        """绑定所有需要的 C 函数。"""
        self._rkllm_init = self._lib.rkllm_init
        self._rkllm_init.argtypes = [
            ctypes.POINTER(RKLLM_Handle_t),
            ctypes.POINTER(RKLLMParam),
            ctypes.POINTER(RKLLMCallback),
        ]
        self._rkllm_init.restype = ctypes.c_int

        self._rkllm_run = self._lib.rkllm_run
        self._rkllm_run.argtypes = [
            RKLLM_Handle_t,
            ctypes.POINTER(RKLLMInput),
            ctypes.POINTER(RKLLMInferParam),
            ctypes.c_void_p,
        ]
        self._rkllm_run.restype = ctypes.c_int

        self._rkllm_destroy = self._lib.rkllm_destroy
        self._rkllm_destroy.argtypes = [RKLLM_Handle_t]
        self._rkllm_destroy.restype = ctypes.c_int

        self._rkllm_abort = getattr(self._lib, "rkllm_abort", None)
        if self._rkllm_abort:
            self._rkllm_abort.argtypes = [RKLLM_Handle_t]
            self._rkllm_abort.restype = ctypes.c_int

        self._rkllm_load_lora = getattr(self._lib, "rkllm_load_lora", None)
        if self._rkllm_load_lora:
            self._rkllm_load_lora.argtypes = [
                RKLLM_Handle_t,
                ctypes.POINTER(RKLLMLoraAdapter),
            ]
            self._rkllm_load_lora.restype = ctypes.c_int

        self._rkllm_load_prompt_cache = getattr(
            self._lib, "rkllm_load_prompt_cache", None
        )
        if self._rkllm_load_prompt_cache:
            self._rkllm_load_prompt_cache.argtypes = [
                RKLLM_Handle_t,
                ctypes.c_char_p,
            ]
            self._rkllm_load_prompt_cache.restype = ctypes.c_int

        # 聊天模板接口（用于禁用）
        self._rkllm_set_chat_template = getattr(
            self._lib, "rkllm_set_chat_template", None
        )
        if self._rkllm_set_chat_template:
            self._rkllm_set_chat_template.argtypes = [
                RKLLM_Handle_t, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p
            ]
            self._rkllm_set_chat_template.restype = ctypes.c_int

    def _disable_chat_template(self) -> None:
        """禁用 Runtime 自动聊天模板，保证完整 ChatML prompt 原样输入。"""
        if self._rkllm_set_chat_template is None:
            raise RuntimeError(
                "rkllm_set_chat_template is unavailable; "
                "cannot guarantee raw ChatML prompt input"
            )
        ret = self._rkllm_set_chat_template(
            self.handle,
            b"",  # system
            b"",  # prefix
            b"",  # postfix
        )
        if ret != 0:
            raise RuntimeError(
                f"Failed to disable RKLLM chat template, ret={ret}"
            )
        if self.verbose:
            print("[RKLLM] automatic chat template disabled")

    def _build_model_param(
        self,
        *,
        max_context_len: int,
        max_new_tokens: int,
        top_k: int,
        top_p: float,
        temperature: float,
        repeat_penalty: float,
    ) -> RKLLMParam:
        param = RKLLMParam()
        ctypes.memset(ctypes.byref(param), 0, ctypes.sizeof(RKLLMParam))

        param.model_path = self._model_path_bytes
        param.max_context_len = max_context_len
        param.max_new_tokens = max_new_tokens
        param.top_k = top_k
        param.n_keep = -1
        param.top_p = top_p
        param.temperature = temperature
        param.repeat_penalty = repeat_penalty
        param.frequency_penalty = 0.0
        param.presence_penalty = 0.0
        param.mirostat = 0
        param.mirostat_tau = 5.0
        param.mirostat_eta = 0.1
        param.skip_special_token = True
        param.ignore_eos_token = False  # 保持模型在生成完协议后停止
        param.is_async = False

        # 扩展参数
        param.extend_param.base_domain_id = 0
        param.extend_param.embed_flash = 1
        param.extend_param.n_batch = 1
        param.extend_param.use_cross_attn = 0
        if self.platform in ("rk3576", "rk3588"):
            param.extend_param.enabled_cpus_num = 4
            param.extend_param.enabled_cpus_mask = (
                (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7)
            )
        else:
            param.extend_param.enabled_cpus_num = 4
            param.extend_param.enabled_cpus_mask = (
                (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
            )
        return param

    def _build_infer_param(self) -> RKLLMInferParam:
        infer_param = RKLLMInferParam()
        ctypes.memset(ctypes.byref(infer_param), 0, ctypes.sizeof(RKLLMInferParam))
        infer_param.mode = RKLLMInferMode.RKLLM_INFER_GENERATE
        if self._lora_param is not None:
            self._lora_param_pointer = ctypes.pointer(self._lora_param)
            infer_param.lora_params = self._lora_param_pointer
        else:
            infer_param.lora_params = None
        infer_param.prompt_cache_params = None
        infer_param.sampling_params = None
        infer_param.keep_history = 0
        infer_param.max_new_tokens = 0  # 使用初始化值
        return infer_param

    def _load_lora(self, lora_path: str) -> None:
        if self._rkllm_load_lora is None:
            raise RuntimeError("rkllm_load_lora not available")
        path = Path(lora_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"LoRA not found: {path}")
        self._lora_path_bytes = str(path).encode("utf-8")
        self._lora_name_bytes = b"default_lora"
        adapter = RKLLMLoraAdapter()
        ctypes.memset(ctypes.byref(adapter), 0, ctypes.sizeof(RKLLMLoraAdapter))
        adapter.lora_adapter_path = self._lora_path_bytes
        adapter.lora_adapter_name = self._lora_name_bytes
        adapter.scale = 1.0
        ret = self._rkllm_load_lora(self.handle, ctypes.byref(adapter))
        if ret != 0:
            raise RuntimeError(f"rkllm_load_lora failed, ret={ret}")
        self._lora_param = RKLLMLoraParam()
        self._lora_param.lora_adapter_name = self._lora_name_bytes
        if self.verbose:
            print(f"[RKLLM] LoRA loaded: {path}")

    def _load_prompt_cache(self, cache_path: str) -> None:
        if self._rkllm_load_prompt_cache is None:
            raise RuntimeError("rkllm_load_prompt_cache not available")
        path = Path(cache_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Prompt cache not found: {path}")
        cache_bytes = str(path).encode("utf-8")
        ret = self._rkllm_load_prompt_cache(self.handle, cache_bytes)
        if ret != 0:
            raise RuntimeError(f"rkllm_load_prompt_cache failed, ret={ret}")
        if self.verbose:
            print(f"[RKLLM] prompt cache loaded: {path}")

    # --------------------------------------------------------
    # 推理接口
    # --------------------------------------------------------

    def chat(
        self,
        prompt: str,
        role: str = "user",
        enable_thinking: bool = False,
        stream_print: bool = False,
        max_new_tokens: Optional[int] = None,
        sampling_params: Optional[RKLLMSamplingParam] = None,
    ) -> str:
        """
        发送单轮文本 prompt，返回模型输出。

        返回仅包含文本。如需性能数据，在调用后访问 last_perf 属性。
        """
        if self._released or not self.handle.value:
            raise RuntimeError("RKLLMEngine already released")
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")

        prompt_bytes = prompt.encode("utf-8")
        role_bytes = role.encode("utf-8")

        rkllm_input = RKLLMInput()
        ctypes.memset(ctypes.byref(rkllm_input), 0, ctypes.sizeof(RKLLMInput))
        rkllm_input.role = role_bytes
        rkllm_input.enable_thinking = enable_thinking
        rkllm_input.input_type = RKLLMInputType.RKLLM_INPUT_PROMPT
        rkllm_input.prompt_input = prompt_bytes

        with _runtime_call_lock:
            _reset_callback_state(stream_print=stream_print)

            # 备份参数
            old_sampling = self.infer_param.sampling_params
            old_max_tokens = self.infer_param.max_new_tokens

            if sampling_params is not None:
                self.infer_param.sampling_params = ctypes.pointer(sampling_params)
            if max_new_tokens is not None:
                self.infer_param.max_new_tokens = int(max_new_tokens)

            try:
                ret = self._rkllm_run(
                    self.handle,
                    ctypes.byref(rkllm_input),
                    ctypes.byref(self.infer_param),
                    None,
                )
                if ret != 0:
                    raise RuntimeError(f"rkllm_run failed, ret={ret}")
                if _global_state == LLMCallState.RKLLM_RUN_ERROR:
                    raise RuntimeError(_callback_error or "Inference error")
                answer = _get_callback_text()
                if not answer and _callback_error:
                    raise RuntimeError(_callback_error)
                return answer
            finally:
                self.infer_param.sampling_params = old_sampling
                self.infer_param.max_new_tokens = old_max_tokens
                global _stream_enabled
                _stream_enabled = False

    def classify(
        self,
        utterance: str,
        *,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        stream_print: bool = False,
        max_new_tokens: int = 16,
    ) -> str:
        """使用微调时的 ChatML 格式执行单句分类。"""
        prompt = build_classification_prompt(utterance, system_prompt)
        sampling = make_sampling_params(
            top_k=1,
            top_p=1.0,
            temperature=0.0,
            repeat_penalty=1.0,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )
        raw_output = self.chat(
            prompt=prompt,
            role="user",
            enable_thinking=False,
            stream_print=stream_print,
            max_new_tokens=max_new_tokens,
            sampling_params=sampling,
        )
        return parse_classification_output(raw_output)

    @property
    def last_perf(self) -> Optional[Dict[str, Any]]:
        """返回最后一次推理的性能数据（字典），无数据时返回 None。"""
        global _last_perf
        if _last_perf is None:
            return None
        return {
            "prefill_time_ms": _last_perf.prefill_time_ms,
            "prefill_tokens": _last_perf.prefill_tokens,
            "generate_time_ms": _last_perf.generate_time_ms,
            "generate_tokens": _last_perf.generate_tokens,
            "memory_usage_mb": _last_perf.memory_usage_mb,
        }

    def abort(self) -> int:
        if self._released or not self.handle.value:
            return 0
        if self._rkllm_abort is None:
            raise RuntimeError("rkllm_abort not available")
        return int(self._rkllm_abort(self.handle))

    def release(self) -> None:
        if self._released:
            return
        if self.handle.value:
            ret = self._rkllm_destroy(self.handle)
            if ret != 0 and self.verbose:
                print(f"[RKLLM] warning: rkllm_destroy returned {ret}")
        self.handle = RKLLM_Handle_t()
        self._released = True
        if self.verbose:
            print("[RKLLM] released")

    def __enter__(self) -> "RKLLMEngine":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass


# ============================================================
# 6. 板端辅助
# ============================================================

def try_fix_frequency(platform: str, script_dir: Optional[str] = None, verbose: bool = True) -> None:
    script_name = f"fix_freq_{platform.lower()}.sh"
    candidates = []
    if script_dir:
        candidates.append(Path(script_dir))
    candidates.extend([MODULE_DIR, PROJECT_ROOT, Path.cwd()])
    for directory in candidates:
        script_path = directory / script_name
        if script_path.is_file():
            if verbose:
                print(f"[RKLLM] running: {script_path}")
            subprocess.run(["sudo", "bash", str(script_path)], check=False)
            return
    if verbose:
        print(f"[RKLLM] freq script not found: {script_name}")


def try_set_resource_limit(verbose: bool = True) -> None:
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (102400, 102400))
    except Exception as exc:
        if verbose:
            print(f"[RKLLM] setrlimit failed: {exc}")


def make_sampling_params(
    *,
    top_k: int = 1,
    top_p: float = 1.0,
    temperature: float = 0.0,
    repeat_penalty: float = 1.0,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
) -> RKLLMSamplingParam:
    sampling = RKLLMSamplingParam()
    ctypes.memset(ctypes.byref(sampling), 0, ctypes.sizeof(RKLLMSamplingParam))
    sampling.top_k = top_k
    sampling.top_p = top_p
    sampling.temperature = temperature
    sampling.repeat_penalty = repeat_penalty
    sampling.frequency_penalty = frequency_penalty
    sampling.presence_penalty = presence_penalty
    sampling.mirostat = 0
    sampling.mirostat_tau = 5.0
    sampling.mirostat_eta = 0.1
    return sampling


# ============================================================
# 7. 命令行入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RKLLM ChatML intent classification test"
    )
    parser.add_argument(
        "--model", "--rkllm_model_path", dest="model_path", required=True
    )
    parser.add_argument("--lib_path", default=None)
    parser.add_argument(
        "--platform",
        default="rk3588",
        choices=["rk3588", "rk3576", "rv1126b", "rk3562"],
    )
    parser.add_argument(
        "--utterance",
        "--prompt",
        dest="utterance",
        default="赶紧去解决一下那边那个",
        help="待分类的原始用户语句，不需要添加 ChatML 模板",
    )
    parser.add_argument("--system_prompt", default=DEFAULT_SYSTEM_PROMPT)
    parser.add_argument("--max_context_len", type=int, default=512)
    parser.add_argument("--max_new_tokens", type=int, default=16)
    parser.add_argument("--stream_print", action="store_true")
    parser.add_argument("--show_raw_prompt", action="store_true")
    parser.add_argument("--no_fix_freq", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.show_raw_prompt:
        print(build_classification_prompt(args.utterance, args.system_prompt))

    if not args.no_fix_freq:
        try_fix_frequency(args.platform, verbose=args.verbose)
    try_set_resource_limit(verbose=args.verbose)

    with RKLLMEngine(
        model_path=args.model_path,
        platform=args.platform,
        max_context_len=args.max_context_len,
        max_new_tokens=args.max_new_tokens,
        top_k=1,
        top_p=1.0,
        temperature=0.0,
        repeat_penalty=1.0,
        lib_path=args.lib_path,
        verbose=args.verbose,
    ) as engine:
        answer = engine.classify(
            utterance=args.utterance,
            system_prompt=args.system_prompt,
            stream_print=args.stream_print,
            max_new_tokens=args.max_new_tokens,
        )
        if not args.stream_print:
            print(answer)
        perf = engine.last_perf
        if perf and args.verbose:
            print(f"\n[RKLLM] Perf: {perf}")


if __name__ == "__main__":
    main()
"""
uv run python -m marsdog_voice_interaction.adapters.llm.rkllm_engine_chatml \
  --model /home/cat/xbb/models/llm/qwen2_5_5b_rk3588_260829_w8a8.rkllm \
  --lib_path /home/cat/xbb/project/20260622_MarsDogPro/lib/librkllmrt.so \
  --platform rk3588 \
  --utterance "我去出差几天。" \
  --max_context_len 512 \
  --max_new_tokens 16 \
  --no_fix_freq \
  --verbose


"""
