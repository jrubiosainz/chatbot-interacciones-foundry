# Guion de la demo

Unos 10 minutos para enseñar el prototipo en una reunión: qué hacer, qué decir y qué hacer si algo falla.

**La idea en una frase:** cinco ciudadanos hablan con un asistente por texto o por voz, y el tablero de la derecha muestra, con la telemetría de Microsoft Foundry, cuánto usa cada uno la IA: mensajes, modelos, tokens y búsquedas.

## Chuleta

| Minuto | Qué haces | Qué cuentas |
|---|---|---|
| 0–1 | Enseñas la pantalla entera | Cinco usuarios a la izquierda y su consumo a la derecha |
| 1–2 | Recorres la conversación de Carmen | Cada respuesta lleva su ficha: modelo, tokens y búsquedas |
| 2–3 | Ana pregunta por texto | El agente responde con la base de conocimiento (AI Search) |
| 3–4 | Diego pregunta algo de actualidad | Si no está en la base, busca en internet (Bing) |
| 4–5 | Luis cambia al modelo ligero | Cada usuario puede ir con un modelo distinto |
| 5–6 | Carmen pregunta por voz | La voz usa dos modelos más y se cuenta aparte |
| 6–8 | Recorres el tablero y la consulta KQL | Todo sale de Application Insights, sin contadores a mano |
| 8–10 | Explicas cómo se adapta | Identificador propio, fórmula de facturación y repo público |

## Antes de empezar

Cinco minutos antes de la reunión:

