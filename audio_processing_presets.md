# Audio Processing Presets — Bumblebee Bot

Per-character FFmpeg and Python presets matched to 1950s–1970s broadcast styles.

---

## FFmpeg Presets

### 1950s BBC Radio (mono, narrow, warm)
Applies to: James Beard, Brian Widlake era
```bash
ffmpeg -i input.wav -ac 1 -ar 16000 -af \
"highpass=f=120,lowpass=f=3500,acompressor=threshold=-20dB:ratio=3:attack=5:release=50,\
aphaser=delay=2:decay=0.4,volume=1.2" output_bbc1950s.wav
```

### 1960s American AM Radio (Wolfman Jack style)
Applies to: Wolfman Jack, Don Steele, Murray the K, Cousin Brucie
```bash
ffmpeg -i input.wav -ac 1 -ar 11025 -af \
"acompressor=threshold=-25dB:ratio=6:attack=2:release=100,\
highpass=f=300,lowpass=f=2800,volume=1.4" output_am1960s.wav
```
Note: `distortion` filter referenced in source — use `aeval` or `volume` overdrive workaround in FFmpeg.

### 1970s Colour TV (BBC / PBS)
Applies to: Keith Floyd, Graham Kerr, Vincent Duggleby, Julia Child
```bash
ffmpeg -i input.wav -ac 1 -ar 22050 -af \
"acompressor=threshold=-18dB:ratio=2.5:attack=10:release=80,\
highpass=f=80,lowpass=f=8000,aecho=0.8:0.88:60:0.4" output_tv1970s.wav
```

### 1960s BBC Current Affairs (clean, dry)
Applies to: Brian Widlake, Alan Watson, John Tusa, Robin Day, Richard Dimbleby
```bash
ffmpeg -i input.wav -ac 1 -ar 16000 -af \
"highpass=f=100,lowpass=f=4000,acompressor=threshold=-22dB:ratio=4:attack=8:release=60,\
volume=1.1" output_bbc1960s_news.wav
```

### 1960s BBC Mono Broadcast (general named characters)
Applies to: Churchill, MLK, JFK, FDR, Queen Elizabeth, Fanny Cradock
```bash
ffmpeg -i input.wav -ac 1 -ar 16000 -af \
"highpass=f=120,lowpass=f=3500,acompressor=threshold=-20dB:ratio=3:attack=5:release=50,\
aphaser=delay=2:decay=0.4,volume=1.2" output_bbc_mono.wav
```

---

## Python (pydub) Presets

### BBC 1960s Mono
```python
from pydub import AudioSegment, effects

audio = AudioSegment.from_file("input.wav")
audio = audio.set_channels(1).set_frame_rate(16000)
audio = effects.low_pass_filter(audio, 3500)
audio = effects.high_pass_filter(audio, 120)
audio = audio + 2  # slight gain
audio.export("bbc1960s.wav", format="wav")
```

### AM Radio 1960s
```python
from pydub import AudioSegment, effects

audio = AudioSegment.from_file("input.wav")
audio = audio.set_channels(1).set_frame_rate(11025)
audio = effects.low_pass_filter(audio, 2800)
audio = effects.high_pass_filter(audio, 300)
audio.export("am1960s.wav", format="wav")
```

---

## Character → Preset Mapping

| Character | Preset |
|---|---|
| Winston Churchill | BBC 1960s Mono |
| MLK, JFK, FDR | BBC 1960s Mono |
| Queen Elizabeth II, King George VI | BBC 1960s Mono |
| Fanny Cradock | BBC 1960s Mono |
| Richard Dimbleby, Robin Day | BBC 1960s Current Affairs |
| Brian Widlake, Alan Watson, John Tusa | BBC 1960s Current Affairs |
| Vincent Duggleby | 1970s Colour TV |
| Julia Child | 1970s Colour TV |
| Keith Floyd, Graham Kerr | 1970s Colour TV |
| James Beard | 1950s BBC Radio |
| Wolfman Jack, Don Steele, Murray the K | 1960s American AM Radio |
| Cousin Brucie, Robert W. Morgan | 1960s American AM Radio |
| John Wayne, Sergeant Hartman | 1960s American AM Radio |
| David Attenborough | 1970s Colour TV |
| Optimus Prime | 1970s Colour TV (slight overdrive) |
| Alan Whicker, Cliff Michelmore | BBC 1960s Documentary |
| Lowell Thomas | 1950s American Radio |
| Judith Chalmers, John Carter | 1970s ITV Colour TV |
| Charles Kuralt | 1970s CBS Field Recording |
| Don Herbert | 1960s Current Affairs (clean) |

### BBC 1960s Documentary (Alan Whicker style)
Applies to: Alan Whicker, Cliff Michelmore, Lowell Thomas
```bash
ffmpeg -i input.wav -ac 1 -ar 16000 -af \
"highpass=f=100,lowpass=f=5000,acompressor=threshold=-20dB:ratio=3,volume=1.1" output_bbc_doc.wav
```

### 1970s ITV Colour TV (Travel — Chalmers, Carter)
Applies to: Judith Chalmers, John Carter
```bash
ffmpeg -i input.wav -ac 1 -ar 22050 -af \
"acompressor=threshold=-18dB:ratio=2.5,highpass=f=80,lowpass=f=8000" output_itv1970s.wav
```

### 1950s American Radio (Lowell Thomas)
Applies to: Lowell Thomas
```bash
ffmpeg -i input.wav -ac 1 -ar 11025 -af \
"highpass=f=200,lowpass=f=3000,acompressor=threshold=-25dB:ratio=4" output_thomas1950s.wav
```

### 1970s CBS Field Recording (Charles Kuralt)
Applies to: Charles Kuralt
```bash
ffmpeg -i input.wav -ac 1 -ar 22050 -af \
"acompressor=threshold=-20dB:ratio=2,highpass=f=60,lowpass=f=9000,aecho=0.6:0.7:40:0.2,volume=1.0" output_cbs_field.wav
```

---

## TODO
- [ ] Add preset key to character_voices.json so orchestrator can select filter per character automatically
- [ ] Test each preset against a reference TTS output and compare raw vs filtered
- [ ] Tune aecho values per character — some (Wolfman Jack) need more, some (Widlake) need none
