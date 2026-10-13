import os
from pathlib import Path

import pytest
import sherpa_onnx

from marsdog_voice_interaction.utils.uploaded_audio import normalize_pcm16_wav


MODEL_DIR = os.environ.get("MARSDOG_KWS_TEST_MODEL_DIR")


@pytest.mark.skipif(MODEL_DIR is None, reason="需要官方 KWS 模型和配套录音")
@pytest.mark.parametrize(
    ("recording", "expected_keyword"),
    [("zh_3.wav", "文森特卡索"), ("zh_4.wav", "蒋友伯"), ("en_0.wav", "LIGHT_UP")],
)
def test_keyword_is_detected_before_the_recording_finishes(recording, expected_keyword):
    model_dir = Path(MODEL_DIR)
    spotter = sherpa_onnx.KeywordSpotter(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx"),
        decoder=str(model_dir / "decoder-epoch-13-avg-2-chunk-8-left-64.onnx"),
        joiner=str(model_dir / "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx"),
        keywords_file=str(model_dir / "test_wavs" / "keywords.txt"),
        keywords_threshold=0.4,
        num_trailing_blanks=1,
        num_threads=2,
    )
    samples, sample_rate, _ = normalize_pcm16_wav(
        (model_dir / "test_wavs" / recording).read_bytes(),
    )
    stream = spotter.create_stream()
    detected_at = None
    for start in range(0, len(samples), 320):
        spotter_input = samples[start:start + 320]
        stream.accept_waveform(sample_rate, spotter_input)
        while spotter.is_ready(stream):
            spotter.decode_stream(stream)
            keyword = spotter.get_result(stream)
            if keyword:
                spotter.reset_stream(stream)
            if keyword == expected_keyword:
                detected_at = (start + len(spotter_input)) / sample_rate
                break
        if detected_at is not None:
            break
    assert detected_at is not None
    assert detected_at < len(samples) / sample_rate - 1.0
