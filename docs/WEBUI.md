# Cómo usar el panel

Este panel automatiza tu tienda **Goaffpro** y **SimplyCodes**: se afilia a cada tienda que coincide en ambos sitios, carga el cupón, y arma un CSV ordenado por comisión (mejores arriba).

**Antes de arrancar:** dejá Chrome abierto, con `simplycodes.com` logueado a mano. El programa toma el control de esa ventana mientras trabaja — no uses la compu hasta que termine.

## Launcher

### Cuenta Goaffpro
Email y contraseña de tu cuenta. Se usan para loguearse solo.

### Datos para los formularios de los merchants
Nombre, apellido, teléfono, país, provincia y ciudad — se completan solos en los formularios de afiliación. Si un portal pide un dato que no está acá, el programa frena y te lo pide por consola.

### Cobro de comisiones
Email de PayPal. Se carga como método de pago en cada portal de afiliado. Dejalo vacío si no querés tocar el método de pago.

### Corrida
- **Carpeta para los CSV**: dónde se guarda una copia del archivo al terminar.
- **Cantidad de tiendas a completar**: corta sola al llegar a ese número. Activá **Correr indefinido** para que siga hasta que la cortes vos.
- **Capturas manuales**: si lo activás, el programa frena en cada tienda para que saques vos la captura del dashboard y la subas vos en SimplyCodes (el bot no saca ni sube capturas).

Apretá **Iniciar** para arrancar. Se abre una consola nueva — necesaria porque el ESC y las pausas por captcha requieren foco de teclado ahí.

## Cortar una corrida

- **Ctrl+Alt+F12**: funciona con cualquier ventana activa.
- **ESC**: con la consola del programa enfocada.
- Botón **Detener** en el panel.

Corta al toque, sin esperar a que termine la tienda en curso, y exporta el CSV con lo juntado hasta ese momento.

## Historial

Cada corrida deja un CSV. Desde acá podés abrir uno, filtrar tiendas y descargarlo.

## Error logs

Errores de la última corrida (se reinicia cada vez que arrancás de nuevo). Si algo falla, copiá el texto de acá para revisarlo.

## Si el programa se frena solo

Si aparece un captcha, rate-limit o Cloudflare, el programa se detiene y avisa en consola. Resolvelo a mano en la ventana de Chrome y apretá Enter para seguir. Nunca intenta sortear el bloqueo solo.

## Tiendas que quedaron pendientes

Si un merchant todavía no generó el código de cupón, la tienda queda pendiente y no se reintenta sola en la misma corrida. Esperá unos minutos y arrancá una corrida nueva para reintentarla.
