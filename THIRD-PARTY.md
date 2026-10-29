# Сторонние компоненты

Код Dubbing Studio распространяется по лицензии MIT (файл `LICENSE`).
Установщик для Windows содержит или скачивает компоненты, у каждого из
которых своя лицензия.

## Внутри установщика

| Компонент | Лицензия | Где лежит текст |
|---|---|---|
| [Python 3.12](https://www.python.org), сборка [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | PSF-2.0 и лицензии встроенных библиотек | `python\LICENSE.txt` в папке программы |
| [uv](https://github.com/astral-sh/uv) | MIT или Apache-2.0 | репозиторий uv |
| [Tauri](https://tauri.app) и его зависимости на Rust | MIT или Apache-2.0 | репозиторий Tauri |

## Скачивается при первом запуске

| Компонент | Лицензия |
|---|---|
| Пакеты Python из `uv.lock` (faster-whisper, CTranslate2, NumPy и другие) | их открытые лицензии (MIT, BSD, Apache-2.0) |
| NVIDIA cuBLAS (`nvidia-cublas-cu12`, только при видеокарте NVIDIA) | лицензия NVIDIA на ПО |
| [FFmpeg](https://ffmpeg.org), [LGPL-сборка BtbN](https://github.com/BtbN/FFmpeg-Builds) | LGPL-2.1 или новее |
| Модели Whisper (при первом распознавании) | MIT |

## Ставится по выбору в настройках

Движки озвучки, отделения фона и разделения на говорящих ставятся в свои
окружения только когда пользователь их выбирает. У их кода и **весов
моделей** свои лицензии, они показаны рядом с каждым движком в настройках:

| Движок | Лицензия |
|---|---|
| OmniVoice | код Apache-2.0, веса CC-BY-NC 4.0 (только некоммерческое использование) |
| Chatterbox Multilingual | MIT (код и веса) |
| Demucs | MIT (код и модели) |
| sherpa-onnx, модели 3D-Speaker и сегментации pyannote | Apache-2.0 и MIT |

Если дублированные ролики приносят доход, проверьте лицензию выбранной модели.
