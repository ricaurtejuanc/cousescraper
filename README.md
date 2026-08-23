# golfcoursescraper

Scraper de datos de campos de golf españoles (Vc / Vs / Par / tarjeta hoyo a
hoyo) a partir de los portales oficiales de las federaciones autonómicas de
golf. Todo el código vive en `scrape_madrid_courses.py` (el nombre quedó del
alcance inicial, aunque hoy cubre tres federaciones).

## Requisitos

```bash
pip install requests beautifulsoup4
```

## Uso

```bash
python3 scrape_madrid_courses.py
```

Genera dos CSV en el directorio actual (ambos en UTF-8 con BOM, para que
Excel muestre bien tildes/eñes):

- **`spain_courses_vc_vs.csv`** — una fila por club + recorrido + tee +
  género, con Vc (course rating), Vs (slope) y Par total.
- **`spain_courses_tarjetas.csv`** — una fila por hoyo (1-18) con
  metros/par/hándicap del hoyo. Formato normalizado, pensado para cargar en
  base de datos y poder filtrar/pivotar por tee en una web. Se une con el
  CSV anterior por `(trazado_id, barra_id, genero)`.

Ambos CSV solo filtran los tees que necesitamos: **Blancas/Hombre**,
**Amarillas/Hombre**, **Rojas/Mujer** (ver `TARGET_TEES` en el script). El
color de tee (`tee`) y el género (`genero`) van en columnas separadas — no
mezclados como "AMARILLAS (M)" — para evitar la ambigüedad entre "M" de
Masculino y "M" de Mujer.

## Cobertura por federación

España tiene 17 federaciones autonómicas de golf. Se investigaron las 17;
solo 3 publican Vc/Vs de forma estructurada y aprovechable:

| Federación | Clubes | Vc/Vs | Tarjeta hoyo a hoyo | Plataforma |
|---|---|---|---|---|
| **Madrid** (fedgolfmadrid.com) | 29 | ✅ | ✅ | Symfony + AJAX (FOSJsRoutingBundle) |
| **Andalucía** (portal.golfandalucia.com) | 103 | ✅ | ✅ | Symfony + AJAX (misma plataforma que Madrid) |
| **Galicia** (fggolf.com) | 22 | ✅ | ❌ (solo Par total) | ASP.NET WebForms, HTML estático |

**Total actual: 154 clubes, ~607 filas en el CSV resumen, ~10.170 filas
hoyo a hoyo (solo Madrid + Andalucía).**

### Madrid y Andalucía — misma plataforma AJAX

El HTML estático de la ficha de club (`/club/{codigo}`) solo trae el
`<select>` de recorridos. Los valores de Vc/Vs/Par se cargan por AJAX
cuando el usuario elige recorrido ("trazado") y color de tee ("barra"). El
scraper replica esas llamadas directamente:

1. `GET {club_url}` → `<select id="trazados">` con los recorridos del club.
2. `POST {ajax_prefix}/barras-trazado?trazado={id}` → tees disponibles para
   ese recorrido.
3. `POST {ajax_prefix}/trazadobarra-valores?barra={id}&trazado={id}&hoyos=3`
   → `{"m": {"campo": Vc, "slope": Vs}, "f": {...}}`.
4. `POST {ajax_prefix}/datos-trazado?barra={id}&trazado={id}` → tarjeta
   hoyo a hoyo (metros/par/hcp × 18) por género. Par total = suma de los
   18 valores de "par".

Las rutas AJAX se confirmaron leyendo `/js/routing.js` (FOSJsRoutingBundle)
de cada sitio. Andalucía además expone el listado completo de clubes vía
AJAX (`{ajax_prefix}/clubes-provincias`), así que no está hardcodeado como
en Madrid (`MADRID_CLUBS`).

### Galicia — HTML estático, sin AJAX

Plataforma distinta (ASP.NET WebForms). El Vc/Vs/Par por tee+género ya
viene en el HTML de la ficha de club (`/campos/{slug}.aspx`), sin
necesidad de AJAX:

- `div.cajaBarras`: una `<li>` por tee, con color (vía estilo inline),
  slope, valor (Vc) y par — pero sin el nombre/género del tee.
- `select[id*=ddlBarras]` (del calculador de hándicap de la misma página):
  las mismas barras, en el mismo orden, con el nombre real ("Rojas Damas",
  "Barras amarillas caballeros", ...) del que se saca color + género.

El listado de los 22 clubes se obtiene del directorio `/campos.aspx`. A
diferencia de Madrid/Andalucía, Galicia **no expone tarjeta hoyo a hoyo**
(solo el par total agregado), así que no aporta filas a
`spain_courses_tarjetas.csv`. Algunos clubes repiten la misma barra con dos
ids distintos e idénticos valores (bug de datos del propio sitio); el
scraper deduplica por `(tee, género)` dentro de cada recorrido.

### Federaciones descartadas (14)

Investigadas una por una; ninguna publica Vc/Vs de forma estructurada:

- **WordPress, sin datos estructurados** (9): Baleares, Cantabria,
  Castilla-La Mancha, Castilla y León, Comunidad Valenciana, Extremadura,
  Murcia, Navarra, País Vasco.
- **Plataforma propia, pero solo texto libre o enlaces externos** (3):
  Aragón, Asturias, Canarias.
- **Cataluña** (catgolf.com): texto libre con par/metros, sin Vc/Vs.

También se evaluó el buscador nacional de clubes de la RFEG
(`rfegolf.es/clubpaginas/clubsection.aspx`, ASP.NET WebForms legacy con
selects en cascada Región→Provincia→Localidad→Club). Tras varios intentos
de reproducir su postback (AJAX parcial, postback completo, navegador
headless) no fue posible automatizarlo, y de todas formas la base de clubes
nacional de la RFEG solo expone Par/Metros/Hándicap por hoyo, **sin
Vc/Slope** — así que no se persiguió más.

## Estructura del código

- `TARGET_TEES`: qué combinaciones tee-color/género se capturan.
- `FEDERATIONS`: configuración de Madrid y Andalucía (misma plataforma
  AJAX, solo cambia el dominio/prefijo/id de select).
- `scrape_club()` / `get_trazados()` / `get_barras()` / `get_valores()` /
  `get_tarjeta()`: pipeline AJAX común a Madrid y Andalucía.
- `scrape_galicia_club()` / `get_galicia_club_list()`: pipeline HTML
  estático específico de Galicia.
- `fetch()`: GET con reintentos (fggolf.com resetea la conexión de forma
  intermitente).
- `main()`: orquesta las tres federaciones y escribe los dos CSV.

## Limitaciones conocidas

- El scraper imprime al final una lista de combinaciones tee/club no
  encontradas — normalmente son campos pequeños (pitch & putt, campos de
  9 hoyos) que no tienen ese color de tee configurado, no un error.
- Los CSV generados (`*.csv`) no se versionan en git (ver `.gitignore`);
  hay que correr el script para regenerarlos.
