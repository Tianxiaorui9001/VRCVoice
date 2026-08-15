"""录音模块: 基于 sounddevice, 支持指定麦克风设备。"""
import threading
import numpy as np
import sounddevice as sd


class Recorder:
    # 虚拟/非物理设备特征词(默认选择时要避开, 尤其 Pico 串流麦克风)
    VIRTUAL_MARKERS = ("pico", "steam", "droidcam", "virtual", "虚拟",
                       "wave", "netease", "网易", "nvidia", "loopback", "mix",
                       "sound mapper", "mapper", "主声音", "primary", "混音")

    def __init__(self, sample_rate: int = 16000, device: str = ""):
        self.sample_rate = sample_rate
        self.device = device          # 设备名(空 = 默认设备)
        self._recording = False
        self._chunks = []
        self._lock = threading.Lock()
        self._stream = None

    @staticmethod
    def is_virtual(name: str) -> bool:
        n = (name or "").lower()
        return any(m in n for m in Recorder.VIRTUAL_MARKERS)

    @staticmethod
    def suggest_default_mic() -> str:
        """智能默认麦克风: 系统默认若是虚拟设备(如 Pico 串流麦克风)则选第一个物理输入设备。"""
        try:
            devs = sd.query_devices()
            inputs = [d for d in devs if d["max_input_channels"] > 0]
            if not inputs:
                return ""
            default_idx = sd.default.device[0]
            if default_idx is not None and default_idx >= 0 and default_idx < len(devs):
                name = devs[default_idx]["name"]
                if not Recorder.is_virtual(name):
                    return name
            for d in inputs:
                if not Recorder.is_virtual(d["name"]):
                    return d["name"]
            return inputs[0]["name"]
        except Exception:
            return ""

    @staticmethod
    def list_devices() -> list:
        """返回可用输入设备 [(名称, 是否默认), ...]"""
        try:
            devs = sd.query_devices()
            out = []
            for i, d in enumerate(devs):
                if d["max_input_channels"] > 0:
                    out.append((d["name"], i == sd.default.device[0]))
            return out
        except Exception as e:
            print(f"[recorder] 枚举设备失败: {e}")
            return []

    @staticmethod
    def resolve_device(name: str):
        """按名称找设备索引; 找不到回默认。"""
        if not name:
            return None
        try:
            devs = sd.query_devices()
            for i, d in enumerate(devs):
                if d["name"] == name:
                    return i
        except Exception:
            pass
        return None

    def start(self):
        if self._recording:
            return
        self._recording = True
        self._chunks = []
        idx = self.resolve_device(self.device)
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=idx,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[recorder] 打开麦克风失败({self.device}): {e}")
            self._recording = False
            raise

    def _callback(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._chunks.append(indata[:, 0].copy())

    def stop(self):
        if not self._recording:
            return b""
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._lock:
            data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
            self._chunks = []
        return data

    @property
    def is_recording(self) -> bool:
        return self._recording

    def get_partial(self):
        """当前已录到的音频(用于实时 ASR 喂数据)。"""
        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._chunks)
