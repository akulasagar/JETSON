import sounddevice as sd
import soundfile as sf

data, fs = sf.read('test.wav')
sd.play(data, fs)
sd.wait()
