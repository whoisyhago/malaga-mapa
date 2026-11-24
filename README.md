# Mapa de viviendas · Málaga (uso interno)

```
malaga-mapa/
├── index.html          ← el mapa (Leaflet + filtros). Lee data/listings.json
├── collector.py        ← recoge anuncios de las fuentes y escribe data/listings.json
├── config.json         ← qué fuentes están activas y sus credenciales
└── data/
    ├── listings.json   ← generado por collector.py
    └── import/         ← suelta aquí exportaciones .json/.csv (ej. fotocasa__nov.csv)
```

## Probar ya (datos ficticios)

```bash
pip install requests
python collector.py --demo        # crea 260 anuncios DEMO
python -m http.server 8000        # abre http://localhost:8000
python collector.py --demo        # vuelve a lanzarlo: entran/salen anuncios → pulsa ↻ y verás los NUEVOS
```

> Hay que abrirlo con un servidor (`http.server`) y no con doble clic: el navegador bloquea
> `fetch()` de ficheros locales con `file://`.

## Datos reales

El HTML no puede leer los portales directamente (CORS + protección antibots), así que
`collector.py` hace de intermediario. Cada fuente se activa en `config.json`:

| Fuente | Portal | Qué necesitas |
|---|---|---|
| `idealista_api` | Idealista | Credenciales de la API oficial (se solicitan a Idealista; la cuota gratuita es pequeña, por eso `max_pages: 2`). |
| `apify` | Fotocasa, Pisos.com (o Idealista) | Token de Apify + el actor que elijas, programado en Apify. El script solo descarga el último resultado. |
| `import_dir` | Cualquiera | Ficheros en `data/import/`. El prefijo antes de `__` dice el portal: `fotocasa__x.csv`, `pisoscom__x.json`, `idealista__x.json`. |

Las credenciales también pueden ir en variables de entorno: `IDEALISTA_API_KEY`, `IDEALISTA_API_SECRET`, `APIFY_TOKEN`.

El normalizador reconoce los nombres de campo habituales (`price`, `size`/`surface`, `rooms`/`bedrooms`,
`latitude`/`lat`…). Si un proveedor usa nombres raros, añádelos en `normalize()`.

**Ojo:** las condiciones de uso de Fotocasa y Pisos.com no permiten la extracción automatizada.
Para uso interno conviene revisarlo con legal, o usar un acuerdo/feed del portal o un proveedor de datos con licencia.

## Qué hace el recolector

- Guarda `first_seen` de cada anuncio → el mapa marca en rojo los **NUEVOS** desde tu última visita ("Marcar vistos" reinicia).
- Solo publica lo que está **activo** en esa ejecución: lo que desaparece de los portales sale del mapa.
- Agrupa el mismo piso publicado en varios portales (misma zona, precio y m²) → aparece una vez con "También en…".
- Si todas las fuentes fallan, conserva el fichero anterior.

## En la nube (GitHub Actions + GitHub Pages)

El workflow `.github/workflows/actualizar.yml` cada 30 min: descarga el `listings.json` publicado
(para saber qué es nuevo), ejecuta `collector.py` y publica `index.html` + datos en GitHub Pages.
Los datos **no** se guardan en el repo, así el historial no crece.

Configuración (una sola vez, en el repo de GitHub):

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Secrets and variables → Actions**
   - *Secrets*: `IDEALISTA_API_KEY`, `IDEALISTA_API_SECRET`, `APIFY_TOKEN` (las que tengas)
   - *Variables*: `DEMO_MODE` = `true` mientras no haya fuentes reales; bórrala o pon `false` después
3. **Actions → Actualizar mapa → Run workflow** para la primera publicación.

URL: `https://whoisyhago.github.io/malaga-mapa/`

⚠️ La web de GitHub Pages es **pública** (cualquiera con la URL), aunque el repo sea privado.
Nunca pongas claves en `config.json`: las fuentes se activan solas cuando existe su secret.

## Automatizar en local

```cron
*/30 * * * * cd /ruta/malaga-mapa && /usr/bin/python3 collector.py >> collector.log 2>&1
```

En Windows: Programador de tareas → `python collector.py` cada 30 min.
El HTML se recarga solo (selector "Auto-refresco") y te avisa de cuántos anuncios han entrado.
