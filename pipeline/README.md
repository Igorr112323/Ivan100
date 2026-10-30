stages:
  1. fetch_era5.py    — суточный ряд ERA5 (T, влажность, ветер, радиация, осадки, эталонная ET0)
  2. fetch_sentinel.py — реальные снимки Sentinel-2 L2A над массивом, NDVI/NDWI на сетке 10 м
  3. build_checks.py  — сегментация контуров чеков, отметки из Copernicus DEM, зональная статистика

Любое изменение файлов pipeline/ запускает .github/workflows/fetch-data.yml
