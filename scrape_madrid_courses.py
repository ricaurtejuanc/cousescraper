"""
Scraper de Vc (Course Rating) / Vs (Slope) / Par para los campos de golf de
España, federación por federación (portales autonómicos de golf).

Filtra solo: BLANCAS/Hombre, AMARILLAS/Hombre, ROJAS/Mujer — los tees que
necesita AfterGolf de momento. El color de tee y el género van en columnas
separadas (tee, genero) en vez de mezclarlos en una sola etiqueta.

De las 17 federaciones autonómicas investigadas, tres publican Vc/Vs de
forma estructurada:
  - Madrid y Andalucía corren sobre la misma plataforma (un backend Symfony
    con FOSJsRoutingBundle que expone rutas AJAX idénticas, solo con
    distinto prefijo) — ver detalle de las rutas más abajo.
  - Galicia (fggolf.com) usa una plataforma propia (ASP.NET WebForms) que
    publica Vc/Vs/Par por tee+género directamente en el HTML estático de la
    ficha de club, sin AJAX — ver scrape_galicia_club().
El resto usa plataformas propias (mayormente WordPress) que no publican
Vc/Vs de forma estructurada — Cataluña (catgolf.com), Aragón, Asturias y
Canarias, por ejemplo, solo tienen texto libre con par/metros o enlazan a
la web de cada club, sin Vc/Vs en ningún lado del HTML.

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
                                    -> {"m": {"metros": [18], "par": [18],
                                              "hcp": [18]},
                                        "f": {...}}
                                       Tarjeta hoyo a hoyo (1-18) de ese
                                       género: metros, par y hándicap del
                                       hoyo. Par total = suma de "par".

Salidas:
  - spain_courses_vc_vs.csv     -> una fila por club+recorrido+tee+género,
                                    con Vc/Vs/Par total.
  - spain_courses_tarjetas.csv  -> una fila por hoyo (formato normalizado,
                                    listo para cargar en base de datos y
                                    filtrar/pivotar por tee en la web).
                                    Se une con el CSV anterior por
                                    (trazado_id, barra_id, genero).

Andalucía además expone un listado completo de clubes vía AJAX
({ajax_prefix}/clubes-provincias, sin parámetros), así que su lista de
clubes se obtiene en tiempo de ejecución en vez de estar hardcodeada.

Las rutas AJAX se confirmaron leyendo /js/routing.js (FOSJsRoutingBundle)
de cada sitio.

Requisitos: pip install requests beautifulsoup4
"""

import re
import requests
from bs4 import BeautifulSoup
import csv
import time


def fetch(session, url, retries=3, **kwargs):
    """GET con reintentos: fggolf.com resetea la conexión de forma intermitente."""
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=15, **kwargs)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2)

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

# --- Galicia (fggolf.com) ---------------------------------------------------
# Plataforma distinta (ASP.NET WebForms): a diferencia de Madrid/Andalucía,
# el Vc/Vs/Par por tee+género viene ya en el HTML estático de la ficha de
# club, sin AJAX. Cada club puede tener varios recorridos; cada recorrido
# tiene un bloque <h3 class="nombreCampo"> seguido de un <div class="cajaCampo">
# que contiene:
#   - div.cajaBarras: una <li> por barra (tee), con el color (estilo inline),
#     slope, valor (Vc) y par — pero SIN el nombre/género de la barra.
#   - select[id*=ddlBarras] (del calculador de hándicap): las mismas barras,
#     en el mismo orden, mostrando el nombre real ("Rojas Damas", "Barras
#     amarillas caballeros", ...) del que sacamos color+género.
# No expone tarjeta hoyo a hoyo (solo el par total), así que Galicia no
# aporta filas a spain_courses_tarjetas.csv.
GALICIA_BASE_URL = "https://www.fggolf.com"


def get_galicia_club_list(session):
    """Devuelve [(slug, name), ...] leyendo el directorio /campos.aspx."""
    r = fetch(session, f"{GALICIA_BASE_URL}/campos.aspx", headers=HEADERS)
    soup = BeautifulSoup(r.text, "html.parser")
    clubs = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not (href.startswith("campos/") and href.endswith(".aspx")):
            continue
        slug = href[len("campos/"):-len(".aspx")]
        text = a.get_text(strip=True)
        if text and "ver información" not in text.lower():
            clubs.setdefault(slug, text)
    return sorted(clubs.items())


