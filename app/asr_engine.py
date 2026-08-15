"""ASR 引擎: sherpa-onnx 本地流式识别。
模型: sherpa-onnx-streaming-zipformer-bilingual-zh-en (中英双语流式, int8)
模型文件 (放在 models/ 下):
  encoder-epoch-99-avg-1.int8.onnx
  decoder-epoch-99-avg-1.onnx
  joiner-epoch-99-avg-1.int8.onnx
  tokens.txt
"""
import os
import re
import numpy as np
import sherpa_onnx

# 热词文件模板(%APPDATA%\VRCVoice\hotwords.txt 首次启动时创建)
HOTWORDS_TEMPLATE = """# VRCVoice 热词表: 每行一个词, 可带权重(建议 1.0~5.0), 权重越大识别越偏向该词。
# 带 # 的行会被忽略。用「设置-识别-识别热词」编辑, 或直接改本文件后重启生效。
"""


# sherpa-onnx hotwords 硬性约束(经实测确认):
# 1) 行格式必须是 "词 :权重"(冒号前缀)。GUI 的 "词 权重" 空格格式会被当成长词, 编码失败。
# 2) 任一行含 tokens.txt 词表外字符(如 # = | + 等非字母数字) → EncodeBase 返回 false,
#    整个热词表全部丢弃! 所以注释行/PRESET 标记行/特殊字符词必须在此剔除。
# 3) 中文逐字查 tokens.txt 单字(5755 个, 常用字全覆盖); ASCII 字母走 bpe 编码,
#    小写字母不在词表会编成 <unk>(静默无效), 故英文词转大写。
_HOTWORD_BAD_CHARS = set('+-?/()&%=|:#@[]{}<>~`!*^\\"\'')

# tokens.txt 单字符集(懒加载), 用于中文热词可编码性检查
_single_chars = None


def _load_single_chars(model_dir: str) -> set:
    """读取模型 tokens.txt 的全部单字符 token, 用于热词编码预检。
    文件缺失时返回空集(跳过检查)。"""
    global _single_chars
    if _single_chars is not None:
        return _single_chars
    chars = set()
    try:
        with open(os.path.join(model_dir, "tokens.txt"), encoding="utf-8") as f:
            for line in f:
                t = line.split()[0] if line.strip() else ""
                if len(t) == 1:
                    chars.add(t)
    except (OSError, IndexError):
        pass
    _single_chars = chars
    return chars


def _prepare_hotwords(hotwords_file: str, model_dir: str = "") -> str:
    """把 GUI 格式热词文件转换为 sherpa-onnx 兼容格式, 返回临时文件路径。
    转换规则: 去注释/PRESET 行; "词 权重" → "词 :权重"; 英文词转大写;
    丢弃含特殊字符的词(否则整表作废); 中文词逐字检查词表可编码性。
    无有效词返回空字符串(不启用热词偏置)。"""
    if not hotwords_file or not os.path.exists(hotwords_file):
        return ""
    try:
        with open(hotwords_file, "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    content = raw.decode("utf-8-sig", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        try:
            with open(hotwords_file, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except OSError:
            pass
    singles = _load_single_chars(model_dir) if model_dir else set()
    out = []
    for line in content.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        word = parts[0]
        score = parts[1] if len(parts) > 1 else "1.0"
        if score.startswith(":"):
            score = score[1:]  # 兼容用户手写 sherpa 格式
        try:
            fscore = float(score)
        except ValueError:
            fscore = 1.0
        if fscore < 0.5:
            fscore = 1.0
        if word.isascii():
            word = word.upper()  # 词表只有大写单字母/子词, 小写会编成 <unk>
        if any(c in _HOTWORD_BAD_CHARS for c in word):
            continue  # 特殊字符不在词表, 会连累整表作废
        if singles and not all(c in singles for c in word if not c.isascii()):
            continue  # 中文热词里有词表外汉字 → 丢弃(避免杀全表)
        out.append(f"{word} :{fscore:g}")
    if not out:
        return ""
    tmp = hotwords_file + ".sherpa"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(out) + "\n")
    except OSError:
        return ""
    return tmp


# 模型偶发输出 token 文本(静音 SIL / 未知 UNK), 识别结果里直接剔除。
# 不用 \b(汉字也算 \w, 边界不生效), 用 ASCII 字母数字前后断言
_TOKEN_JUNK = re.compile(r"\s*(?<![A-Za-z0-9])(?:SIL|UNK)(?![A-Za-z0-9])")


def _clean_result(text: str) -> str:
    """剔除模型输出的噪声 token(SIL=静音段, UNK=未知词), 压缩多余空格。"""
    if not text:
        return ""
    text = _TOKEN_JUNK.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


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
        # 注意: sherpa 要求每行 "词 :权重" 且不能有注释/特殊字符(否则整表作废),
        # 因此先经 _prepare_hotwords 转换为临时文件再传给 sherpa。
        from .log import data_dir
        hotwords_file = os.path.join(data_dir(), "hotwords.txt")
        if not os.path.exists(hotwords_file):
            try:
                with open(hotwords_file, "w", encoding="utf-8") as f:
                    f.write(HOTWORDS_TEMPLATE)
            except OSError:
                hotwords_file = ""
        bpe_vocab = os.path.join(self.model_dir, "bpe.vocab")
        has_bpe = os.path.exists(bpe_vocab)
        hotwords_file = _prepare_hotwords(hotwords_file, self.model_dir) if hotwords_file else ""
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
            # cjkchar+bpe: 中文热词逐字查单字词表, 英文热词走 bpe 编码(实测两者都必需)
            modeling_unit="cjkchar+bpe" if has_bpe else "cjkchar",
            bpe_vocab=bpe_vocab if has_bpe else "",
        )
        if hotwords_file:
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
        return _clean_result(self.recognizer.get_result(self._stream))

    def finalize(self):
        """结束: 返回最终文本。"""
        if self._stream is None:
            return ""
        return _clean_result(self.recognizer.get_result(self._stream))
