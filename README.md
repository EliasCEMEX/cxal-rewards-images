# CXAL Rewards Images

Repositorio que aloja las imágenes de los premios del catálogo de CXAL, sincronizadas desde el proveedor Rewardix.

Las imágenes se sirven vía GitHub Pages y se consumen desde los correos de marketing generados por el pipeline de recomendaciones.

## Estructura

- `premios/` — imágenes de cada premio, nombradas por su ID (`{id_premio}.jpg`).
- `mapeo_imagenes.csv` — relación entre ID de premio y URL pública.
- `sync_imagenes.py` — script de sincronización con el proveedor.

## URL base

```
https://EliasCEMEX.github.io/cxal-rewards-images/premios/{id}.jpg
```