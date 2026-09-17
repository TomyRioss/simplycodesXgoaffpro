# simplycodesXgoaffpro

Cruza tiendas de [Goaffpro](https://goaffpro.com) (afiliados con acceso instantáneo) con [SimplyCodes](https://simplycodes.com), se afilia a cada match, carga el cupón generado, y exporta todo a CSV (ordenado por comisión de afiliado, mejores arriba; nada de esto filtra).

Controla la ventana de Chrome que **ya tenés abierta** con tu sesión — no usa CDP, no la cierra ni abre una nueva. Ver `docs/GOAL.md` para el detalle del flujo y las decisiones tomadas.

## Setup

1. `pip install -r requirements.txt`
2. Tené Chrome abierto normal, con `simplycodes.com` logueado a mano (Goaffpro se loguea solo, con credenciales de `.env`: `GOAFFPRO_EMAIL`, `GOAFFPRO_PASSWORD`).
3. No hace falta ningún flag de Chrome ni reiniciarlo.

## Correr

### Con el launcher web (recomendado)

```
python webui.py
```

Abre `http://localhost:8765`: ahí se cargan las credenciales de Goaffpro, los
datos del perfil (nombre, apellido, teléfono, país, provincia, ciudad), el
email de PayPal para cobrar las comisiones, la cantidad de tiendas a
completar (o "Correr indefinido"), la carpeta donde dejar los CSV y el modo
de **capturas manuales**. Todo queda en `config.json`.

"Iniciar" lanza `main.py` en una consola nueva — el ESC y las pausas por
captcha necesitan una consola con foco de teclado, cosa que la página no puede
dar. Mientras corre, no se puede usar Chrome ni la computadora: el programa
maneja la ventana. Al terminar, "Descargar CSV" baja el `export.csv`.

### Directo por consola

```
python main.py
```

Por default corre sin tope, recorriendo el catálogo de Goaffpro en loop, hasta que apretás **ESC** (con la consola de `main.py` enfocada, no la de Chrome), Ctrl+Alt+F12, o el botón Detener del launcher. Al cortar, exporta `export.csv`.

### Parámetros

| Flag | Qué hace | Default |
|---|---|---|
| `--stop-after N` | Corta sola al juntar N tiendas completadas (cupón subido), sin esperar ESC | sin tope |
| `--csv-dir RUTA` | Carpeta donde dejar una copia fechada del CSV | la del launcher |
| `--retry-pending` | No descubre tiendas nuevas: solo reintenta las que quedaron a medias (`pending_verification`, `coupon_failed`, etc.) y sale | — |

Ejemplos:

```
python main.py --stop-after 50               # corta sola al juntar 50 tiendas completadas
python main.py --retry-pending               # solo reintenta pendientes, no busca tiendas nuevas
```

## Cortar

Apretá **ESC** con la ventana de la consola (`main.py`) enfocada. Corta al toque, sin esperar a terminar la tienda en curso, y exporta el CSV con lo que haya juntado hasta ese momento.

## Reintentar pendientes

Por cada tienda nueva, `main.py` corre el flujo completo (afiliación → código → cupón → método de pago) antes de pasar a la siguiente. Las que quedan a medias (`pending_verification`, `coupon_failed`, `enrolled` sin código, etc.) **se reintentan solas al arrancar cada tanda del loop** — por eso, si un merchant todavía no generó el código, alcanza con correr `python main.py` de nuevo y la tienda se retoma (dale unos minutos antes, para que el merchant tenga tiempo de generarlo). Con `python main.py --retry-pending` se reintentan SOLO esas y sale, sin descubrir tiendas nuevas.

## Salida

- `export.csv`: todas las tiendas que matchearon Goaffpro+Simplycodes, con columnas fijas (ver `db.CSV_COLUMNS` o `docs/GOAL.md`), ordenadas por comisión de afiliado (mejores arriba). `POPULARITY` y `NEEDING_CODES` van vacías: no tienen fuente real en el sitio (ver `docs/GOAL.md`).
- `data.db` (SQLite): persistencia local, incluye tiendas rechazadas (para no re-chequearlas en el próximo rescaneo) y la página de Goaffpro donde quedó el descubrimiento.
- `screenshots/`: captura de 'My Stores' de Goaffpro con el código de cada tienda afiliada (prueba que se sube a SimplyCodes al cargar el cupón).

## Bloqueos

Si aparece un captcha, rate-limit, o Cloudflare, el script se detiene, avisa en consola, y espera que lo resuelvas a mano en la ventana de Chrome antes de continuar (Enter para seguir).

## Más detalle

`docs/GOAL.md` tiene el flujo completo, las decisiones técnicas tomadas, y las limitaciones conocidas.