def _parse_num_coma(text):
    """'68,6' -> 68.6 ; '0' -> 0.0 ; '-'/'' -> None."""
    text = (text or "").strip().replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def scrape_galicia_club(session, slug, name):
    results = []
    tarjetas = []  # Galicia no expone tarjeta hoyo a hoyo
    try:
        r = fetch(session, f"{GALICIA_BASE_URL}/campos/{slug}.aspx", headers=HEADERS)
    except Exception as e:
        print(f"  ERROR fetching {name} ({slug}): {e}")
        return results, tarjetas

    soup = BeautifulSoup(r.text, "html.parser")
    for idx, h3 in enumerate(soup.find_all("h3", class_="nombreCampo"), start=1):
        recorrido = h3.get_text(strip=True) or f"Recorrido {idx}"
        caja_campo = h3.find_next_sibling("div", class_="cajaCampo")
        if not caja_campo:
            continue

        caja_barras = caja_campo.find("div", class_="cajaBarras")
        select = caja_campo.find("select", id=re.compile("ddlBarras"))
        if not caja_barras or not select:
            continue

        items = caja_barras.find_all("li")
        options = select.find_all("option")
        if len(items) != len(options):
            print(f"  ⚠️  {name} ({slug}) / {recorrido}: {len(items)} barras vs "
                  f"{len(options)} opciones — desajuste, se omite")
            continue

        vistos = set()  # algunos clubes repiten la misma barra con 2 ids distintos
        for li, opt in zip(items, options):
            slope = _parse_num_coma(li.find("span", class_="slope").get_text())
            valor = _parse_num_coma(li.find("span", class_="valor").get_text())
            par = _parse_num_coma(li.find("span", class_="par").get_text())
            barra_id = opt.get("value")
            nombre_barra = opt.get_text(strip=True).upper()

            if not slope or not valor or not par:
                continue  # barra vacía/no configurada en el club

            if "CABALLERO" in nombre_barra:
                genero = "H"
            elif "DAMA" in nombre_barra:
                genero = "M"
            else:
                continue  # no se puede determinar el género con confianza

            tee_color = next(
                (color for color, g, needle, _ in TARGET_TEES
                 if g == genero and needle in nombre_barra),
                None,
            )
            if not tee_color:
                continue  # combinación color/género que no nos interesa
            if (tee_color, genero) in vistos:
                continue  # barra duplicada en la web de origen
            vistos.add((tee_color, genero))

            results.append({
                "federacion": "Galicia",
                "club_code": slug,
                "club_name": name,
                "trazado_id": f"{slug}-{idx}",
                "recorrido": recorrido,
                "barra_id": barra_id,
                "tee": tee_color,
                "genero": genero,
                "vc": valor,
                "vs": int(slope),
                "par": int(par),
            })

        time.sleep(0.2)  # no martillear el servidor de la federación

    return results, tarjetas


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


