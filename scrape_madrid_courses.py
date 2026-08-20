"""
Scraper de Vc (Course Rating) / Vs (Slope) para los campos de golf de Madrid
afiliados a la Federación de Golf de Madrid (fedgolfmadrid.com).

Filtra solo: BLANCAS (M), AMARILLAS (M), ROJAS (F) — los tees que necesita
AfterGolf de momento.

Los valores de Vc/Vs NO están en el HTML estático de /club/{codigo}: la
página los carga por AJAX (jQuery) una vez que el usuario elige un
"trazado" (recorrido) y una "barra" (color de tee) en los <select> del
formulario "Trazados". Este script replica esas mismas llamadas AJAX
directamente:

  1. GET /club/{codigo}            -> <select id="trazados"> con los
                                       recorridos del club (id + nombre).
  2. POST /ajax/barras-trazado?trazado={id}
                                    -> lista de barras (tees) disponibles
                                       para ese recorrido: [{id, nombre,
                                       color}, ...].
  3. POST /ajax/trazadobarra-valores?barra={id}&trazado={id}&hoyos=3
                                    -> {"m": {"campo": Vc, "slope": Vs},
                                        "f": {"campo": Vc, "slope": Vs}}
                                       (campo = Vc, slope = Vs; hoyos=3
                                       pide el recorrido completo 1-18).

Las rutas AJAX se confirmaron leyendo /js/routing.js (FOSJsRoutingBundle)
y /js/frontend/trazados_club.js.

Requisitos: pip install requests beautifulsoup4
"""

import requests
from bs4 import BeautifulSoup
import csv
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

# tee_label -> (fragmento a buscar en el nombre de la barra, clave de género en el JSON)
TARGET_TEES = {
    "BLANCAS (M)": ("BLANCA", "m"),
    "AMARILLAS (M)": ("AMARILLA", "m"),
    "ROJAS (F)": ("ROJA", "f"),
}

BASE_URL = "https://fedgolfmadrid.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfterGolfDataBot/1.0)"}
AJAX_HEADERS = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}


def get_trazados(session, code):
    """Devuelve [(trazado_id, nombre_recorrido), ...] para un club."""
    r = session.get(f"{BASE_URL}/club/{code}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    select = soup.find("select", id="trazados")
    if not select:
        return []
    return [
        (opt.get("value"), opt.get_text(strip=True))
        for opt in select.find_all("option")
        if opt.get("value")
    ]


def get_barras(session, trazado_id):
    """Devuelve [{"id", "nombre", "color"}, ...] disponibles para un recorrido."""
    r = session.post(
        f"{BASE_URL}/ajax/barras-trazado",
        params={"trazado": trazado_id},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_valores(session, trazado_id, barra_id):
    """Devuelve {"m": {"campo": Vc, "slope": Vs}, "f": {...}} para trazado+barra."""
    r = session.post(
        f"{BASE_URL}/ajax/trazadobarra-valores",
        params={"barra": barra_id, "trazado": trazado_id, "hoyos": 3},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def scrape_club(session, code, name):
    results = []
    try:
        trazados = get_trazados(session, code)
    except Exception as e:
        print(f"  ERROR fetching {name} ({code}): {e}")
        return results

    for trazado_id, recorrido in trazados:
        try:
            barras = get_barras(session, trazado_id)
        except Exception as e:
            print(f"  ERROR barras {name} ({code}) / {recorrido}: {e}")
            continue

        for tee_label, (needle, gender) in TARGET_TEES.items():
            barra = next(
                (b for b in barras if needle in b.get("nombre", "").upper()),
                None,
            )
            if not barra:
                continue

            try:
                valores = get_valores(session, trazado_id, barra["id"])
            except Exception as e:
                print(f"  ERROR valores {name} ({code}) / {recorrido} / {tee_label}: {e}")
                continue

            datos = valores.get(gender, {})
            vc = datos.get("campo")
            vs = datos.get("slope")
            if not vc and not vs:
                # el club no tiene datos cargados para esta combinación
                continue

            results.append({
                "club_code": code,
                "club_name": name,
                "recorrido": recorrido,
                "tee": tee_label,
                "vc": vc,
                "vs": vs,
            })
            time.sleep(0.2)  # no martillear el servidor de la federación

    return results


def main():
    all_results = []
    missing = []
    session = requests.Session()

    for code, name in CLUBS:
        print(f"Scraping {name} ({code})...")
        rows = scrape_club(session, code, name)
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
