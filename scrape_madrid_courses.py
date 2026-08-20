"""
Scraper de Vc (Course Rating) / Vs (Slope) / Par para los campos de golf de
España, federación por federación (portales autonómicos de golf).

Filtra solo: BLANCAS/Hombre, AMARILLAS/Hombre, ROJAS/Mujer — los tees que
necesita AfterGolf de momento. El color de tee y el género van en columnas
separadas (tee, genero) en vez de mezclarlos en una sola etiqueta.

De las 17 federaciones autonómicas investigadas, solo Madrid y Andalucía
corren sobre la misma plataforma (un backend Symfony con FOSJsRoutingBundle
que expone rutas AJAX idénticas, solo con distinto prefijo). El resto usa
plataformas propias (WordPress, sitios a medida) que no publican Vc/Vs de
forma estructurada — Cataluña (catgolf.com), por ejemplo, solo tiene texto
libre con par/metros, sin Vc/Vs en ningún lado del HTML.

Los valores de Vc/Vs/Par NO están en el HTML estático de la ficha de club:
la página los carga por AJAX (jQuery) una vez que el usuario elige un
"trazado" (recorrido) y una "barra" (color de tee) en los <select> del
formulario "Trazados". Este script replica esas mismas llamadas AJAX
directamente para cada federación soportada:

  1. GET {club_url}                -> <select id="trazados"|"trazados_aux">
                                       con los recorridos del club (id +
                                       nombre).
  2. POST {ajax_prefix}/barras-trazado?trazado={id}
                                    -> lista de barras (tees) disponibles
                                       para ese recorrido: [{id, nombre,
                                       color}, ...].
  3. POST {ajax_prefix}/trazadobarra-valores?barra={id}&trazado={id}&hoyos=3
                                    -> {"m": {"campo": Vc, "slope": Vs},
                                        "f": {"campo": Vc, "slope": Vs}}
                                       (campo = Vc, slope = Vs; hoyos=3
                                       pide el recorrido completo 1-18).
  4. POST {ajax_prefix}/datos-trazado?barra={id}&trazado={id}
                                    -> {"m": {"par": [18 valores], ...},
                                        "f": {"par": [18 valores], ...}}
                                       Par total = suma de los 18 valores
                                       numéricos de ese género.

Andalucía además expone un listado completo de clubes vía AJAX
({ajax_prefix}/clubes-provincias, sin parámetros), así que su lista de
clubes se obtiene en tiempo de ejecución en vez de estar hardcodeada.

Las rutas AJAX se confirmaron leyendo /js/routing.js (FOSJsRoutingBundle)
de cada sitio.

Requisitos: pip install requests beautifulsoup4
"""

import requests
from bs4 import BeautifulSoup
import csv
import time

