"""
Sincroniza imágenes de Rewardix al repositorio local
y las publica en GitHub Pages.

Flujo:
1. Asegura las entradas manuales COTIZA (14 y 16) en el mapeo.
2. Lee el CSV de Rewardix (id_premio, url_imagen).
3. Para cada premio, descarga y optimiza la imagen.
4. La guarda en ./premios/{id}.jpg
5. Actualiza el mapeo en ./mapeo_imagenes.csv
6. Hace commit y push a GitHub Pages.

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

ARCHIVO_REWARDIX = "rewardix_export.csv"
ARCHIVO_MAPEO = "mapeo_imagenes.csv"
CARPETA_IMAGENES = "premios"

GITHUB_USER = "EliasCEMEX"
NOMBRE_REPO = "cxal-rewards-images"
URL_BASE_PUBLICA = f"https://{GITHUB_USER}.github.io/{NOMBRE_REPO}/"

# Nombres de columnas esperadas en el CSV del proveedor
COL_ID = "id_premio"
COL_URL = "url_imagen"

# Optimización de imágenes
OPTIMIZAR = True
MAX_ANCHO = 600
CALIDAD_JPEG = 80
PAUSA_ENTRE_DESCARGAS = 0.5  # segundos entre descargas

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

# LOGGING
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("sync")

# UTILIDADES
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
        subprocess.run(["git", "commit", "-m", f"Sync imagenes {fecha}"], check=True)
        subprocess.run(["git", "push"], check=True)
        log.info("Cambios subidos a GitHub Pages")
    except subprocess.CalledProcessError as e:
        log.error(f"Error en git: {e}")


# MAIN
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

    # Asegurar entradas COTIZA en el mapeo
    cotiza_modificado = False

    for cotiza_id, info in COTIZA_MANUAL.items():
        ruta_local = os.path.join(CARPETA_IMAGENES, info["nombre_archivo"])
        url_propia = f"{URL_BASE_PUBLICA}{CARPETA_IMAGENES}/{info['nombre_archivo']}"

        if not os.path.exists(ruta_local):
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

    # Sincronizar imágenes desde Rewardix
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

        # Proteger IDs COTIZA contra cualquier sobreescritura accidental
        if id_premio in COTIZA_MANUAL:
            log.warning(
                f"[{i}/{total}] Saltando ID {id_premio}: reservado para imagen manual COTIZA"
            )
            saltados += 1
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

    # Guardar mapeo y resumen
    guardar_mapeo_local(mapeo)

    log.info("=" * 60)
    log.info("Resumen de sincronización:")
    log.info(f"  Nuevos:           {nuevos}")
    log.info(f"  Actualizados:     {actualizados}")
    log.info(f"  Saltados:         {saltados}")
    log.info(f"  Errores:          {errores}")
    log.info(f"  COTIZA modif.:    {cotiza_modificado}")
    log.info(f"  Total mapeo:      {len(mapeo)}")
    log.info(f"  COTIZA fijos:     {len(COTIZA_MANUAL)}")
    log.info("=" * 60)

    # Push si hubo cambios reales (incluye primera inicialización de COTIZA)
    if (nuevos > 0 or actualizados > 0 or cotiza_modificado) and not args.no_push:
        git_commit_push()


if __name__ == "__main__":
    main()
