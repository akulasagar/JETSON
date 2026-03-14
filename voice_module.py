import os
import hashlib
import platform
import subprocess
import threading
import queue
import logging

logger = logging.getLogger(__name__)

SYSTEM = platform.system()

voice_queue = queue.Queue()

# Paths
_BASE_DIR = os.path.dirname(__file__)
_EN_MODEL = os.path.join(_BASE_DIR, "models", "en_US-amy-low.onnx")
_TE_MODEL = os.path.join(_BASE_DIR, "models", "te_IN-maya-medium.onnx")
_PIPER_BIN = os.path.join(_BASE_DIR, "venv", "bin", "piper")
_CACHE_DIR = os.path.join(_BASE_DIR, "voice_cache")


def _get_cache_path(text, lang):
    """Get cached wav file path for a given text+lang combo."""
    key = hashlib.md5(f"{lang}:{text}".encode()).hexdigest()
    return os.path.join(_CACHE_DIR, f"{key}.wav")


def _generate_piper_wav(text, model_path, wav_path):
    """Generate wav file using piper CLI. Returns True if successful."""
    try:
        subprocess.run(
            [_PIPER_BIN, "--model", model_path, "--output_file", wav_path],
            input=text.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15
        )
        return os.path.exists(wav_path) and os.path.getsize(wav_path) > 44
    except Exception as e:
        logger.debug(f"Piper generate error: {e}")
        return False


def _play_wav(wav_path):
    """Play a wav file using aplay."""
    try:
        subprocess.run(["aplay", wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception as e:
        logger.debug(f"aplay error: {e}")


def _play_espeak(text, lang):
    """Fallback: play with espeak-ng."""
    try:
        if lang == "te":
            subprocess.run(["espeak-ng", "-v", "te", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(["espeak-ng", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.debug(f"espeak error: {e}")


def linux_voice_worker():
    # Create cache directory
    os.makedirs(_CACHE_DIR, exist_ok=True)

    piper_en_ok = os.path.exists(_PIPER_BIN) and os.path.exists(_EN_MODEL)
    piper_te_ok = os.path.exists(_PIPER_BIN) and os.path.exists(_TE_MODEL)
    print(f"[VOICE] Piper EN: {piper_en_ok}, TE: {piper_te_ok}, Cache: {_CACHE_DIR}")

    while True:
        text, lang = voice_queue.get()
        print(f"[VOICE - {lang}] {text}")
        try:
            model = _TE_MODEL if lang == "te" else _EN_MODEL
            piper_ok = piper_te_ok if lang == "te" else piper_en_ok
            cache_path = _get_cache_path(text, lang)

            if piper_ok:
                # Check cache first — instant playback if cached
                if os.path.exists(cache_path):
                    _play_wav(cache_path)
                else:
                    # First time: generate, cache, and play
                    if _generate_piper_wav(text, model, cache_path):
                        _play_wav(cache_path)
                    else:
                        _play_espeak(text, lang)
            else:
                _play_espeak(text, lang)
        except Exception as e:
            logger.debug(f"Voice error: {e}")
            print(f"Voice error: {e}")
        voice_queue.task_done()


def windows_voice_worker():
    while True:
        text, lang = voice_queue.get()
        print(f"[VOICE - {lang}] {text}")
        try:
            command = f'''
            Add-Type -AssemblyName System.Speech;
            $speak = New-Object System.Speech.Synthesis.SpeechSynthesizer;
            $speak.Speak("{text}");
            '''
            subprocess.run(
                ["powershell", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            logger.debug(f"Voice error: {e}")
            print("Voice error:", e)
        voice_queue.task_done()


if SYSTEM == "Windows":
    voice_thread = threading.Thread(target=windows_voice_worker, daemon=True)
else:
    voice_thread = threading.Thread(target=linux_voice_worker, daemon=True)

voice_thread.start()


def speak(text, lang="en"):
    voice_queue.put((text, lang))


def speak_dual(en_text, te_text):
    speak(en_text, "en")
    speak(te_text, "te")