# Los 29 campos de Madrid con instalación jugable (fuente: fedgolfmadrid.com/club/lista)
MADRID_CLUBS = [
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

# (color de tee, género "H"/"M", fragmento a buscar en el nombre de la barra,
#  clave de género en el JSON de la API)
TARGET_TEES = [
    ("BLANCAS", "H", "BLANCA", "m"),
    ("AMARILLAS", "H", "AMARILLA", "m"),
    ("ROJAS", "M", "ROJA", "f"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfterGolfDataBot/1.0)"}
AJAX_HEADERS = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}

# Federaciones confirmadas sobre la misma plataforma AJAX. Cada entrada:
#  - base_url: dominio del portal
#  - ajax_prefix: prefijo de las rutas AJAX (varía por federación)
#  - club_url: plantilla de la ficha de club (varía el idioma/ruta)
#  - trazado_select_id: id del <select> con los recorridos (varía el markup)
#  - clubs: lista estática [(code, name), ...], o None para obtenerla por AJAX
FEDERATIONS = [
    {
        "key": "madrid",
        "name": "Madrid",
        "base_url": "https://fedgolfmadrid.com",
        "ajax_prefix": "/ajax",
        "club_url": "/club/{code}",
        "trazado_select_id": "trazados",
        "clubs": MADRID_CLUBS,
    },
    {
        "key": "andalucia",
        "name": "Andalucía",
        "base_url": "https://portal.golfandalucia.com",
        "ajax_prefix": "/nodo/ajax",
        "club_url": "/es/club/{code}",
        "trazado_select_id": "trazados_aux",
        "clubs": None,
    },
]


def get_club_list(session, fed):
    """Devuelve [(code, name), ...] para una federación (estática o vía AJAX)."""
    if fed["clubs"] is not None:
        return fed["clubs"]

    r = session.post(
        f"{fed['base_url']}{fed['ajax_prefix']}/clubes-provincias",
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    con_campo = r.json().get("con", {})
    return sorted(
        ((code, info["nombre"]) for code, info in con_campo.items()),
        key=lambda x: x[0],
    )


def get_trazados(session, fed, code):
    """Devuelve [(trazado_id, nombre_recorrido), ...] para un club."""
    url = fed["base_url"] + fed["club_url"].format(code=code)
    r = session.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    select = soup.find("select", id=fed["trazado_select_id"])
    if not select:
        return []
    return [
        (opt.get("value"), opt.get_text(strip=True))
        for opt in select.find_all("option")
        if opt.get("value")
    ]


def get_barras(session, fed, trazado_id):
    """Devuelve [{"id", "nombre", "color"}, ...] disponibles para un recorrido."""
    r = session.post(
        f"{fed['base_url']}{fed['ajax_prefix']}/barras-trazado",
        params={"trazado": trazado_id},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_valores(session, fed, trazado_id, barra_id):
    """Devuelve {"m": {"campo": Vc, "slope": Vs}, "f": {...}} para trazado+barra."""
    r = session.post(
        f"{fed['base_url']}{fed['ajax_prefix']}/trazadobarra-valores",
        params={"barra": barra_id, "trazado": trazado_id, "hoyos": 3},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_par(session, fed, trazado_id, barra_id, gender_key):
    """Suma el par de los 18 hoyos para trazado+barra+género (None si no hay datos)."""
    r = session.post(
        f"{fed['base_url']}{fed['ajax_prefix']}/datos-trazado",
        params={"barra": barra_id, "trazado": trazado_id},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    pares = r.json().get(gender_key, {}).get("par", [])
    numericos = [p for p in pares if isinstance(p, (int, float))]
    return sum(numericos) if numericos else None


def scrape_club(session, fed, code, name):
    results = []
    try:
        trazados = get_trazados(session, fed, code)
    except Exception as e:
        print(f"  ERROR fetching {name} ({code}): {e}")
        return results

    for trazado_id, recorrido in trazados:
        try:
            barras = get_barras(session, fed, trazado_id)
        except Exception as e:
            print(f"  ERROR barras {name} ({code}) / {recorrido}: {e}")
            continue

        for tee_color, genero, needle, gender_key in TARGET_TEES:
            barra = next(
                (b for b in barras if needle in b.get("nombre", "").upper()),
                None,
            )
            if not barra:
                continue

            try:
                valores = get_valores(session, fed, trazado_id, barra["id"])
            except Exception as e:
                print(f"  ERROR valores {name} ({code}) / {recorrido} / {tee_color} {genero}: {e}")
                continue

            datos = valores.get(gender_key, {})
            vc = datos.get("campo")
            vs = datos.get("slope")
            if not vc and not vs:
                # el club no tiene datos cargados para esta combinación
                continue

            try:
                par = get_par(session, fed, trazado_id, barra["id"], gender_key)
            except Exception as e:
                print(f"  ERROR par {name} ({code}) / {recorrido} / {tee_color} {genero}: {e}")
                par = None

            results.append({
                "federacion": fed["name"],
                "club_code": code,
                "club_name": name,
                "recorrido": recorrido,
                "tee": tee_color,
                "genero": genero,
                "vc": vc,
                "vs": vs,
                "par": par,
            })
            time.sleep(0.2)  # no martillear el servidor de la federación

    return results


def main():
    all_results = []
    missing = []
    session = requests.Session()

    for fed in FEDERATIONS:
        print(f"=== Federación: {fed['name']} ===")
        clubs = get_club_list(session, fed)
        print(f"  {len(clubs)} clubes encontrados")

        for code, name in clubs:
            print(f"Scraping {name} ({code})...")
            rows = scrape_club(session, fed, code, name)
            found = {(r["tee"], r["genero"]) for r in rows}
            for tee_color, genero, _, _ in TARGET_TEES:
                if (tee_color, genero) not in found:
                    missing.append(f"{fed['name']} — {name} ({code}) — falta {tee_color} ({genero})")
            all_results.extend(rows)
            time.sleep(1)  # no martillear el servidor de la federación

    with open("spain_courses_vc_vs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["federacion", "club_code", "club_name", "recorrido", "tee", "genero", "vc", "vs", "par"],
        )
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\n✅ {len(all_results)} filas guardadas en spain_courses_vc_vs.csv")

    if missing:
        print(f"\n⚠️  {len(missing)} combinaciones tee/club no encontradas "
              f"(puede ser que el club no tenga ese color de tee, o que "
              f"el nombre de recorrido no coincida exactamente):")
        for m in missing:
            print(f"   - {m}")


if __name__ == "__main__":
    main()
