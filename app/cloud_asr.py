"""云端 ASR 后端: 调用 OpenAI 兼容的 /v1/audio/transcriptions 接口。
适用于空间紧张/低配机器(本地模型约 500MB, 云端不占空间)。
接口兼容: 硅基流动 SiliconFlow / OpenAI / 各类中转站。
"""
import io
import wave
import numpy as np
import requests


class CloudASR:
    def __init__(self, endpoint: str, api_key: str, model: str,
                 language: str = "", timeout_sec: int = 30):
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.language = language
        self.timeout = timeout_sec
        self._chunks = []
        self.last_error = ""

    def reset(self):
        self._chunks = []
        self.last_error = ""

    def accept_waveform(self, samples: np.ndarray) -> str:
        """云端不流式, 只攒音频, 返回空串。"""
        if samples is not None and samples.size > 0:
            self._chunks.append(samples)
        return ""

    def finalize(self) -> str:
        if not self._chunks:
            return ""
        if not self.api_key:
            self.last_error = "未配置云端 API Key"
            return ""
        audio = np.concatenate(self._chunks)
        wav_bytes = self._to_wav(audio)
        try:
            files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
            data = {"model": self.model}
            if self.language:
                data["language"] = self.language
            resp = requests.post(
                self.endpoint, headers={"Authorization": f"Bearer {self.api_key}"},
                files=files, data=data, timeout=self.timeout,
            )
            if resp.status_code != 200:
                self.last_error = f"云端返回 {resp.status_code}: {resp.text[:200]}"
                return ""
            out = resp.json()
            return out.get("text", "").strip()
        except Exception as e:
            self.last_error = f"云端请求失败: {e}"
            return ""

    @staticmethod
    def _to_wav(samples: np.ndarray, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            w.writeframes(pcm.tobytes())
        return buf.getvalue()
