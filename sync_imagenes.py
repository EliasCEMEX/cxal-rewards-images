"""
Sincroniza imágenes de Rewardix al repositorio local
y las publica en GitHub Pages.

Puede ejecutarse de dos formas:

1. Como script independiente:
       python sync_imagenes.py
       python sync_imagenes.py --force
       python sync_imagenes.py --no-push

2. Como módulo importado desde otro script:
       from sync_imagenes import ejecutar_sync
       resultado = ejecutar_sync(archivo_rewardix, force=False, do_push=True)

Flujo:
1. Asegura las entradas manuales COTIZA (14 y 16) en el mapeo.
2. Lee el archivo de Rewardix (xlsx o csv) con columnas ID INTERNO + IMAGEN.
3. Para cada premio, descarga y optimiza la imagen.
4. La guarda en ./premios/{id}.jpg
5. Actualiza el mapeo en ./mapeo_imagenes.csv
6. Hace commit y push a GitHub Pages (solo si hubo cambios reales).
"""

import os
import csv
import time
import argparse
import logging
import subprocess
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
from PIL import Image

# Por defecto el archivo está en Downloads; si se llama como módulo se sobrescribe.
ARCHIVO_REWARDIX_DEFAULT = Path(
    r"C:\Users\e-yamiledlbl\Downloads\CEMEX Catalogo imagenes (1).xlsx"
)

# El mapeo y la carpeta de imágenes siempre viven en el repo del script
DIR_REPO = Path(__file__).resolve().parent
ARCHIVO_MAPEO = DIR_REPO / "mapeo_imagenes.csv"
CARPETA_IMAGENES = DIR_REPO / "premios"

GITHUB_USER = "EliasCEMEX"
NOMBRE_REPO = "cxal-rewards-images"
URL_BASE_PUBLICA = f"https://{GITHUB_USER}.github.io/{NOMBRE_REPO}/"

# Nombres de columnas esperadas en el archivo del proveedor
COL_ID = "ID INTERNO"
COL_URL = "IMAGEN"
HEADER_ROW = 1   # header en la fila 2 del Excel (índice 1)

# Optimización de imágenes
OPTIMIZAR = True
MAX_ANCHO = 600
CALIDAD_JPEG = 80
PAUSA_ENTRE_DESCARGAS = 0.5

