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

Por default corre en tandas de 10 tiendas, en loop, hasta que apretás **ESC** (con la consola de `main.py` enfocada, no la de Chrome). Al cortar, exporta `export.csv`.

### Parámetros

| Flag | Qué hace | Default |
|---|---|---|
| `--batch-size N` | Tiendas a buscar/cruzar por tanda | 10 |
| `--max-batches N` | Corta sola después de N tandas | sin tope |
| `--stop-after N` | Corta sola al juntar N tiendas persistidas, sin esperar ESC | sin tope |
| `--csv-dir RUTA` | Carpeta donde dejar una copia fechada del CSV | la del launcher |
| `--retry-pending` | No descubre tiendas nuevas: solo reintenta las que quedaron a medias (`pending_verification`, `coupon_failed`, etc.) y sale | — |

Ejemplos:

```
python main.py --batch-size 20              # tandas de 20 en vez de 10
python main.py --stop-after 50               # corta sola al juntar 50 tiendas persistidas
python main.py --batch-size 10 --max-batches 3   # como mucho 3 tandas (30 tiendas revisadas)
python main.py --retry-pending               # solo reintenta pendientes, no busca tiendas nuevas
```

## Cortar

Apretá **ESC** con la ventana de la consola (`main.py`) enfocada. Corta al toque, sin esperar a terminar la tienda en curso, y exporta el CSV con lo que haya juntado hasta ese momento.

## Reintentar pendientes

Por cada tienda nueva, `main.py` corre el flujo completo (afiliación → código → cupón → método de pago) antes de pasar a la siguiente. Si el merchant todavía no generó el código de cupón, la tienda queda `pending_verification` y **no se reintenta sola** — hay que correr `python main.py --retry-pending` a mano (dale unos minutos antes, para que el merchant tenga tiempo de generarlo).

## Salida

- `export.csv`: todas las tiendas que matchearon Goaffpro+Simplycodes, con columnas fijas (ver `db.CSV_COLUMNS` o `docs/GOAL.md`), ordenadas por comisión de afiliado (mejores arriba). `POPULARITY` y `NEEDING_CODES` van vacías: no tienen fuente real en el sitio (ver `docs/GOAL.md`).
- `data.db` (SQLite): persistencia local, incluye tiendas rechazadas (para no re-chequearlas en la próxima tanda) y la página de Goaffpro donde quedó el descubrimiento.
- `screenshots/`: captura de 'My Stores' de Goaffpro con el código de cada tienda afiliada (prueba que se sube a SimplyCodes al cargar el cupón).

## Bloqueos

Si aparece un captcha, rate-limit, o Cloudflare, el script se detiene, avisa en consola, y espera que lo resuelvas a mano en la ventana de Chrome antes de continuar (Enter para seguir).

## Más detalle

`docs/GOAL.md` tiene el flujo completo, las decisiones técnicas tomadas, y las limitaciones conocidas.
