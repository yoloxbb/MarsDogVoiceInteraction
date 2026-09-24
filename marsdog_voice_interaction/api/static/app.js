"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  recorder: null,
  stream: null,
  chunks: [],
  startedAt: 0,
  timer: null,
  samples: null,
  sampleRate: 0,
  durationMs: 0,
  analysis: null,
  previewUrl: null,
  starting: false,
  analyzing: false,
  saving: false,
  speakers: {},
  maxSamples: 5,
  recordingRawMode: false,
};

function showMessage(message, error = false) {
  $("message").textContent = message;
  $("message").classList.toggle("error", error);
}

function errorText(body, fallback) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.error === "string") return detail.error;
  if (typeof body?.error === "string") return body.error;
  return fallback;
}

function currentShots() {
  return Number(state.speakers[$("speaker-name").value] || 0);
}

function updateControls() {
  const recording = state.recorder?.state === "recording";
  const working = state.starting || state.analyzing || state.saving;
  $("record-button").disabled = recording || working;
  $("stop-button").disabled = !recording;
  $("raw-capture").disabled = recording || working;
  $("suggest-button").disabled = !state.analysis?.suggested_crop_ms || working;
  $("preview-button").disabled = !state.samples || working;
  $("download-button").disabled = !state.samples || working;
  $("save-button").disabled = !state.samples || !state.analysis?.valid_for_enrollment ||
    recording || working || currentShots() >= state.maxSamples;
}

function updateSpeakerCount() {
  const count = currentShots();
  const full = count >= state.maxSamples;
  $("speaker-count").textContent = `已注册 ${count} / ${state.maxSamples} 条样本` +
    (full ? "，此身份已满" : `，还可添加 ${state.maxSamples - count} 条`);
  updateControls();
}

async function refreshSpeakers() {
  try {
    const response = await fetch("/api/v1/speakers", {cache: "no-store"});
    const body = await response.json();
    if (!response.ok) throw new Error(errorText(body, "无法读取样本数"));
    state.speakers = Object.fromEntries(
      (body.speakers || []).map((speaker) => [speaker.name, speaker.shots])
    );
    state.maxSamples = Number(body.max_samples_per_speaker || 5);
    updateSpeakerCount();
  } catch (error) {
    $("speaker-count").textContent = `无法读取样本数：${error.message}`;
    showMessage(error.message, true);
  }
}

