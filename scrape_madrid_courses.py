"""
Scraper de Vc (Course Rating) / Vs (Slope) para los campos de golf de Madrid
afiliados a la Federación de Golf de Madrid (fedgolfmadrid.com).

Filtra solo: BLANCAS (M), AMARILLAS (M), ROJAS (F) — los tees que necesita
AfterGolf de momento.

ANTES DE CONFIAR EN EL OUTPUT:
1. Corre este script contra 2-3 clubs primero (ej. CM01, CM02, CM41).
2. Abre esas mismas páginas en el navegador, inspecciona el HTML real
   (DevTools > Elements) y confirma que cada tabla <table> que el script
   emparejó con una etiqueta "Recorrido:" es REALMENTE la tabla de ese
   recorrido y no la del recorrido anterior/siguiente.
3. Si el emparejamiento falla, el problema está en la función scrape_club()
   — probablemente hay que anclarse a un contenedor padre común en vez de
   emparejar por índice de lista (zip).

Requisitos: pip install requests beautifulsoup4
"""

import requests
from bs4 import BeautifulSoup
import csv
import re
import time

# Los 29 campos de Madrid con instalación jugable (fuente: fedgolfmadrid.com/club/lista)
CLUBS = [
    ("CM01", "Real Club Puerta de Hierro"),
    ("CM02", "Real Club de Campo Villa de Madrid"),
    ("CM03", "C.d.s.c.e.a. Barberán y Collar"),
    ("CM04", "RACE"),
    ("CM05", "Real Club de Golf La Herrería"),
    ("CM06", "Real Club de Golf Las Rozas de Madrid"),
    ("CM07", "Real Club de Golf Lomas-Bosque"),
    ("CM08", "El Robledal Golf"),
    ("CM09", "Club de Golf Encinas de Boadilla"),
    ("CM11", "Real Sociedad Hípica Española Club de Campo"),
    ("CM12", "Campo de Golf B.A. de Torrejón"),
    ("CM14", "Green Paddock S.A."),
    ("CM18", "Golf Park Entertainment"),
    ("CM22", "Forus Golf Las Rejas"),
    ("CM33", "Golf Negralejo"),
    ("CM41", "Escuela RFGM / Centro de Tecnificación"),
    ("CM52", "Real Club La Moraleja"),
    ("CM60", "Golf Los Retamares"),
    ("CM61", "Club de Golf La Dehesa"),
    ("CM66", "Club de Golf Aranjuez"),
    ("CM74", "Club de Golf de Pozuelo"),
    ("CM77", "Asociación de Golf Villa El Escorial"),
    ("CM81", "Club de Golf Olivar de La Hinojosa"),
    ("CM87", "Centro Deportivo Militar La Dehesa"),
    ("CMA5", "Golf Santander S.A."),
    ("CMA8", "Centro Nacional de Golf"),
    ("CMC8", "El Encín Golf"),
    ("CMD9", "Club de Golf Mistral Samaranch"),
    ("CME9", "LaFinca Golf"),
]

# Solo estos tees nos interesan de momento
TARGET_TEES = ["BLANCAS (M)", "AMARILLAS (M)", "ROJAS (F)"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfterGolfDataBot/1.0)"}


def scrape_club(code, name):
    url = f"https://fedgolfmadrid.com/club/{code}"
    results = []
    try:
        r = requests.get(url, timeout=15, headers=HEADERS)
        r.raise_for_status()
    except Exception as e:
        print(f"  ERROR fetching {name} ({code}): {e}")
        return results

    soup = BeautifulSoup(r.text, "html.parser")

    # Etiquetas "Recorrido: ..." y las tablas de tarjeta en orden de aparición
    recorrido_labels = soup.find_all(string=re.compile(r"Recorrido:"))
    tables = soup.find_all("table")

    if len(recorrido_labels) != len(tables):
        print(f"  ⚠️  {name} ({code}): {len(recorrido_labels)} recorridos vs "
              f"{len(tables)} tablas — desajuste, revisar manualmente")

    for label, table in zip(recorrido_labels, tables):
        label_text = " ".join(label.split())  # normaliza espacios/saltos

        matched_tee = next(
            (tee for tee in TARGET_TEES if tee.upper() in label_text.upper()),
            None
        )
        if not matched_tee:
            continue

        vc, vs = None, None
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) >= 2:
                if cells[0] == "Vc":
                    vc = cells[-1]
                elif cells[0] == "Vs":
                    vs = cells[-1]

        results.append({
            "club_code": code,
            "club_name": name,
            "recorrido": label_text,
            "tee": matched_tee,
            "vc": vc,
            "vs": vs,
        })

    return results


def main():
    all_results = []
    missing = []

    for code, name in CLUBS:
        print(f"Scraping {name} ({code})...")
        rows = scrape_club(code, name)
        found_tees = {r["tee"] for r in rows}
        for tee in TARGET_TEES:
            if tee not in found_tees:
                missing.append(f"{name} ({code}) — falta {tee}")
        all_results.extend(rows)
        time.sleep(1)  # no martillear el servidor de la federación

    with open("madrid_courses_vc_vs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["club_code", "club_name", "recorrido", "tee", "vc", "vs"]
        )
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\n✅ {len(all_results)} filas guardadas en madrid_courses_vc_vs.csv")

    if missing:
        print(f"\n⚠️  {len(missing)} combinaciones tee/club no encontradas "
              f"(puede ser que el club no tenga ese color de tee, o que "
              f"el nombre de recorrido no coincida exactamente):")
        for m in missing:
            print(f"   - {m}")


if __name__ == "__main__":
    main()