def get_tarjeta(session, fed, trazado_id, barra_id, gender_key):
    """Devuelve la tarjeta hoyo a hoyo para trazado+barra+género:
    [{"hoyo": 1..18, "metros": int, "par": int, "hcp": int}, ...] (lista
    vacía si el club no tiene datos cargados para esa combinación)."""
    r = session.post(
        f"{fed['base_url']}{fed['ajax_prefix']}/datos-trazado",
        params={"barra": barra_id, "trazado": trazado_id},
        headers=AJAX_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    datos = r.json().get(gender_key, {})
    metros = datos.get("metros", [])
    pares = datos.get("par", [])
    hcps = datos.get("hcp", [])
    hoyos = []
    for i, (m, p, h) in enumerate(zip(metros, pares, hcps), start=1):
        if not isinstance(p, (int, float)):
            continue  # "-": el club no cargó datos para este hoyo/género
        hoyos.append({
            "hoyo": i,
            "metros": m if isinstance(m, (int, float)) else None,
            "par": p,
            "hcp": h if isinstance(h, (int, float)) else None,
        })
    return hoyos


def scrape_club(session, fed, code, name):
    results = []
    tarjetas = []
    try:
        trazados = get_trazados(session, fed, code)
    except Exception as e:
        print(f"  ERROR fetching {name} ({code}): {e}")
        return results, tarjetas

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
                hoyos = get_tarjeta(session, fed, trazado_id, barra["id"], gender_key)
            except Exception as e:
                print(f"  ERROR tarjeta {name} ({code}) / {recorrido} / {tee_color} {genero}: {e}")
                hoyos = []
            par = sum(h["par"] for h in hoyos) if hoyos else None

            results.append({
                "federacion": fed["name"],
                "club_code": code,
                "club_name": name,
                "trazado_id": trazado_id,
                "recorrido": recorrido,
                "barra_id": barra["id"],
                "tee": tee_color,
                "genero": genero,
                "vc": vc,
                "vs": vs,
                "par": par,
            })

            for h in hoyos:
                tarjetas.append({
                    "federacion": fed["name"],
                    "club_code": code,
                    "club_name": name,
                    "trazado_id": trazado_id,
                    "recorrido": recorrido,
                    "barra_id": barra["id"],
                    "tee": tee_color,
                    "genero": genero,
                    "hoyo": h["hoyo"],
                    "metros": h["metros"],
                    "par": h["par"],
                    "hcp": h["hcp"],
                })

            time.sleep(0.2)  # no martillear el servidor de la federación

    return results, tarjetas


def main():
    all_results = []
    all_tarjetas = []
    missing = []
    session = requests.Session()

    for fed in FEDERATIONS:
        print(f"=== Federación: {fed['name']} ===")
        clubs = get_club_list(session, fed)
        print(f"  {len(clubs)} clubes encontrados")

        for code, name in clubs:
            print(f"Scraping {name} ({code})...")
            rows, tarjetas = scrape_club(session, fed, code, name)
            found = {(r["tee"], r["genero"]) for r in rows}
            for tee_color, genero, _, _ in TARGET_TEES:
                if (tee_color, genero) not in found:
                    missing.append(f"{fed['name']} — {name} ({code}) — falta {tee_color} ({genero})")
            all_results.extend(rows)
            all_tarjetas.extend(tarjetas)
            time.sleep(1)  # no martillear el servidor de la federación

    print("=== Federación: Galicia ===")
    galicia_clubs = get_galicia_club_list(session)
    print(f"  {len(galicia_clubs)} clubes encontrados")
    for slug, name in galicia_clubs:
        print(f"Scraping {name} ({slug})...")
        rows, tarjetas = scrape_galicia_club(session, slug, name)
        found = {(r["tee"], r["genero"]) for r in rows}
        for tee_color, genero, _, _ in TARGET_TEES:
            if (tee_color, genero) not in found:
                missing.append(f"Galicia — {name} ({slug}) — falta {tee_color} ({genero})")
        all_results.extend(rows)
        all_tarjetas.extend(tarjetas)
        time.sleep(1)  # no martillear el servidor de la federación

    # utf-8-sig añade el BOM que Excel necesita para detectar UTF-8 y no
    # mostrar mal las tildes/eñes (si no, las interpreta como Windows-1252).
    with open("spain_courses_vc_vs.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "federacion", "club_code", "club_name", "trazado_id", "recorrido",
                "barra_id", "tee", "genero", "vc", "vs", "par",
            ],
        )
        writer.writeheader()
        writer.writerows(all_results)

    # Tabla normalizada (una fila por hoyo) para cargar en base de datos y
    # poder filtrar/pivotar por tee en la web. trazado_id + barra_id + genero
    # son la clave para unirla con spain_courses_vc_vs.csv.
    with open("spain_courses_tarjetas.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "federacion", "club_code", "club_name", "trazado_id", "recorrido",
                "barra_id", "tee", "genero", "hoyo", "metros", "par", "hcp",
            ],
        )
        writer.writeheader()
        writer.writerows(all_tarjetas)

    print(f"\n✅ {len(all_results)} filas guardadas en spain_courses_vc_vs.csv")
    print(f"✅ {len(all_tarjetas)} filas (hoyo a hoyo) guardadas en spain_courses_tarjetas.csv")

    if missing:
        print(f"\n⚠️  {len(missing)} combinaciones tee/club no encontradas "
              f"(puede ser que el club no tenga ese color de tee, o que "
              f"el nombre de recorrido no coincida exactamente):")
        for m in missing:
            print(f"   - {m}")


if __name__ == "__main__":
    main()