function elapsedLabel(milliseconds) {
  const seconds = Math.floor(milliseconds / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:` +
    `${String(seconds % 60).padStart(2, "0")}`;
}

function releaseMicrophone() {
  if (state.timer !== null) clearInterval(state.timer);
  state.timer = null;
  state.stream?.getTracks().forEach((track) => track.stop());
  state.stream = null;
}

function clearPreview() {
  $("preview-audio").pause();
  $("preview-audio").removeAttribute("src");
  $("preview-audio").load();
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
}

async function startRecording() {
  if (state.starting || state.analyzing || state.saving) return;
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    showMessage("浏览器无法使用麦克风。请在本机通过 localhost 打开页面，并允许麦克风权限。", true);
    return;
  }
  state.starting = true;
  updateControls();
  try {
    const rawMode = $("raw-capture").checked;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: rawMode ? {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        channelCount: 1,
      } : true,
    });
    state.recordingRawMode = rawMode;
    state.stream = stream;
    const recorder = new MediaRecorder(stream);
    const track = stream.getAudioTracks()[0];
    const settings = track.getSettings();
    const value = (name) => settings[name] === undefined ? "未报告" : String(settings[name]);
    $("capture-details").textContent =
      `设备：${track.label || "未报告"}；模式：${rawMode ? "关闭处理请求" : "浏览器默认"}；` +
      `采样率：${value("sampleRate")} Hz；回声消除：${value("echoCancellation")}；` +
      `降噪：${value("noiseSuppression")}；自动增益：${value("autoGainControl")}；` +
      `录制格式：${recorder.mimeType || "浏览器默认"}`;
    clearPreview();
    state.analysis = null;
    state.samples = null;
    state.recorder = recorder;
    state.chunks = [];
    $("edit-panel").hidden = true;
    $("record-time").textContent = "00:00";
    recorder.ondataavailable = (event) => {
      if (event.data.size) state.chunks.push(event.data);
    };
    recorder.onstop = () => void finishRecording(recorder.mimeType);
    recorder.onerror = () => {
      releaseMicrophone();
      showMessage("录音失败，请检查麦克风是否仍可用。", true);
      updateControls();
    };
    recorder.start(1000);
    state.startedAt = performance.now();
    state.timer = setInterval(() => {
      const elapsed = performance.now() - state.startedAt;
      $("record-time").textContent = elapsedLabel(elapsed);
      if (elapsed >= 60000) stopRecording();
    }, 250);
    $("record-status").textContent = "正在录音…说完后点击停止";
    showMessage("");
  } catch (error) {
    releaseMicrophone();
    showMessage(`无法开始录音：${error.message}`, true);
  } finally {
    state.starting = false;
    updateControls();
  }
}

function stopRecording() {
  if (state.recorder?.state !== "recording") return;
  state.analyzing = true;
  state.recorder.stop();
  releaseMicrophone();
  $("record-status").textContent = "正在处理录音…";
  updateControls();
}

function encodeWav(samples, sampleRate) {
  const bytes = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(bytes);
  const ascii = (offset, value) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, bytes.byteLength - 8, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return new Blob([bytes], {type: "audio/wav"});
}

async function decodeRecording(blob) {
  const context = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const buffer = await context.decodeAudioData(await blob.arrayBuffer());
    const samples = new Float32Array(buffer.length);
    for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
      const values = buffer.getChannelData(channel);
      for (let i = 0; i < values.length; i++) samples[i] += values[i] / buffer.numberOfChannels;
    }
    return {samples, sampleRate: buffer.sampleRate};
  } finally {
    await context.close();
  }
}

async function finishRecording(mimeType) {
  releaseMicrophone();
  state.analyzing = true;
  updateControls();
  try {
    if (!state.chunks.length) throw new Error("没有录到声音");
    const recording = new Blob(state.chunks, {type: mimeType || state.chunks[0].type});
    const decoded = await decodeRecording(recording);
    if (!decoded.samples.length) throw new Error("没有录到声音");
    state.samples = decoded.samples.subarray(
      0, Math.floor(decoded.sampleRate * 60)
    );
    state.sampleRate = decoded.sampleRate;
    $("capture-details").textContent +=
      `；上传/下载 WAV 采样率：${decoded.sampleRate} Hz（保存时服务端转为 16000 Hz）`;
    state.durationMs = Math.round(state.samples.length * 1000 / decoded.sampleRate);
    $("edit-panel").hidden = false;
    $("crop-start").max = String(state.durationMs);
    $("crop-end").max = String(state.durationMs);
    $("crop-start").value = "0";
    $("crop-end").value = String(state.durationMs);
    updateCrop();
    $("record-status").textContent = `录音完成，时长 ${(state.durationMs / 1000).toFixed(2)} 秒`;
    $("vad-summary").textContent = "正在分析 VAD…";
    $("vad-summary").classList.remove("error");
    const form = new FormData();
    form.append("audio", encodeWav(state.samples, state.sampleRate), "recording.wav");
    const response = await fetch("/api/v1/recordings/vad", {method: "POST", body: form});
    const body = await response.json();
    if (!response.ok) throw new Error(errorText(body, "VAD 分析失败"));
    state.analysis = body;
    if (body.suggested_crop_ms) applySuggestion();
    const ranges = body.speech_ranges_ms || [];
    $("vad-summary").textContent = ranges.length
      ? `VAD 检出 ${ranges.length} 段语音，有效语音 ${(body.speech_duration_ms / 1000).toFixed(2)} 秒。可调整两端后试听。`
      : "VAD 未检测到语音，请重新录制。";
    $("vad-summary").classList.toggle("error", !body.valid_for_enrollment);
    if (!body.valid_for_enrollment && ranges.length) {
      showMessage(`有效语音不足 ${(body.min_speech_duration_ms / 1000).toFixed(1)} 秒，请重新录制。`, true);
    }
  } catch (error) {
    $("record-status").textContent = "录音处理失败，请重新录制";
    $("vad-summary").textContent = `VAD 分析失败：${error.message}`;
    $("vad-summary").classList.add("error");
    showMessage(error.message, true);
  } finally {
    state.analyzing = false;
    updateControls();
  }
}

function updateCrop(changed) {
  const startInput = $("crop-start");
  const endInput = $("crop-end");
  let start = Number(startInput.value);
  let end = Number(endInput.value);
  const gap = Math.min(500, state.durationMs);
  if (end - start < gap) {
    if (changed === "start") start = Math.max(0, end - gap);
    else end = Math.min(state.durationMs, start + gap);
  }
  startInput.value = String(start);
  endInput.value = String(end);
  $("crop-start-label").textContent = `${(start / 1000).toFixed(2)} 秒`;
  $("crop-end-label").textContent = `${(end / 1000).toFixed(2)} 秒`;
  $("crop-duration").textContent = `截取时长：${((end - start) / 1000).toFixed(2)} 秒`;
  clearPreview();
}

function applySuggestion() {
  const crop = state.analysis?.suggested_crop_ms;
  if (!crop) return;
  $("crop-start").value = String(Math.round(crop.start_ms));
  $("crop-end").value = String(Math.round(crop.end_ms));
  updateCrop();
}

function selectedWav() {
  const start = Math.round(Number($("crop-start").value) * state.sampleRate / 1000);
  const end = Math.round(Number($("crop-end").value) * state.sampleRate / 1000);
  return encodeWav(state.samples.subarray(start, end), state.sampleRate);
}

async function previewSelection() {
  if (!state.samples) return;
  clearPreview();
  state.previewUrl = URL.createObjectURL(selectedWav());
  $("preview-audio").src = state.previewUrl;
  try {
    await $("preview-audio").play();
  } catch (_) {
    showMessage("已生成试听片段，请点击播放器上的播放按钮。");
  }
}

function downloadSelection() {
  if (!state.samples) return;
  const url = URL.createObjectURL(selectedWav());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${$("speaker-name").value}-${state.recordingRawMode ? "processing-off" : "default"}.wav`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

async function saveSelection() {
  if ($("save-button").disabled) return;
  state.saving = true;
  updateControls();
  try {
    const name = $("speaker-name").value;
    const form = new FormData();
    form.append("audio", selectedWav(), `${name}.wav`);
    const response = await fetch(`/api/v1/speakers/${name}/samples`, {
      method: "POST", body: form,
    });
    const body = await response.json();
    if (!response.ok) throw new Error(errorText(body, "保存失败"));
    showMessage(`已保存 ${$("speaker-name").selectedOptions[0].textContent} 的第 ${body.sample_id} 条声纹样本。`);
    await refreshSpeakers();
  } catch (error) {
    showMessage(`保存失败：${error.message}`, true);
  } finally {
    state.saving = false;
    updateControls();
  }
}

$("record-button").addEventListener("click", startRecording);
$("stop-button").addEventListener("click", stopRecording);
$("refresh-button").addEventListener("click", refreshSpeakers);
$("speaker-name").addEventListener("change", updateSpeakerCount);
$("crop-start").addEventListener("input", () => updateCrop("start"));
$("crop-end").addEventListener("input", () => updateCrop("end"));
$("suggest-button").addEventListener("click", applySuggestion);
$("preview-button").addEventListener("click", previewSelection);
$("download-button").addEventListener("click", downloadSelection);
$("save-button").addEventListener("click", saveSelection);
window.addEventListener("pagehide", releaseMicrophone);

fetch("/health", {cache: "no-store"}).then((response) => {
  if (!response.ok) throw new Error();
  $("service-status").textContent = "服务已连接";
}).catch(() => {
  $("service-status").textContent = "服务不可用";
  $("service-status").classList.add("error");
});
void refreshSpeakers();
