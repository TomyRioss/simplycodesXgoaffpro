# simplycodesXgoaffpro

Automatiza afiliación (Goaffpro) + carga de cupones (Simplycodes) para toda tienda que matchee en ambos sitios, en tandas de a 10 hasta que el usuario corta con ESC. Badge Gold + coin rate High de SimplyCodes no filtran, solo ordenan el CSV (mejores arriba). Exporta CSV al cortar. Ver `docs/GOAL.md` para el flujo completo y decisiones tomadas.

## Stack

- Python
- Windows UI Automation (`pywinauto`) sobre la ventana de Chrome real ya abierta con sesión — sin CDP, sin reiniciar Chrome, sin herramientas de Claude (el cliente final no las tiene)
- SQLite local (`data.db`) para persistencia — sin DB externa
- DeepSeek como recurso opcional (ver `docs/models.md`)

## Reglas del proyecto

- Antes de agregar cualquier lib/herramienta nueva: buscar si ya existe algo simple que lo resuelva (foros, librerías conocidas). Evitar reinventar la rueda / overengineering.
- Ante rate-limiting, captcha o bloqueo externo: el flujo se detiene, avisa al usuario y espera intervención manual. Nunca intentar sortear captchas.
- Credenciales (Goaffpro, DeepSeek) van en `.env`, nunca hardcodeadas ni commiteadas.
- `docs/models.md` documenta uso de IA — mantenerlo en `.gitignore`.
- Datos de perfil fijos: Nombre Tomas, Apellido Rios, Teléfono 1134083120, Email tomyrios2006@gmail.com. Si un formulario pide un campo no cubierto por este perfil, el flujo debe parar y preguntar — no inventar datos.
