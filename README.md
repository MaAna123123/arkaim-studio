# 🎬 videopril — AI Video Highlight Clipper

Локальное веб-приложение для автоматического поиска интересных моментов в видео с помощью ИИ и моментальной нарезки клипов.

## 🚀 Быстрый старт

Просто запустите:
```
start.bat
```
Приложение откроется в браузере по адресу **http://localhost:5173**

---

## ⚙️ Ручной запуск

### Backend (FastAPI)
```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend (Vite)
```bash
cd frontend
npm run dev
```

---

## 🧠 Режимы анализа

### С Gemini API Key (рекомендуется)
- Нажмите ⚙️ **Настройки** в приложении
- Вставьте ваш бесплатный ключ с [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- ИИ анализирует кадры видео по вашему текстовому запросу (смысловой поиск)

### Без API Key (локальный режим)
- Автоматически обнаруживает смены сцен и аудио-пики через FFmpeg
- Работает полностью офлайн

---

## 📁 Структура проекта

```
videopril/
├── backend/
│   ├── main.py              # FastAPI сервер
│   ├── video_processor.py   # FFmpeg работа с видео
│   ├── ai_analyzer.py       # Gemini AI + локальный анализ
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── App.jsx          # Главный компонент
│       ├── components/      # Header, VideoUploader, Player, etc.
│       └── index.css        # Дизайн-система
├── start.bat                # Запуск одной командой
└── README.md
```

---

## 🛠 Технологии
- **Backend:** Python 3.12 · FastAPI · FFmpeg · Google Gemini AI
- **Frontend:** React 19 · Vite · Vanilla CSS (Glassmorphism)
- **ИИ:** Google Gemini 2.0 Flash (бесплатный тариф) / OpenCV fallback
