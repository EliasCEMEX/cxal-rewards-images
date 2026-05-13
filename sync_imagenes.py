"""
Sincroniza imágenes de Rewardix al repositorio local
y las publica en GitHub Pages.

Flujo:
1. Lee el CSV de Rewardix (id_premio, url_imagen)
2. Para cada premio, descarga y optimiza la imagen
3. La guarda en ./premios/{id}.jpg
4. Actualiza el mapeo en ./mapeo_imagenes.csv
5. Hace commit y push a GitHub
6. GitHub Pages publica automáticamente

Uso:
    python sync_imagenes.py
    python sync_imagenes.py --force   # re-descarga todo
"""

import os
import csv
import time
import argparse
import logging
import subprocess
from io import BytesIO
from datetime import datetime, timezone

import requests
from PIL import Image

# ============================================================
# CONFIG - ajusta estos valores
# ============================================================
ARCHIVO_REWARDIX = "rewardix_export.csv"     # CSV que te pasen
ARCHIVO_MAPEO = "mapeo_imagenes.csv"         # archivo que se genera

CARPETA_IMAGENES = "premios"

GITHUB_USER = "EliasCEMEX"             
NOMBRE_REPO = "cxal-rewards-images"

URL_BASE_PUBLICA = f"https://{GITHUB_USER}.github.io/{NOMBRE_REPO}/"

# Nombres de columnas esperadas en el CSV de Rewardix
# Ajustar si el CSV que llegue usa otros nombres
COL_ID = "id_premio"
COL_URL = "url_imagen"

# Optimización de imágenes
OPTIMIZAR = True
MAX_ANCHO = 600
CALIDAD_JPEG = 80

# Throttling para no saturar al proveedor
PAUSA_ENTRE_DESCARGAS = 0.5   # segundos

# User-Agent para evitar bloqueos por bot
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sync")


# ============================================================
# UTILIDADES
# ============================================================
def cargar_mapeo_local():
    if not os.path.exists(ARCHIVO_MAPEO):
        return {}
    mapeo = {}
    with open(ARCHIVO_MAPEO, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapeo[row["id_premio"]] = row
    return mapeo


def guardar_mapeo_local(mapeo):
    campos = ["id_premio", "url_propia", "url_rewardix_original", "fecha_sincronizacion"]
    with open(ARCHIVO_MAPEO, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for row in mapeo.values():
            writer.writerow(row)


def descargar_y_optimizar(url):
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


def git_commit_push():
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    try:
        subprocess.run(["git", "add", "."], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if result.returncode == 0:
            log.info("No hay cambios para commitear")
            return
        subprocess.run(["git", "commit", "-m", f"Sync imagenes {fecha}"],
                       check=True)
        subprocess.run(["git", "push"], check=True)
        log.info("Cambios subidos a GitHub Pages")
    except subprocess.CalledProcessError as e:
        log.error(f"Error en git: {e}")


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-descarga aunque ya esté sincronizado")
    parser.add_argument("--no-push", action="store_true",
                        help="No hace git push al final")
    args = parser.parse_args()

    if not os.path.exists(ARCHIVO_REWARDIX):
        log.error(f"No se encontró {ARCHIVO_REWARDIX}")
        return

    os.makedirs(CARPETA_IMAGENES, exist_ok=True)
    mapeo = cargar_mapeo_local()
    nuevos = 0
    actualizados = 0
    errores = 0
    saltados = 0

    with open(ARCHIVO_REWARDIX, newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    total = len(filas)
    log.info(f"Procesando {total} premios del CSV de Rewardix")

    for i, row in enumerate(filas, 1):
        id_premio = str(row[COL_ID]).strip()
        url_rewardix = row[COL_URL].strip()

        if not id_premio or not url_rewardix:
            continue

        if id_premio in mapeo and not args.force:
            saltados += 1
            continue

        try:
            log.info(f"[{i}/{total}] Descargando premio {id_premio}...")
            bytes_img = descargar_y_optimizar(url_rewardix)

            nombre_archivo = f"{id_premio}.jpg"
            ruta = os.path.join(CARPETA_IMAGENES, nombre_archivo)
            with open(ruta, "wb") as f_img:
                f_img.write(bytes_img)

            url_propia = f"{URL_BASE_PUBLICA}{CARPETA_IMAGENES}/{nombre_archivo}"

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

    guardar_mapeo_local(mapeo)

    log.info("=" * 60)
    log.info(f"Resumen de sincronización:")
    log.info(f"  Nuevos:       {nuevos}")
    log.info(f"  Actualizados: {actualizados}")
    log.info(f"  Saltados:     {saltados}")
    log.info(f"  Errores:      {errores}")
    log.info(f"  Total mapeo:  {len(mapeo)}")
    log.info("=" * 60)

    if (nuevos > 0 or actualizados > 0) and not args.no_push:
        git_commit_push()


if __name__ == "__main__":
    main()