1. Abre la app. La URL se obtiene con `azd env get-value SERVICE_WEB_URI`.
2. Espera a que la cabecera diga **«Foundry listo · proj-chatbot-interacciones»**. Si el contenedor acaba de arrancar, tarda uno o dos minutos.
3. Comprueba que el tablero tiene datos con **«Últimos 30 días»**, el rango por defecto. Si está vacío, [siembra las conversaciones](../README.md#sembrar-las-conversaciones-de-demo).
4. Pulsa **🎙️** una vez para dar permiso al micrófono y cancela con **Esc**. No se envía nada.
5. Deja el audio como arranca, en **«🔇 Audio silenciado»**. Así no suena nada aunque haya respuestas de voz.
6. Si al compartir pantalla no caben el chat y el tablero a la vez, baja el zoom del navegador.

> [!TIP]
> Para que el público oiga una respuesta hablada en Teams, comparte la pantalla con **«Incluir sonido del ordenador»**, activa el audio y pulsa **«▶ Escuchar»** en ese mensaje. Si no, basta con enseñar la etiqueta `gpt-audio-mini`.

## Paso a paso

### 1. La foto general (1 min)

- **Haz:** enseña la pantalla entera sin tocar nada.
- **Di:** «A la izquierda, un asistente de atención ciudadana con cinco usuarios ficticios, cada uno en su pestaña y con una conversación de 15 a 20 mensajes. A la derecha, lo que Foundry ha registrado de cada uno: cuántos mensajes, si fueron por texto o por voz, con qué modelo, cuántos tokens y cuántas búsquedas.»

### 2. Una conversación por dentro (1 min)

- **Haz:** abre la pestaña **Carmen**, que usa mucho la voz, y sube un poco por el historial.
- **Di:** «Cada respuesta lleva su ficha: si llegó por texto o por voz, qué modelo contestó, los tokens de entrada y de salida, si consultó la base de conocimiento o internet, y cuánto tardó. Los tokens de entrada crecen con la conversación, porque el modelo recibe todo el historial en cada turno. El mismo mensaje cuesta más al final de una conversación larga.»

### 3. Pregunta por texto con AI Search (1 min)

- **Haz:** en la pestaña **Ana**, escribe `¿Abren los puntos limpios los domingos?` y pulsa **Enviar**.
- **Verás:** en 5–15 segundos, «domingos y festivos de 9:00 a 14:00» con la etiqueta **🔎 AI Search**.
- **Di:** «El agente busca en la base de conocimiento del ayuntamiento, un índice de Azure AI Search con fichas de trámites. En el tablero, Ana suma un mensaje y una consulta a AI Search.»
- Si su fila aún no ha subido, verás **«+1 en ingesta»**: Application Insights tarda de 1 a 3 minutos. Sigue y vuelve después.

### 4. Pregunta de actualidad con Bing (1 min)

- **Haz:** en la pestaña **Diego**, pregunta `¿Va a llover mañana en Madrid?`
- **Verás:** la respuesta con la etiqueta **🌐 Bing (Web Search)**.
- **Di:** «Esto no está en la base del ayuntamiento, así que el agente busca en internet con la herramienta Web Search de Foundry. También se cuenta por usuario.»

### 5. Cambio de modelo (1 min)

- **Haz:** en la pestaña **Luis**, elige **«gpt-5.4-mini (ligero)»** en el selector de modelo y pregunta `¿Qué necesito para pedir una licencia de terraza?`
- **Di:** «Cada usuario puede ir con un modelo distinto. El ligero es más barato y basta para trámites sencillos. En la matriz "Llamadas por usuario y modelo" se ve qué modelo usa cada uno.»

### 6. Pregunta por voz (1–2 min)

- **Haz:** en la pestaña **Carmen**, pulsa **🎙️**, di en voz alta «¿Cómo pido la teleasistencia?» y pulsa **⏹** para enviar. **Esc** cancela, y el máximo son 60 segundos.
- **Verás:** «Transcribiendo audio…», tu frase como mensaje de voz y la respuesta con la etiqueta `gpt-audio-mini`. No suena nada, porque el audio sigue silenciado.
- **Di:** «La voz usa dos modelos más: `gpt-4o-mini-transcribe` pasa el audio a texto y `gpt-audio-mini` genera la respuesta hablada. El tablero lo cuenta como mensaje por voz y separa las llamadas de voz a texto y de texto a voz.»

### 7. El tablero (2 min)

- **Haz:** pulsa **↻** y recorre el tablero de arriba abajo:
  - las tarjetas de mensajes, mensajes por voz, llamadas a modelos, tokens, AI Search y Bing;
  - **«Interacciones por usuario»**, con una fila por usuario y sus modelos;
  - **«Llamadas por usuario y modelo»**, el cruce de usuario y modelo;
  - el rango **«Última hora»**, para ver solo lo que se ha hecho en la demo.
- **Di:** «Un mensaje no es una llamada. Un mensaje de texto es una llamada al modelo, y uno de voz son al menos tres: transcribir, responder y leer la respuesta. A eso se suman las búsquedas. Por eso hay más llamadas que mensajes, y conviene decidir qué se factura.»
- **Haz:** despliega **«Consulta KQL utilizada»**, al final del tablero.
- **Di:** «Esta es la consulta exacta a Application Insights. No hay contadores a mano: se puede pegar en el portal, en un workbook o en Power BI.»

### 8. Cómo se adapta (2 min)

- **Di**, en tres ideas:
  1. **Vuestro identificador.** Cada llamada lleva atributos de OpenTelemetry: usuario (`app.user.id`), canal (`app.modality`), modelo (`gen_ai.request.model`), tokens y conversación (`gen_ai.conversation.id`). Un identificador de sesión propio se añade como un atributo más junto a `app.user.id`, en `src/app/telemetry.py`, y la consulta agrupa por él.
  2. **Vuestra fórmula.** Si se factura por petición, con reglas distintas para voz y texto, la fórmula va en la consulta y sale como una columna más del tablero. Hay un ejemplo más abajo.
  3. **Es un repo público.** `azd up` lo despliega en vuestra suscripción y el script de siembra carga las conversaciones de ejemplo.
- Si preguntan por euros o por límites: el coste real por recurso y por despliegue de modelo está en **Cost Management**, con presupuestos y alertas. Los límites de tokens por consumidor se ponen en el **AI Gateway** de Foundry o en **API Management**. Este tablero añade lo que esas herramientas no ven: **quién** hizo cada llamada.

## Ejemplo: una fórmula de facturación en KQL

Pega esta consulta en **Logs** de Application Insights. Cuenta, por usuario, los mensajes de texto y de voz, las llamadas a modelos y los tokens. Después aplica una fórmula de ejemplo: cada mensaje de voz vale 2 peticiones y se cobra lo mayor entre las peticiones y los bloques de 10.000 tokens.

```kusto
dependencies
| where timestamp > ago(30d)
| where cloud_RoleName == "chatbot-interacciones"
| extend spanKind = tostring(customDimensions["app.kind"]),
         userId   = tostring(customDimensions["app.user.id"]),
         modality = tostring(customDimensions["app.modality"]),
         status   = tostring(customDimensions["app.turn.status"]),
         tokens   = coalesce(toint(customDimensions["gen_ai.usage.input_tokens"]), 0)
                  + coalesce(toint(customDimensions["gen_ai.usage.output_tokens"]), 0)
| where isnotempty(userId)
| summarize mensajesTexto  = countif(spanKind == "turn" and status == "ok" and modality == "texto"),
            mensajesVoz    = countif(spanKind == "turn" and status == "ok" and modality == "voz"),
            llamadasModelo = countif(spanKind == "model_call"),
            tokens         = sumif(tokens, spanKind == "model_call")
            by userId
// Fórmula de ejemplo: un mensaje de voz cuenta como 2 peticiones y se cobra
// lo mayor entre las peticiones y los bloques de 10.000 tokens.
| extend unidadesFacturables = max_of(mensajesTexto + 2 * mensajesVoz, tokens / 10000)
| order by unidadesFacturables desc
```

La consulta cuenta todo lo registrado en el periodo y no tiene en cuenta el botón «Reiniciar», así que puede dar más mensajes que el tablero. Para llevar la fórmula al tablero, calcula la columna en `src/app/dashboard.py`, que agrega por usuario el resultado de la consulta, y píntala en `src/app/static/app.js`.

## Si algo falla

| Qué pasa | Qué hacer |
|---|---|
| Error 429 (límite de tokens por minuto) | Cada turno de una conversación larga envía 25.000–30.000 tokens. Espera 30 segundos y repite, o cambia al modelo ligero. |
| El tablero no sube | Es normal: Application Insights tarda 1–3 minutos. Verás «⏳ … todavía en ingesta» y «+N en ingesta». Sigue con la demo y pulsa **↻** después. |
| No busca en internet | Pídeselo: «búscalo en internet». |
| El micrófono no graba | Da permiso de micrófono a la página desde la barra de direcciones, o haz la pregunta por texto. Carmen ya tiene mensajes de voz que enseñar. |
| No se oye la voz | Es intencionado: el audio arranca silenciado. Mira el aviso de [Antes de empezar](#antes-de-empezar). |
| La cabecera no dice «Foundry listo» | El servidor está preparando el índice y los agentes tras arrancar. Espera uno o dos minutos y recarga. |

## Después de la demo

- **«Reiniciar»**, en la cabecera de cada usuario, vacía su conversación y pone su fila a cero. La telemetría anterior se conserva en Application Insights.
- Para volver a cargar la conversación de ejemplo de un usuario:

  ```bash
  python3 scripts/seed_conversations.py --url "$(azd env get-value SERVICE_WEB_URI)" --users ana --reset
  ```

- La app no tiene autenticación y cada mensaje consume tokens. No compartas la URL en abierto y bórralo todo con `azd down --purge` cuando ya no lo necesites. Más detalles en [Costes y limpieza](../README.md#costes-y-limpieza).
