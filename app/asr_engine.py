"""ASR 引擎: sherpa-onnx 本地流式识别。
模型: sherpa-onnx-streaming-zipformer-bilingual-zh-en (中英双语流式, int8)
模型文件 (放在 models/ 下):
  encoder-epoch-99-avg-1.int8.onnx
  decoder-epoch-99-avg-1.onnx
  joiner-epoch-99-avg-1.int8.onnx
  tokens.txt
"""
import os
import numpy as np
import sherpa_onnx

# 热词文件模板(%APPDATA%\VRCVoice\hotwords.txt 首次启动时创建)
HOTWORDS_TEMPLATE = """# VRCVoice 热词表: 每行一个词, 可带权重(建议 1.0~5.0), 权重越大识别越偏向该词。
# 带 # 的行会被忽略。用「设置-识别-识别热词」编辑, 或直接改本文件后重启生效。
"""


class ASREngine:
    def __init__(self, model_dir: str, language: str = "zh-en"):
        self.model_dir = model_dir
        self.language = language
        self.recognizer = None
        self._stream = None
        self._init_with_retry()

    def _init_with_retry(self, attempts: int = 2, delay: float = 1.0):
        """模型文件可能被杀软临时锁定(error 13), 重试一次; 预加载机制下触发时快速失败。"""
        last = None
        for i in range(attempts):
            try:
                self._init()
                return
            except Exception as e:
                last = e
                if "系统找不到" in str(e) or "FileNotFound" in str(e):
                    raise  # 文件真不存在, 重试无用
                import time
                time.sleep(delay)
        raise last

    def _init(self):
        enc = os.path.join(self.model_dir, "encoder-epoch-99-avg-1.int8.onnx")
        dec = os.path.join(self.model_dir, "decoder-epoch-99-avg-1.onnx")
        joiner = os.path.join(self.model_dir, "joiner-epoch-99-avg-1.int8.onnx")
        tokens = os.path.join(self.model_dir, "tokens.txt")
        for p in (enc, dec, joiner, tokens):
            if not os.path.exists(p):
                raise FileNotFoundError(f"模型文件缺失: {p}\n请把 zipformer 双语模型解压到 {self.model_dir}")

        # 热词支持: %APPDATA%\VRCVoice\hotwords.txt 存在且有内容时, 用 modified_beam_search + 热词偏置,
        # 专有名词/人名/游戏名等识别更准。文件每行一个词(可带权重, 如: VRChat 3.0)。
        # 放 APPDATA(而非 models/) 避免升级覆盖用户自建词表。
        from .log import data_dir
        hotwords_file = os.path.join(data_dir(), "hotwords.txt")
        if not os.path.exists(hotwords_file):
            try:
                with open(hotwords_file, "w", encoding="utf-8") as f:
                    f.write(HOTWORDS_TEMPLATE)
            except OSError:
                hotwords_file = ""
        use_hotwords = False
        if hotwords_file and os.path.exists(hotwords_file):
            with open(hotwords_file, "r", encoding="utf-8") as f:
                use_hotwords = any(line.strip() for line in f)
        kwargs = dict(
            tokens=tokens,
            encoder=enc,
            decoder=dec,
            joiner=joiner,
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
            enable_endpoint_detection=False,
            rule_fsts="",
            modeling_unit="bpe" if os.path.exists(os.path.join(self.model_dir, "bpe.vocab")) else "cjkchar",
            bpe_vocab=os.path.join(self.model_dir, "bpe.vocab") if os.path.exists(os.path.join(self.model_dir, "bpe.vocab")) else "",
        )
        if use_hotwords:
            kwargs.update(
                decoding_method="modified_beam_search",
                max_active_paths=4,
                hotwords_file=hotwords_file,
                hotwords_score=1.5,
            )
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(**kwargs)
        self._stream = self.recognizer.create_stream()

    def reset(self):
        self._stream = self.recognizer.create_stream()

    def accept_waveform(self, samples: np.ndarray):
        """喂入 16k float32 音频, 返回最新部分识别文本。"""
        if self._stream is None or samples.size == 0:
            return ""
        self._stream.accept_waveform(16000, samples)
        while self.recognizer.is_ready(self._stream):
            self.recognizer.decode_stream(self._stream)
        return self.recognizer.get_result(self._stream)

    def finalize(self):
        """结束: 返回最终文本。"""
        if self._stream is None:
            return ""
        return self.recognizer.get_result(self._stream)
