# АКВАЧЕК

Система спутникового мониторинга и оптимизации водоподачи на рисовых оросительных системах
Краснодарского края. Сайт-продукт: открывается по QR с телефона и сразу показывает работающий
инструмент диспетчера — карту массива, расчёт и ведомость водоподачи.

## Что внутри

| Часть | Что делает |
|---|---|
| `pipeline/` | Добыча РЕАЛЬНЫХ данных: Sentinel-2 L2A (STAC + публичные COG на AWS), ERA5 (ECMWF), Copernicus DEM GLO-30 |
| `data/` | Результат пайплайна: снимки, индексы, контуры чеков, метеоряд |
| `backend/` | FastAPI: FAO-56 Penman–Monteith, водный баланс чека, задача ЛП (PuLP + CBC), базовые стратегии, Монте-Карло, экономика |
| `web/` | Интерфейс:React + Vite + TypeScript, карта Leaflet, графики на SVG, оформление — «рабочий чертёж мелиоратора» |

Синтетических данных нет. Если источник недоступен, пайплайн завершается ошибкой, а не подставляет
заглушку; сайт в этом случае показывает состояние загрузки.

## Запуск локально

```bash
# 1. данные (если data/ пуст — запустить workflow fetch-data в GitHub Actions и сделать git pull)

# 2. сервер
python -m venv venv && source venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000

# 3. интерфейс (dev, с прокси на API)
cd web && npm install && npm run dev
```

Продакшн-сборка одной командой:

```bash
cd web && npm run build && cd ..
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 2
# сайт: http://localhost:8000
```

## Развёртывание на VPS

```bash
sudo ./setup.sh                        # http://IP:8080
sudo DOMAIN=akvachek.ru ./setup.sh     # + nginx и HTTPS
```

или через compose:

```bash
docker compose up -d --build           # http://IP:8080
```

## Обновление данных

Любое изменение файлов `pipeline/` запускает workflow `fetch-data` в GitHub Actions. Он скачивает
снимки и погоду, строит контуры чеков и коммитит результат в `data/`. После этого:

```bash
git pull && docker compose up -d --build
```

## Модель (открыто, параметры видны на сайте)

* ET₀ — FAO-56 Penman–Monteith, ур. 6, на суточных данных ERA5;
* ETc = Kc(фаза)·ET₀, Kc = 1,05 / 1,20 / 0,95 при 30 / 60 / 53 сутках;
* фильтрация F = F₀ + 0,15·Δz мм/сут, F₀ калибруется по нормативной норме 16 009 м³/га;
* КПД подачи = 0,80 − 0,012·Δz (упрощённая модель этапа 1, калибровка — этап 2);
* эффективные осадки 0,7·P; разовое затопление и насыщение 2 000 м³/га;
* отклик урожая — FAO-33: потеря = Σ Ky,p·w_p·g(δ_p), g — кусочно-линейная функция дефицита;
* оптимизация — ЛП, минимум суммарной потери урожая при лимите водозабора, решатель CBC.

## Источники

* Sentinel-2 L2A — Copernicus/ESA, публичные COG на AWS Open Data («Содержит обработанные данные Copernicus Sentinel»);
* ERA5 — ECMWF, Historical Weather API (Open-Meteo);
* Copernicus DEM GLO-30 — ESA / AWS Open Data;
* FAO Irrigation and Drainage Papers 56 и 33;
* статистика по рису — Росстат и Национальный рисовый союз, 2025 г.

Персональные данные (телефон, почта, адрес) в этой версии не публикуются.