# User-Agent para evitar bloqueos por bot
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# IDS COTIZA: imágenes manuales que SIEMPRE se preservan.
COTIZA_MANUAL = {
    "14": {
        "nombre_archivo": "14.jpg",
        "descripcion": "COTIZA TU VEHICULO",
    },
    "16": {
        "nombre_archivo": "16.jpg",
        "descripcion": "COTIZA TU VIAJE",
    },
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sync")

# UTILIDADES
def cargar_mapeo_local() -> dict:
    if not ARCHIVO_MAPEO.exists():
        return {}
    mapeo = {}
    with open(ARCHIVO_MAPEO, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapeo[row["id_premio"]] = row
    return mapeo


def guardar_mapeo_local(mapeo: dict) -> None:
    campos = ["id_premio", "url_propia", "url_rewardix_original", "fecha_sincronizacion"]
    with open(ARCHIVO_MAPEO, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for row in mapeo.values():
            writer.writerow(row)


def descargar_y_optimizar(url: str) -> bytes:
    r = requests.get(url, timeout=30, headers=HEADERS)
    r.raise_for_status()

    if not OPTIMIZAR:
        return r.content

    img = Image.open(BytesIO(r.content))
    if img.width > MAX_ANCHO or img.height > MAX_ANCHO:
        img.thumbnail((MAX_ANCHO, MAX_ANCHO))
    buffer = BytesIO()
    img.convert("RGB").save(buffer, format="JPEG",
                            quality=CALIDAD_JPEG, optimize=True)
    return buffer.getvalue()


def git_commit_push() -> None:
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    try:
        # cwd asegura que git corra dentro del repo correcto aunque
        # se llame desde otro proceso (p.ej. cxal_recommender.py)
        subprocess.run(["git", "add", "."], check=True, cwd=DIR_REPO)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=DIR_REPO,
        )
        if result.returncode == 0:
            log.info("No hay cambios para commitear")
            return
        subprocess.run(
            ["git", "commit", "-m", f"Sync imagenes {fecha}"],
            check=True,
            cwd=DIR_REPO,
        )
        subprocess.run(["git", "push"], check=True, cwd=DIR_REPO)
        log.info("Cambios subidos a GitHub Pages")
    except subprocess.CalledProcessError as e:
        log.error(f"Error en git: {e}")


def _leer_archivo_rewardix(ruta: Path) -> list[dict]:
    """
    Lee el archivo de Rewardix (xlsx, xls o csv) y devuelve una lista de dicts
    con las columnas COL_ID y COL_URL.
    Asume header_row=HEADER_ROW para Excel.
    """
    ruta = Path(ruta)
    ext = ruta.suffix.lower()

    if ext in (".xlsx", ".xls", ".xlsm"):
        df = pd.read_excel(ruta, header=HEADER_ROW)
    elif ext == ".csv":
        df = pd.read_csv(ruta)
    else:
        raise ValueError(f"Formato de archivo no soportado: {ext}")

    if COL_ID not in df.columns or COL_URL not in df.columns:
        raise KeyError(
            f"No se encontraron las columnas '{COL_ID}' y/o '{COL_URL}' "
            f"en {ruta}. Columnas detectadas: {list(df.columns)}"
        )

    df = df[[COL_ID, COL_URL]].copy()
    df = df.dropna(subset=[COL_ID, COL_URL])
    return df.to_dict("records")

# FUNCIÓN PÚBLICA: ejecutar_sync
def ejecutar_sync(
    archivo_rewardix: Path | str | None,
    force: bool = False,
    do_push: bool = True,
) -> dict:
    """
    Ejecuta el ciclo completo de sincronización de imágenes.

    Parámetros:
        archivo_rewardix: ruta al export del proveedor. Si es None,
            se SALTA la descarga de nuevas imágenes y solo se asegura
            que COTIZA esté en el mapeo (caso "usar mapeo existente").
        force: si True, re-descarga imágenes aunque ya existan en el mapeo.
        do_push: si True, hace git push al final (solo si hubo cambios).

    Devuelve un dict con el resumen:
        {
            "nuevos": int,
            "actualizados": int,
            "saltados": int,
            "errores": int,
            "cotiza_modificado": bool,
            "total_mapeo": int,
            "skip_rewardix": bool,
        }
    """
    CARPETA_IMAGENES.mkdir(parents=True, exist_ok=True)
    mapeo = cargar_mapeo_local()

    # 1) Asegurar entradas COTIZA
    cotiza_modificado = False
    for cotiza_id, info in COTIZA_MANUAL.items():
        ruta_local = CARPETA_IMAGENES / info["nombre_archivo"]
        url_propia = f"{URL_BASE_PUBLICA}{CARPETA_IMAGENES.name}/{info['nombre_archivo']}"

        if not ruta_local.exists():
            log.warning(
                f"!! Falta imagen manual para {info['descripcion']} (ID {cotiza_id}): "
                f"esperada en {ruta_local}. Súbela al repo antes del próximo envío."
            )

        existente = mapeo.get(cotiza_id)
        necesita_update = (
            existente is None
            or existente.get("url_propia") != url_propia
            or existente.get("url_rewardix_original") != "MANUAL"
        )

        if necesita_update:
            mapeo[cotiza_id] = {
                "id_premio": cotiza_id,
                "url_propia": url_propia,
                "url_rewardix_original": "MANUAL",
                "fecha_sincronizacion": datetime.now(timezone.utc).isoformat(),
            }
            cotiza_modificado = True
            log.info(
                f"Entrada COTIZA {cotiza_id} ({info['descripcion']}) inicializada/actualizada"
            )

    log.info(f"Entradas COTIZA aseguradas: {list(COTIZA_MANUAL.keys())}")

    # 2) Sincronizar imágenes desde Rewardix (si hay archivo)
    nuevos = 0
    actualizados = 0
    errores = 0
    saltados = 0
    skip_rewardix = False

    if archivo_rewardix is None:
        log.warning(
            "No se proporcionó archivo de Rewardix. "
            "Se mantendrá el mapeo existente sin agregar nuevas imágenes."
        )
        skip_rewardix = True
    else:
        archivo_rewardix = Path(archivo_rewardix)
        if not archivo_rewardix.exists():
            log.error(f"No se encontró el archivo de Rewardix: {archivo_rewardix}")
            skip_rewardix = True

    if not skip_rewardix:
        try:
            filas = _leer_archivo_rewardix(archivo_rewardix)
        except Exception as e:
            log.error(f"Error leyendo archivo de Rewardix: {e}")
            filas = []
            skip_rewardix = True

        total = len(filas)
        log.info(f"Procesando {total} premios del archivo de Rewardix")

        for i, row in enumerate(filas, 1):
            id_premio = str(row.get(COL_ID, "")).strip()
            url_rewardix = str(row.get(COL_URL, "")).strip()

            # Limpieza adicional para ids tipo "12345.0"
            try:
                id_premio = str(int(float(id_premio)))
            except (ValueError, TypeError):
                pass

            if not id_premio or not url_rewardix or url_rewardix.lower() == "nan":
                continue

            # Proteger IDs COTIZA contra cualquier sobreescritura accidental
            if id_premio in COTIZA_MANUAL:
                log.warning(
                    f"[{i}/{total}] Saltando ID {id_premio}: reservado para imagen manual COTIZA"
                )
                saltados += 1
                continue

            if id_premio in mapeo and not force:
                saltados += 1
                continue

            try:
                log.info(f"[{i}/{total}] Descargando premio {id_premio}...")
                bytes_img = descargar_y_optimizar(url_rewardix)

                nombre_archivo = f"{id_premio}.jpg"
                ruta = CARPETA_IMAGENES / nombre_archivo
                with open(ruta, "wb") as f_img:
                    f_img.write(bytes_img)

                url_propia = f"{URL_BASE_PUBLICA}{CARPETA_IMAGENES.name}/{nombre_archivo}"

                ya_existia = id_premio in mapeo
                mapeo[id_premio] = {
                    "id_premio": id_premio,
                    "url_propia": url_propia,
                    "url_rewardix_original": url_rewardix,
                    "fecha_sincronizacion": datetime.now(timezone.utc).isoformat(),
                }
                if ya_existia:
                    actualizados += 1
                else:
                    nuevos += 1

                time.sleep(PAUSA_ENTRE_DESCARGAS)

            except Exception as e:
                log.error(f"Error con premio {id_premio}: {e}")
                errores += 1

    # 3) Guardar mapeo y resumen
    guardar_mapeo_local(mapeo)

    log.info("=" * 60)
    log.info("Resumen de sincronización:")
    log.info(f"  Skip Rewardix:    {skip_rewardix}")
    log.info(f"  Nuevos:           {nuevos}")
    log.info(f"  Actualizados:     {actualizados}")
    log.info(f"  Saltados:         {saltados}")
    log.info(f"  Errores:          {errores}")
    log.info(f"  COTIZA modif.:    {cotiza_modificado}")
    log.info(f"  Total mapeo:      {len(mapeo)}")
    log.info(f"  COTIZA fijos:     {len(COTIZA_MANUAL)}")
    log.info("=" * 60)

    # Push si hubo cambios reales
    if do_push and (nuevos > 0 or actualizados > 0 or cotiza_modificado):
        git_commit_push()

    return {
        "nuevos": nuevos,
        "actualizados": actualizados,
        "saltados": saltados,
        "errores": errores,
        "cotiza_modificado": cotiza_modificado,
        "total_mapeo": len(mapeo),
        "skip_rewardix": skip_rewardix,
        "ruta_mapeo": str(ARCHIVO_MAPEO),
    }

# MAIN (modo script)
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-descarga aunque ya esté sincronizado")
    parser.add_argument("--no-push", action="store_true",
                        help="No hace git push al final")
    parser.add_argument("--archivo", type=str, default=str(ARCHIVO_REWARDIX_DEFAULT),
                        help="Ruta al archivo de Rewardix (xlsx o csv)")
    args = parser.parse_args()

    ejecutar_sync(
        archivo_rewardix=args.archivo,
        force=args.force,
        do_push=not args.no_push,
    )


if __name__ == "__main__":
    main()
