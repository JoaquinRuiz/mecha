# Mecha — spec para Claude Code

Nov 4, 2026 · @Joaquín

> ¿Cuánta mecha le queda a tus API keys?

## Visión y nombre

Mecha es un panel local, web y CLI, que dice de un vistazo cuánta vida le queda a cada credencial de IA: si funciona, si tiene saldo, cuándo caduca y de qué tipo es. Cubre Anthropic, OpenAI, Google Gemini y xAI, y suma OpenRouter como quinto proveedor en la fase 4 porque es el que más información expone.

El nombre juega con los dos sentidos de la palabra: la mecha que se consume, como el tiempo hasta caducar o el saldo que se quema, y el robot gigante del anime. En PyPI `mecha` ya está ocupado, así que el paquete se publica como `mecha-keys` con dos comandos, `mecha` y `mecha-keys`; el segundo permite lanzar `uvx mecha-keys` sin `--from`.

### Principios

1. **Local-first.** Corre en 127.0.0.1. Una clave solo sale de la máquina hacia el endpoint oficial de su propio proveedor. Sin telemetría ni nube.
2. **Coste cero por defecto.** Las comprobaciones automáticas usan solo endpoints gratuitos. Las sondas que consumen tokens son opt-in por credencial y muestran su coste estimado.
3. **Honestidad con lo desconocido.** «Desconocido» es un estado válido. Cada dato muestra su fuente (API, cabecera, JWT, fichero, manual o inferido) y nunca se inventa un saldo.
4. **OAuth solo en local.** Los tokens OAuth de suscripción (Claude, ChatGPT, Gemini CLI) nunca se usan para llamar a una API: se inspeccionan en local. [Anthropic no permite](https://code.claude.com/docs/es/legal-and-compliance) que terceros enruten peticiones con credenciales de planes Free, Pro o Max.
5. **Un secreto guardado no vuelve a verse.** El frontend solo recibe la forma redactada y una huella.
6. **Proveedores como plugins.** Añadir uno es un módulo con sus fixtures y sus tests, sin tocar el núcleo.

## Alcance

La v1 responde una sola pregunta: qué credencial está rota o va a romperse, y por qué. Lo que no ayude a contestarla queda fuera.

**Objetivos**

- Alta pegando el secreto: Mecha detecta proveedor y tipo sin usar la red.
- Dashboard con semáforo por credencial y contadores globales.
- Comprobación manual, periódica y en bloque, con historial por credencial.
- Enriquecimiento con claves de administración o gestión: estado, caducidad y último uso de las claves normales de la misma organización.
- CLI con salida en tabla y JSON, y códigos de salida útiles para cron y CI.
- Importar desde `.env` y vincular los ficheros de sesión de Codex CLI, Claude Code y Gemini CLI.

**No-objetivos**

- No es un proxy ni enruta peticiones de otras apps.
- No es analítica de costes: solo muestra el gasto que devuelvan las APIs de administración.
- No es un validador masivo: está pensado para decenas de credenciales propias, sin carga de listas ni rotación de IPs.
- Sin multiusuario ni sincronización en la nube.
- No refresca ni reescribe tokens OAuth de otras herramientas.

**Casos de uso**

1. Una app falla en producción y quieres saber en diez segundos qué clave se ha quedado sin saldo.
2. Antes de una clase o una demo, compruebas que todas las claves de prueba responden.
3. Tras activar la caducidad obligatoria de claves en OpenAI o Anthropic, ves cuáles caducan este mes.
4. Detectas claves de Gemini antiguas (`AIza…`) que Google está dejando de aceptar.

## Modelo de estados

Cada comprobación produce cuatro dimensiones independientes; el semáforo de la tarjeta es la peor de ellas según la prioridad rojo > ámbar > gris > verde, y «desconocido» nunca cuenta como fallo.

| Dimensión | Valores | De dónde sale |
| --- | --- | --- |
| Validez | `valid`, `invalid`, `revoked`, `blocked`, `not_applicable`, `unknown` | Código HTTP del endpoint gratuito, flags de bloqueo, estado en la API de administración; `not_applicable` para credenciales OAuth |
| Saldo | `ok`, `low`, `empty`, `capped`, `not_applicable`, `unknown` | Saldo por API (xAI, OpenRouter), errores de cuota, saldo estimado |
| Caducidad | `none`, `scheduled`, `soon`, `expired`, `unknown` | `expires_at` de las APIs de administración, claim `exp`, ficheros vinculados, fecha manual |
| Límites | `ok`, `throttled`, `unknown` | Cabeceras de rate limit y respuestas 429 |

El tipo de credencial (API key, admin, gestión, OAuth) no es un estado: es un atributo que decide qué dimensiones se pueden rellenar.

**Reglas del semáforo**

| Color | Condición |
| --- | --- |
| Rojo | Validez `invalid`, `revoked` o `blocked`; saldo `empty`; caducidad `expired` |
| Ámbar | Caduca en menos de 14 días; saldo `low` (umbral por defecto 5 USD); saldo `capped`; `throttled` en la última comprobación; tipo de clave en retirada, como Gemini `AIza` |
| Gris | No se pudo comprobar (red, timeout, 5xx) o validez `unknown` |
| Verde | Validez `valid` y ninguna condición ámbar, roja o gris |
| Solo local (`local`) | Credencial OAuth (validez `not_applicable`) sin ninguna condición ámbar o roja; estado neutro que no cuenta como gris |

- **Prioridad:** rojo > ámbar > gris > verde. `local` solo se aplica si no hay rojo ni ámbar, y nunca se mezcla con gris porque una credencial OAuth no hace comprobaciones remotas.
- **Condiciones offline siempre evaluadas:** las que no dependen de la red (tipo de clave en retirada, caducidad con fecha conocida por API previa, JWT, fichero o fecha manual) se evalúan en cada comprobación aunque la remota falle. Una `AIza` con timeout queda en ámbar, no en gris; una clave con `expires_at` pasado queda en rojo aunque haya error de red.
- **OAuth:** las credenciales `oauth_token` y `oauth_file` tienen validez `not_applicable` y overall `local`; las reglas de caducidad les aplican igual (caducada es rojo, menos de 14 días es ámbar, con la excepción de los access tokens con refresh token descrita en «Ficheros de sesión vinculados»).
- **Último estado conocido:** una tarjeta gris muestra también el último resultado no gris de la credencial y cuándo se obtuvo («última vez verde hace 2 días»).

Los umbrales se configuran globalmente y por credencial. Cada valor viaja como señal con `source` (`api`, `header`, `jwt`, `file`, `manual`, `inferred`) y `observed_at`, y la UI muestra la fuente junto al dato.

## Detección offline del tipo de credencial

Al pegar un secreto, Mecha lo clasifica sin red por prefijo, forma JWT o estructura JSON, y esa clasificación decide qué comprobaciones son posibles. Los patrones viven en un fichero de datos (`detect_patterns.toml`), así un formato nuevo se añade sin tocar código. Cada patrón lleva `verified` (prefijo confirmado en la documentación oficial) y su referencia; el estado de cada uno está en `docs/providers.md`.

| Patrón | Proveedor | Tipo | Qué implica |
| --- | --- | --- | --- |
| `sk-ant-api03-` | Anthropic | API key | Validación gratuita; caducidad solo con admin key |
| `sk-ant-admin01-` | Anthropic | Admin key | Lista las claves de la organización con estado y `expires_at` |
| `sk-ant-oat01-` | Anthropic | OAuth (suscripción Claude) | Solo inspección local; caducidad manual |
| `sk-ant-ort01-` | Anthropic | OAuth refresh (verificar prefijo) | No se guarda; se avisa de que es un refresh token |
| `sk-proj-` | OpenAI | Project key | Validación gratuita; caducidad solo con admin key |
| `sk-svcacct-` | OpenAI | Service account key | Igual que una project key |
| `sk-admin-` | OpenAI | Admin key | Lista project keys con `expires_at` y `last_used_at`; gasto vía Costs API |
| `sk-` (resto) | OpenAI (probable) | Clave de usuario antigua | Ambiguo, otros proveedores usan `sk-`: se pide confirmar |
| `AQ.` | Google Gemini | Auth key | Formato actual de AI Studio |
| `AIza` + 35 caracteres | Google Gemini | Standard key | Ámbar fijo: Google la está retirando de la Gemini API |
| `xai-` | xAI | API key | `GET /v1/api-key` devuelve flags de bloqueo gratis |
| `sk-or-v1-` | OpenRouter | API key | Saldo y caducidad con la propia clave (fase 4) |
| JWT (`eyJ…`, tres segmentos) | Según el claim `iss` | Token OAuth | Se decodifica sin verificar firma: `exp`, email, plan |

Las claves de gestión de xAI no tienen un prefijo propio: el ejemplo oficial de su validación muestra `xai-...r42q`, igual que una API key. La detección no puede distinguirlas, así que el alta permite marcarlas (`--kind management_key`) y se validan con `GET /auth/management-keys/validation`.

**Tokens JWT**

- `iss` decide el proveedor: `https://auth.openai.com` es ChatGPT/Codex y `https://accounts.google.com` es Google.
- Los tokens de ChatGPT llevan bajo el claim `https://api.openai.com/auth` campos como `chatgpt_plan_type`, `chatgpt_subscription_active_until` y `organizations`. OpenAI no los documenta: el parser tolera ausencias.
- `chatgpt_subscription_active_until` alimenta la dimensión de caducidad («suscripción activa hasta»).
- Todo dato salido de un JWT se etiqueta en la UI como «sin verificar».

**Ficheros de sesión vinculados (opt-in)**

| Herramienta | Ruta por defecto | Qué se lee |
| --- | --- | --- |
| Codex CLI | `~/.codex/auth.json` (o `$CODEX_HOME`) | `tokens.id_token` (plan y fin de suscripción), `last_refresh` |
| Claude Code | `~/.claude/.credentials.json` (Linux y Windows; en macOS está en el Llavero, fuera de la v1) | `claudeAiOauth.expiresAt`, `subscriptionType`, `rateLimitTier`, `scopes` |
| Gemini CLI | `~/.gemini/oauth_creds.json` | `expiry_date`, `scope`, email del `id_token` |

Se vinculan por ruta, en solo lectura, y se releen en cada comprobación; ningún token se copia a la base de datos. El access token de estos ficheros caduca en horas por diseño y la propia CLI lo renueva, así que, si hay refresh token, su caducidad es informativa y no pone la tarjeta en rojo. Ninguno de estos formatos está documentado oficialmente: cada parser tolera campos ausentes y marca sus datos como `file` sin verificar.

## Capacidades por proveedor

Solo xAI (con clave de gestión) y OpenRouter devuelven saldo por API; Anthropic y OpenAI exponen la caducidad solo con clave de administración, y Gemini no expone ni saldo ni caducidad, solo errores. Esta matriz es el contrato: `GET /api/providers` se genera de ella y la UI no promete nada que no esté aquí.

| Proveedor | Validar gratis | Saldo | Caducidad | Sonda activa (opt-in) |
| --- | --- | --- | --- | --- |
| Anthropic | `GET /v1/models` | Sin endpoint; el error aparece al generar | `expires_at` con admin key | `POST /v1/messages`, `max_tokens: 1` |
| OpenAI | `GET /v1/models` | Sin endpoint; gasto con admin key (Costs API) | `expires_at` con admin key | `POST /v1/embeddings`, una palabra |
| Google Gemini | `GET /v1beta/models` | Sin endpoint; el prepago agotado devuelve 402 | No expuesta | `generateContent`, `maxOutputTokens: 1` |
| xAI | `GET /v1/api-key` | Saldo prepago con clave de gestión | `expireTime` con clave de gestión | `POST /v1/chat/completions` mínima |
| OpenRouter (fase 4) | `GET /api/v1/key` | `limit_remaining` con la propia clave | `expires_at` con la propia clave | No hace falta |

### Anthropic

- Base `https://api.anthropic.com`, autenticación con `x-api-key` (la API también acepta `Authorization: Bearer`). La cabecera `anthropic-version` va en configuración: por defecto `2026-10-21`, publicada el 21 de octubre de 2026; `2023-06-01` sigue funcionando. La versión nueva rechaza `temperature`, `top_p` y `top_k`, que la sonda no envía.
- **Pasiva:** `GET /v1/models` (admin keys: `GET /v1/organizations/api_keys?limit=1`). 200 es válida (guardar nº de modelos y la cabecera `anthropic-organization-id` si viene), 401 inválida, 402 `billing_error` es saldo `empty` con «problema de facturación», 403 ámbar «sin permiso». No informa de saldo.
- **Activa:** `POST /v1/messages` con el modelo más barato que liste `/v1/models` (configurable) y `max_tokens: 1`. Un 400 cuyo mensaje contenga «credit balance» o «insufficient credits» es saldo `empty`. Leer `anthropic-ratelimit-requests-*`, `anthropic-ratelimit-input-tokens-*` y `anthropic-ratelimit-output-tokens-*` (limit, remaining, reset). Verificar si `POST /v1/messages/count_tokens`, que es gratuito, devuelve el mismo error de saldo; si lo hace, pasa a ser la sonda por defecto.
- **Admin key:** `GET /v1/organizations/api_keys`, paginado con `after_id` y `limit` hasta 1000. Cada clave trae `status` (`active`, `inactive`, `archived`, `expired`), `expires_at`, `partial_key_hint`, `scope` (con `2026-10-21` sustituye a `workspace_id`) y `created_by`. Mecha cruza `partial_key_hint` con las claves guardadas para rellenar estado y caducidad: genera la pista en el formato de Anthropic (`sk-ant-api03-R2D...igAA`, tres puntos ASCII) a partir del secreto descifrado en memoria y la compara con la de la API; nunca compara con su propio `redacted`.
- **OAuth `sk-ant-oat01-`:** sin llamadas remotas (principio 4).
- **Saldo:** no hay endpoint; se admite un presupuesto manual.

### OpenAI

- Base `https://api.openai.com/v1`, autenticación con `Authorization: Bearer`.
- **Pasiva:** `GET /v1/models` (admin keys: `GET /v1/organization/admin_api_keys?limit=1`). 200 es válida, 401 (`invalid_api_key`) inválida; una clave caducada no tiene código propio documentado (es un 401). Un 401 con «Missing scopes» es una clave restringida sin `api.model.read`: ámbar «sin permiso», no inválida. Este endpoint no comprueba la cuota, así que no detecta falta de saldo.
- **Activa:** `POST /v1/embeddings` con el modelo de embeddings más barato disponible y una sola palabra. 429 `credit_balance_exhausted` (variante: `type` `insufficient_quota`) es saldo `empty`; 429 `organization_spend_limit_exceeded`, `project_spend_limit_exceeded` u `organization_usage_limit_exceeded` es `capped`; 429 `rate_limit_exceeded` o `slow_down` es `throttled`; un 403 por permisos del proyecto es «sonda no permitida» y no invalida la clave. Leer `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens` y `x-ratelimit-reset-*`.
- **Caducidad:** desde el 10 de septiembre de 2026 las project keys pueden caducar y la organización puede imponer una vida máxima. Solo es visible con admin key.
- **Admin key (`sk-admin-`):** `GET /v1/organization/projects` y, por proyecto, `GET /v1/organization/projects/{project_id}/api_keys`, que devuelve `expires_at` (null si no caduca), `last_used_at`, `redacted_value`, `owner` y `owner_project_access`. `GET /v1/organization/admin_api_keys` da lo mismo para las admin keys. Mecha cruza `redacted_value` con las claves guardadas generando, a partir del secreto descifrado, la pista en el formato de OpenAI (la doc muestra `sk-abc...def`); nunca compara con su propio `redacted`.
- **Saldo:** no hay endpoint; el soporte de OpenAI lo confirmó en septiembre de 2026 y remite a la Costs API. Con admin key y un presupuesto manual (importe y fecha de compra), Mecha estima el saldo restando `GET /v1/organization/costs` desde esa fecha, y lo marca como `inferred`.
- **OAuth de ChatGPT (Codex):** solo JWT local.

### Google Gemini

- Base `https://generativelanguage.googleapis.com/v1beta`, con la clave siempre en la cabecera `x-goog-api-key` y nunca en la URL, para que no acabe en logs.
- **Tipos de clave:** desde el 28 de mayo de 2026 AI Studio solo crea auth keys (`AQ.`), ligadas a una cuenta de servicio. Google bloquea las standard keys (`AIza`) sin restringir y está retirando el resto; fuentes de terceros sitúan el corte total en septiembre de 2026. Toda `AIza` lleva aviso ámbar aunque responda 200.
- **Pasiva:** `GET /v1beta/models?pageSize=1000`. 200 es válida; 401 inválida (oficial); 400 `API_KEY_INVALID` inválida (lo que devuelve en vivo una `AIza` inválida); 402 es prepago agotado; 403 `PERMISSION_DENIED` es ámbar «sin permiso» (API no habilitada o proyecto restringido); el mensaje «reported as leaked» es `blocked` por filtración. Cualquier `AQ.` inválida recibe 401 `ACCESS_TOKEN_TYPE_UNSUPPORTED` (comprobado en vivo), igual que las reales afectadas por el problema de cuenta conocido desde julio de 2026: se muestra como inválida con esa pista y el enlace, sin afirmar el diagnóstico.
- **Activa:** `POST /v1beta/models/{modelo}:generateContent` con el modelo más barato disponible (configurable) y `maxOutputTokens: 1`. 402 Payment Required (oficial; variante: 429 «prepayment credits are depleted») es saldo `empty`; el tope de gasto mensual no tiene error documentado y queda sin mapeo (`docs/providers.md`); 429 con detalles de cuota (`QuotaFailure`) es `throttled`, y si el `quotaId` contiene `FreeTier` se marca plan gratuito como `inferred`. Verificar si `countTokens`, que es gratuito, refleja el agotamiento del prepago.
- **Saldo:** el prepago existe desde abril de 2026, pero sin API de lectura: `unknown` salvo que un error diga otra cosa.
- **Caducidad:** no expuesta; fecha manual opcional.

### xAI

- Base `https://api.x.ai/v1`, autenticación con `Authorization: Bearer`.
- **Pasiva:** `GET /v1/api-key` describe la propia clave: `api_key_blocked`, `api_key_disabled`, `team_blocked`, `acls`, `name`, `create_time`, `team_id`, `api_key_id` y `redacted_api_key`. Cualquier flag a `true` es `blocked`; `team_blocked` se muestra como «equipo bloqueado», porque la API no dice el motivo. Una clave desconocida recibe 400 «Incorrect API key provided» (comprobado en vivo; la documentación dice 401) y se trata como inválida.
- **Activa (opcional):** `POST /v1/chat/completions` mínima. Con el prepago agotado y el límite de facturación mensual a 0 USD, xAI rechaza las peticiones; el texto exacto del error está por verificar y se mapea a `empty`.
- **Clave de gestión:** base `https://management-api.x.ai`, autenticación con `Authorization: Bearer`. `GET /auth/management-keys/validation` la valida. `GET /auth/teams/{teamId}/api-keys` lista las claves con `expireTime`, `disabled`, `aclStrings` y `createTime`; un `expireTime` pasado significa clave rechazada. Bajo `/v1/billing/teams/{team_id}/` están `prepaid/balance` (`total.val`, céntimos de USD como string), `postpaid/spending-limits` y `postpaid/invoice/preview` (`prepaidCredits`, `prepaidCreditsUsed`, límite efectivo). La doc no explicita el método HTTP de estas lecturas: verificar.
- `GET /v1/api-key` devuelve gratis el `team_id`, así que una clave de gestión enriquece sola a todas las claves de su equipo, cruzando por `api_key_id`.

### OpenRouter (fase 4)

- `GET https://openrouter.ai/api/v1/key` con la propia clave devuelve `limit`, `limit_remaining`, `usage`, `usage_daily`, `usage_weekly`, `usage_monthly`, `is_free_tier`, `is_management_key`, `expires_at` y `effective_budget`. Es el único proveedor que da saldo y caducidad sin clave especial.

### Mapa de errores

Cada proveedor tiene su `errors.py` con este mapeo como funciones puras, probado con una fixture por fila (`tests/test_error_map.py` comprueba que cada ID de esta tabla tiene su caso); los mensajes se comparan por subcadena, sin distinguir mayúsculas. Las filas de sondas activas, APIs de administración y OpenRouter se conectan en la Fase 4. Revisado contra la documentación oficial el 4 de noviembre de 2026; lo que no cuadra está en `docs/providers.md`.

| ID | Proveedor | Señal | Estado en Mecha |
| --- | --- | --- | --- |
| `all-401` | Todos | 401 en el endpoint de validación | Validez `invalid`; si su caducidad conocida (manual o de admin) ya pasó, se muestra como caducada |
| `all-403` | Todos | 403 (sin permiso, región o proyecto restringido) | Ámbar «sin permiso»: es una respuesta del proveedor, no un fallo al comprobar |
| `all-429` | Todos | 429 sin otra señal | Límites `throttled` |
| `all-network` | Todos | Timeout, error de red o 5xx/529 | Gris; 3 reintentos con backoff exponencial |
| `anthropic-credit` | Anthropic | 400 con «credit balance» o «insufficient credits» | Saldo `empty` |
| `anthropic-billing` | Anthropic | 402 `billing_error` | Saldo `empty`, rojo, «problema de facturación» |
| `anthropic-admin-status` | Anthropic (admin) | `status` `inactive` / `archived` / `expired` | Validez `blocked` / `revoked` / caducidad `expired` |
| `openai-credit` | OpenAI | 429 `credit_balance_exhausted` (variante: `type` `insufficient_quota`) | Saldo `empty` |
| `openai-spend-limit` | OpenAI | 429 `organization_spend_limit_exceeded`, `project_spend_limit_exceeded`, `organization_usage_limit_exceeded` | Saldo `capped` |
| `openai-rate-limit` | OpenAI | 429 `rate_limit_exceeded` o `slow_down` | Límites `throttled` |
| `openai-missing-scopes` | OpenAI | 401 con «Missing scopes» o «insufficient permissions» (clave restringida) | Ámbar «sin permiso», no inválida |
| `gemini-invalid` | Gemini | 401 (variante: 400 `API_KEY_INVALID`) | Validez `invalid` |
| `gemini-restricted` | Gemini | 400/403 con `ErrorInfo.reason` de `google.api.ErrorReason` | Ámbar si depende de desde dónde se llama (`API_KEY_IP_ADDRESS_BLOCKED`, `API_KEY_HTTP_REFERRER_BLOCKED`, `API_KEY_ANDROID_APP_BLOCKED`, `API_KEY_IOS_APP_BLOCKED`, `SECURITY_POLICY_VIOLATED`, `ORG_RESTRICTION_VIOLATION`, `TLS_*`); rojo si falla desde cualquier sitio (`API_KEY_SERVICE_BLOCKED`, `SERVICE_DISABLED`, `BILLING_DISABLED`, `CONSUMER_SUSPENDED`, `GCP_SUSPENDED`, `CONSUMER_INVALID`, `RESOURCE_USAGE_RESTRICTION_VIOLATED`, `ENDPOINT_USAGE_RESTRICTION_VIOLATED`, `IAM_PERMISSION_DENIED`). Cada uno con su motivo concreto |
| `gemini-leaked` | Gemini | «reported as leaked» | Validez `blocked` |
| `gemini-prepay` | Gemini | 402 Payment Required (variante: 429 «prepayment credits are depleted») | Saldo `empty` |
| `gemini-quota` | Gemini | 429 con `QuotaFailure` | Límites `throttled`; si el `quotaId` contiene `FreeTier`, plan gratuito como `inferred` |
| `gemini-aq-unsupported` | Gemini | 401 `ACCESS_TOKEN_TYPE_UNSUPPORTED` con clave `AQ.` | Inválida, con la pista «si funciona en AI Studio, puede ser el problema conocido de las `AQ.`»: cualquier `AQ.` inválida devuelve ese motivo (comprobado en vivo), así que no es un diagnóstico |
| `xai-invalid` | xAI | 400 con «Incorrect API key provided» (API) o 401 «Invalid bearer token» (gestión) | Validez `invalid`. La documentación dice 401, pero la API responde 400 (comprobado en vivo) |
| `xai-flags` | xAI | `api_key_disabled`, `api_key_blocked` o `team_blocked` a `true` | Validez `blocked` («equipo bloqueado» para `team_blocked`) |
| `xai-mgmt-expired` | xAI (gestión) | `expireTime` en el pasado | Caducidad `expired` |
| `openrouter-empty` | OpenRouter | `limit_remaining` ≤ 0 | Saldo `empty` |
| `probe-404` | Todos (sonda activa) | 404 del modelo de la sonda | Gris «sonda mal configurada», no clave inválida; se olvida el modelo elegido para volver a escoger |
| `probe-timeout` | Todos (sonda activa) | Timeout después de enviar el `POST` | Gris «resultado incierto»: puede haberse cobrado, nunca se reintenta solo |

Sin mapeo por ahora: el tope de gasto mensual de Gemini (la documentación no dice qué error devuelve) y el error de saldo agotado de xAI (texto sin documentar). Ambos quedan `unknown` hasta ver el error real.

## Arquitectura y stack

Un único proceso Python sirve la API y el frontend compilado; el núcleo no sabe nada de HTTP entrante y cada proveedor es un plugin con la misma interfaz.

&#91;embedded content: arquitectura de Mecha · un proceso local, cinco proveedores\]

La línea discontinua es 127.0.0.1: lo único que la cruza hacia fuera es HTTPS desde los plugins hacia los hosts permitidos de cada proveedor.

**Stack**

| Capa | Elección |
| --- | --- |
| Proyecto y entorno | uv (`uv init --package`, backend `uv_build`), Python 3.12 o superior |
| API local | FastAPI + Uvicorn |
| HTTP saliente | httpx asíncrono, timeout de 10 s |
| Modelos | Pydantic v2 |
| Persistencia | SQLite (sqlite3 o SQLModel), migraciones numeradas |
| Cifrado | `cryptography` (AES-256-GCM) y `keyring` para la clave maestra |
| CLI | Typer + Rich |
| Planificador | Tarea asyncio propia, sin dependencias extra |
| Rutas de usuario | platformdirs |
| Frontend | Vite + React 19 + TypeScript + Tailwind CSS v4 + shadcn/ui (primitivas Radix, las de por defecto) |
| Datos en el frontend | TanStack Query, y `fetch` con streaming (`eventsource-parser`) para el progreso: `EventSource` no puede enviar la cabecera `Authorization` y el token en la URL acabaría en logs e historial |
| Tablas y gráficos | TanStack Table y los charts de shadcn/ui (Recharts) |
| Iconos, animación y avisos | lucide-react, Motion (con moderación) y avisos propios sobre Radix Toast (Sonner inyecta su CSS sin nonce) |

**Estructura del repo**

```text
mecha/
├── pyproject.toml          # uv; scripts mecha y mecha-keys
├── justfile                # dev, build, test, release
├── src/mecha/
│   ├── cli.py              # Typer
│   ├── server.py           # app FastAPI, estáticos, seguridad
│   ├── api/                # routers REST y SSE
│   ├── core/               # models, status, detect, jwt, checks, scheduler
│   ├── providers/          # base, anthropic, openai, gemini, xai, openrouter
│   ├── linked/             # codex, claude_code, gemini_cli
│   ├── store/              # db, crypto, migrations/
│   ├── data/               # detect_patterns.toml, capabilities.toml
│   └── static/             # build del frontend (generado, fuera de git)
├── web/                    # Vite + React + shadcn/ui
└── tests/                  # pytest + respx, fixtures/<proveedor>/
```

**Interfaz de proveedor**

```python
class Provider(Protocol):
    id: ProviderId
    capabilities: Capabilities  # qué puede saber y con qué tipo de credencial

    allowed_hosts: frozenset[str]  # el cliente HTTP rechaza cualquier otro destino

    def endpoint(self, kind: CredentialKind) -> tuple[str, str, dict]: ...  # endpoint gratuito
    async def check_passive(
        self, cred: Credential, secret: str, http: httpx.AsyncClient, ctx: ProviderContext
    ) -> Observation: ...
    async def check_active(self, cred: Credential, http: httpx.AsyncClient) -> CheckResult: ...
    async def enrich(
        self, admin: Credential, children: list[Credential], http: httpx.AsyncClient
    ) -> dict[CredentialId, Enrichment]: ...
```

La detección no está en el plugin: la resuelve el núcleo con `detect_patterns.toml`. El núcleo descifra el secreto y se lo pasa al plugin solo durante la petición. Un plugin nunca lanza excepciones crudas: devuelve una `Observation` (lo que aprendió de una respuesta), que el motor de estados fusiona con las condiciones offline en el momento de mostrarla, así la caducidad sigue contando entre comprobaciones; un fallo de red es una observación gris. La matriz `capabilities` vive en `data/capabilities.toml` y un test la contrasta con el `endpoint()` de cada plugin.

**Flujo de una comprobación**

1. El planificador o el usuario pide comprobar una credencial.
2. El núcleo descifra el secreto en memoria y elige comprobación pasiva o activa según su configuración.
3. El plugin hace una única petición al host permitido de su proveedor y devuelve señales.
4. El motor de estados calcula el semáforo, guarda el resultado y lo emite por SSE.
5. Si hay una clave de administración o gestión vinculada, su enriquecimiento se fusiona con `source: api`.

Concurrencia: 4 comprobaciones a la vez en total y 2 por proveedor, con un intervalo mínimo de 5 minutos por credencial (también en `mecha check`, que reutiliza el último resultado, lo marca como «en caché» con su hora y admite `--force`). El planificador corre dentro de `mecha serve` y lanza comprobaciones pasivas cada 6 horas por defecto; tras un fallo reintenta con backoff (el intervalo mínimo, con un suelo de 1 minuto, el doble, y así hasta `interval_hours`), para que un portátil que despierta sin red no quede gris horas. Un resultado fallido nunca cuenta como «fresco». Para cron se usa `mecha check`.

**Configuración.** `config.toml` en `MECHA_HOME`, validado con Pydantic y con aviso ante claves desconocidas: `[thresholds]` (`soon_days`, `low_funds_usd`), `[scheduler]` (`enabled`, `interval_hours`, `min_interval_minutes`, `concurrency`, `per_provider`), `[http]` (`timeout_seconds`, `retries`, `backoff_seconds`) y `[providers.anthropic]` (`version`). Las variables de entorno se reservan para rutas y secretos.

**Empaquetado**

- `just build` ejecuta `vite build` con salida en `src/mecha/static/`, y `uv build` incluye ese directorio en el wheel.
- `uvx mecha-keys` arranca en `127.0.0.1:7878` (configurable) y abre el navegador.
- En desarrollo, Vite sirve en el puerto 5173 con proxy de `/api` hacia FastAPI.

## Modelo de datos y API REST

La API REST solo la usa el frontend local y nunca devuelve un secreto: como mucho, su forma redactada y su huella.

```python
class Credential(BaseModel):
    id: str                          # ULID
    label: str
    provider: ProviderId             # anthropic | openai | gemini | xai | openrouter
    kind: CredentialKind             # api_key | admin_key | management_key | oauth_token | oauth_file
    redacted: str                    # "sk-ant-api03-R2D…igAA"
    fingerprint: str                 # HMAC-SHA256 con la clave maestra, 12 caracteres; detecta duplicados
    tags: list[str] = []
    notes: str | None = None
    linked_path: str | None = None   # solo oauth_file
    parent_id: str | None = None     # admin o clave de gestión que la enriquece
    manual_expires_at: datetime | None = None
    manual_budget: Budget | None = None  # importe, moneda y fecha de compra
    active_probe: bool = False
    created_at: datetime
    pattern_id: str | None = None    # patrón de detect_patterns.toml que la clasificó
    token_expires_at: datetime | None = None    # claim exp (JWT), derivado sin red al dar de alta
    subscription_until: datetime | None = None  # chatgpt_subscription_active_until (JWT)
    retiring: bool = False           # tipo de clave en retirada (p. ej. Gemini AIza)

class Signal(BaseModel, Generic[T]):
    value: T
    source: Literal["api", "header", "jwt", "file", "manual", "inferred"]
    observed_at: datetime

class CheckResult(BaseModel):
    credential_id: str
    checked_at: datetime
    mode: Literal["offline", "passive", "active", "enrich"]
    check_status: Literal["not_checked", "failed", "ok"]  # «sin comprobar» ≠ «falló la comprobación»
    reasons: list[Reason]            # códigos que explican el color (expires_soon, retiring_key_type…)
    overall: Literal["green", "amber", "red", "grey", "local"]  # prioridad red > amber > grey > green; local solo OAuth
    validity: Signal[Validity]       # incluye not_applicable para OAuth
    funds: Signal[Funds]             # estado, importe y moneda
    expiry: Signal[Expiry]           # estado y expires_at
    limits: Signal[Limits] | None    # requests y tokens: limit, remaining, reset
    account: dict[str, Signal[str]]  # org_id, team_id, email, plan, workspace…
    http: HttpTrace | None           # endpoint, status y latencia; cabeceras ya redactadas
    error: ProviderError | None      # código y mensaje del proveedor, sin secretos
```

El historial guarda un `CheckResult` por fila y conserva los últimos 500 por credencial. `GET /api/credentials` devuelve, junto al último resultado, el último resultado no gris (`last_known`, con su `checked_at`) para que una tarjeta gris muestre el último estado conocido.

| Método | Ruta | Uso |
| --- | --- | --- |
| GET | `/api/credentials` | Lista con el último resultado de cada credencial |
| POST | `/api/credentials` | Alta con `secret`, `label`, `tags`, `notes` y opciones; responde sin secreto |
| POST | `/api/detect` | Clasifica un secreto sin guardarlo ni usar la red |
| PATCH | `/api/credentials/{id}` | Etiqueta, notas, fecha o presupuesto manual, sonda activa, `parent_id` |
| DELETE | `/api/credentials/{id}` | Borra la credencial y su historial |
| POST | `/api/credentials/{id}/check` | Comprueba ahora; query `mode` = `passive` o `active` |
| POST | `/api/check-all` | Comprueba todas respetando la concurrencia |
| GET | `/api/credentials/{id}/history` | Historial; query `limit` (50 por defecto) |
| GET | `/api/providers` | Matriz de capacidades, para que la UI sepa qué prometer |
| POST | `/api/import/env` | Importa el contenido de un `.env` pegado o subido |
| POST | `/api/linked` | Vincula un fichero de sesión OAuth por ruta |
| GET | `/api/events` | Flujo de eventos (formato SSE, leído con `fetch`) que solo dice qué cambió; la UI recarga por REST, y al reconectar lo recarga todo (sin `Last-Event-ID`). Keepalive cada 15 s; los flujos se cierran al apagar para que Ctrl+C no se cuelgue |
| GET | `/api/health` | Estado del servidor |

Los errores tienen la forma `{code, message}` y nunca incluyen el secreto ni cabeceras de autorización.

## Seguridad

Mecha guarda secretos de alto valor, así que el diseño asume que cualquier web abierta en el navegador puede intentar hablar con él.

- **Cifrado en reposo.** Cada secreto se cifra con AES-256-GCM (nonce aleatorio, el `id` de la credencial como dato asociado). Todo cuelga de una **clave de datos** aleatoria de 32 bytes; de ella se derivan con HKDF una subclave de cifrado y otra para el HMAC de la huella.
  - **Llavero:** guarda la clave de datos tal cual, en una entrada indexada por el id de almacén guardado en la base, para que dos `MECHA_HOME` no compartan entrada.
  - **Frase de paso (opcional, también de recuperación):** si se define, la base guarda además la clave de datos cifrada con una clave derivada de la frase por Argon2id (scrypt si Argon2id no está disponible al crear). En metadatos van el KDF, sus parámetros y el salt; al abrir nunca se cambia de KDF en silencio, y si el KDF guardado no está disponible es un error claro.
  - **Orden al abrir:** llavero → `MECHA_PASSPHRASE` o `MECHA_PASSPHRASE_FILE` (fichero 0600, recomendado para cron) → prompt si hay TTY → si no, salida 3 con un mensaje claro. Si la frase abre el almacén y el llavero no tenía la entrada, se restaura en el llavero.
  - **Verificador:** un valor fijo cifrado con la clave de datos detecta una clave o frase incorrecta antes de tocar filas.
  - **Nunca encima:** si la base existe y no hay clave, es un error; nunca se genera una clave nueva sobre datos existentes. `mecha passphrase set|remove` gestiona la frase; `remove` solo se permite si el llavero tiene la clave.
- **Disco.** La base de datos está en el directorio de datos de usuario (platformdirs, o `MECHA_HOME`) con permisos 0600 y directorio 0700. El proceso fija `umask 077` antes de abrir SQLite para que `-wal`, `-shm` y `-journal` nazcan con 0600; `secure_delete` está activo. En Windows los permisos POSIX son lo mejor posible. Ningún secreto queda en claro en disco, logs ni historial.
- **Solo localhost.** El servidor escucha en 127.0.0.1 y rechaza cualquier cabecera `Host` distinta de `127.0.0.1:<puerto>` o `localhost:<puerto>`, contra DNS rebinding.
- **Token de sesión.** Cada arranque genera un token aleatorio y la CLI abre `http://127.0.0.1:7878/#t=<token>`. El frontend lo lee del fragmento, lo borra de la barra de direcciones y lo envía en `Authorization`. Sin token, 401; sin CORS.
- **CSP estricta.** El HTML servido no carga nada de terceros: fuentes e iconos van empaquetados. Los estilos que inyectan Radix, Motion o los gráficos se autorizan con un nonce distinto en cada respuesta de `index.html` (servido con `no-store`), nunca con `'unsafe-inline'`; `frame-ancestors 'none'`, `base-uri 'none'` y `form-action 'self'` van explícitos porque no heredan de `default-src` (ADR 0001).
- **Peticiones.** Cuerpos de más de 64 KiB se rechazan (413) sin leerlos; el secreto admite como mucho 8 KiB. Los 422 nunca devuelven el input, ni siquiera los nombres de campo sin redactar.
- **Sin vuelta atrás.** Tras el alta, el frontend solo ve `redacted` y `fingerprint`. En la v1 no existe «mostrar clave».
- **Logs.** Un filtro de logging (instalado como factoría de `LogRecord`, cubre todos los loggers y el texto de las excepciones) sustituye cualquier coincidencia de los patrones de detección por su forma redactada, y httpx nunca registra cabeceras.
- **Trazas.** Typer va con `pretty_exceptions_show_locals=False` y sin trazas de Rich: mostrarían las variables locales, secreto incluido. Los errores inesperados de la CLI se imprimen redactados y salen con 3.
- **Salidas controladas.** Cada plugin declara, por tipo de clave, una lista cerrada de **método + host + ruta**, y el cliente HTTP rechaza cualquier otra petición, otro puerto, `http://` y las redirecciones. Las admin keys y las claves de gestión, que podrían crear o borrar claves, solo tienen las lecturas `GET` verificadas; una clave de gestión de xAI solo llega a `management-api.x.ai` y una clave normal nunca llega allí. Solo se reintentan las lecturas: un `POST` (sonda activa) se envía una vez, y si da timeout tras enviarse queda como «resultado incierto», porque puede haberse cobrado. Respeta `HTTPS_PROXY`/`NO_PROXY` y `SSL_CERT_FILE`/`SSL_CERT_DIR` (proxy corporativo, CA propia): el guard valida el host de destino con o sin proxy, y httpx no lee `.netrc` sin `NetRCAuth` explícito.
- **Ficheros vinculados.** Solo lectura; los tokens se leen en cada comprobación y nunca se copian.
- **Copias.** `mecha export` produce un fichero cifrado con frase de paso; no hay export en claro.

## UI/UX

La pantalla principal responde «¿qué está roto o va a romperse?» en menos de dos segundos; todo el detalle vive en un panel lateral.

**Dashboard**

- Cabecera con cinco contadores (verde, ámbar, rojo, gris y «solo local», para que siempre sumen el total) animados con un number ticker, el botón «Comprobar todo» con progreso en vivo por SSE y la hora de la última comprobación global.
- Barra de filtros por proveedor, estado, tipo y etiqueta, con búsqueda. ⌘K abre una paleta de comandos (Command de shadcn/ui) para saltar a una clave o lanzar acciones; `N` abre el alta.
- Rejilla de tarjetas con vista alternativa en tabla. Orden por defecto: rojo, ámbar, gris y verde, y dentro de cada grupo, lo que caduca antes.
- Datos al día con la pestaña abierta: además de los eventos, la lista se recarga al volver a la pestaña y cada 60 s, porque un `mecha check` lanzado desde cron no pasa por el bus de eventos y una clave puede caducar con la pestaña abierta.
- Borrar pide confirmación en un `AlertDialog` con la etiqueta y la forma redactada, con el foco en Cancelar; un `Escape` cierra solo la confirmación.

**Tarjeta de credencial**

- Icono del proveedor, etiqueta, clave redactada en monoespaciada e insignia de tipo (API key, Admin, Gestión, OAuth).
- Píldora de estado con icono y texto, nunca solo color.
- La mecha, una línea de saldo («12,40 USD», «Sin saldo», «No disponible por API»), una línea de límites y «comprobada hace 3 h».
- Acciones: comprobar, sonda activa (con su coste estimado en el tooltip) y abrir detalle.
- Las tarjetas rojas llevan un borde animado sutil; el resto, quietas.

**La mecha, componente firma**

- Barra horizontal que se consume según el tiempo restante hasta caducar, sobre un horizonte fijo (`fuse_horizon_days`, 90 días por defecto en `config.toml`): el mismo tiempo restante se ve igual en todas las tarjetas, se diera de alta cuando se diera; más allá del horizonte, la barra está llena. Si no hay caducidad, según el saldo restante frente al presupuesto (Fase 4).
- Una chispa animada marca el extremo que arde, y el color sale del estado de caducidad que calcula el backend (`scheduled`, `soon`, `expired`), así respeta `soon_days` de `config.toml` sin repetir el umbral en el frontend.
- Sin caducidad ni saldo conocidos: barra punteada gris con el texto «sin fecha conocida».
- Respeta `prefers-reduced-motion`: sin chispa ni animaciones.

**Panel de detalle (Sheet)**

- Todas las señales con un chip de fuente: «API», «JWT», «fichero», «manual» o «inferido».
- Límites de uso, datos de cuenta u organización y, si aplica, la clave de administración que la enriquece.
- Historial con una franja de estados y una sparkline de latencia.
- Cabeceras de la última respuesta, ya redactadas y plegadas por defecto, y notas editables.

**Alta (Dialog)**

- Al pegar el secreto, `/api/detect` muestra al instante proveedor, tipo y qué se podrá saber de él («validez: sí · saldo: no · caducidad: con admin key»).
- Un formato no confirmado en la documentación oficial (`verified = false`, como `AQ.` o `AIza`) es una etiqueta discreta en `detect`, en `--json` y en el panel de detalle, nunca un aviso en cada alta. El alta solo avisa cuando Mecha cambia lo que hace por el patrón (no guarda un refresh token, pide confirmar el proveedor, remite a `mecha link`).
- Campos: etiqueta, etiquetas, caducidad o presupuesto manual y casilla de sonda activa con su coste estimado (presupuesto y sonda, en la Fase 4).
- El campo del secreto lleva `spellcheck=false` y `autocomplete=off` (los correctores mejorados de Chrome y Edge envían el texto a sus servidores). La detección en vivo es una mutación con debounce, sin caché: nunca un `useQuery` con el secreto en la clave, que quedaría en la caché y en las devtools. Al cerrar el diálogo se borra todo el estado.
- Pestañas alternativas para importar un `.env` o vincular un fichero de sesión; si existen las rutas por defecto, se ofrecen directamente.

**Estilo**

- Tema oscuro por defecto con conmutador; estética de herramienta para desarrolladores, densa y sobria, al estilo de Linear o Vercel.
- Geist Sans y Geist Mono, empaquetadas; un único color de acento (el ámbar de una mecha encendida) y los colores de estado reservados para el estado.
- Contraste AA, foco visible y uso completo con teclado.
- Textos en español e inglés según el idioma del navegador.
- Modo demo (`mecha serve --demo`) con datos falsos, para capturas, el README y vídeos: sin secretos (solo formas redactadas y resultados fabricados), determinista (semilla y «ahora» fijos), con cada estado del semáforo y cada tipo de credencial, sin red y con una insignia «Demo» visible. Se puede añadir y borrar en memoria: del secreto añadido solo se guarda la forma redactada, nunca el secreto, y sus comprobaciones son fabricadas.

## CLI

La CLI comparte núcleo con la web y sirve para cron, CI y para quien vive en la terminal.

| Comando | Qué hace |
| --- | --- |
| `mecha` o `mecha serve` | Arranca el servidor y abre el navegador; `--port`, `--no-open`, `--demo` |
| `mecha add` | Alta con entrada oculta o por stdin (el secreto nunca va como argumento: acabaría en el historial y en la lista de procesos); `--label`, `--tag`, `--notes`, `--provider`, `--kind`, `--expires`. La entrada se normaliza: espacios y saltos de línea, comillas, `NOMBRE=` y `Bearer` delante |
| `mecha ls` | Tabla con semáforo, tipo, caducidad, saldo y última comprobación |
| `mecha check [ID…]` | Comprueba con el endpoint gratuito; `--json` para scripts, `--strict` para tratar el gris como fallo, `--force` para saltarse el intervalo mínimo; `--active` usa la sonda de pago (una por hora; con `--force`, aunque no haya pasado la hora) |
| `mecha detect` | Clasifica un secreto leído de stdin, sin red ni guardado |
| `mecha import .env` | Importa `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY` y cualquier valor con prefijo reconocido |
| `mecha link <herramienta>` | Vincula el fichero de sesión de `codex`, `claude-code` o `gemini-cli`; `--path` para rutas propias |
| `mecha rm ID` | Borra una credencial y su historial |
| `mecha export` y `mecha restore` | Copia cifrada con frase de paso |
| `mecha passphrase set` / `remove` | Define, cambia o quita la frase de paso de recuperación del almacén |

`check` y `ls` siguen la convención de Nagios: salen con 0 si todo está verde, gris o solo local (OK), 1 si hay algún ámbar (aviso), 2 si hay algún rojo (crítico) y 3 si falla Mecha (desconocido). Cualquier error en esos dos comandos, incluidos los de uso como una opción mal escrita, sale con 3 y nunca con el 2 de Click, que un script leería como «hay rojo». Con `--strict`, `check` trata el gris como fallo y sale con 3; el rojo sigue ganando (2), y el gris gana al ámbar porque «no pude comprobarlo» no es un mero aviso. En el resto de comandos, 2 indica un error del usuario (opción inválida, clave duplicada o ambigua) y 3 un fallo de Mecha.

```bash
# cada mañana a las 8: aviso si alguna clave está en ámbar o en rojo
0 8 * * * mecha check || notify-send "Mecha" "Revisa tus API keys"
```

## Testing y calidad

Ningún test toca la red ni usa claves reales: cada proveedor se prueba contra respuestas grabadas y anonimizadas.

- **Fixtures por proveedor** en `tests/fixtures/<proveedor>/`: válida, inválida, sin saldo, caducada, bloqueada, rate limit, 5xx, timeout y respuestas de las APIs de administración. respx intercepta httpx.
- **Detección:** tabla de prefijos con casos positivos y negativos, JWT malformados y ficheros vinculados con campos ausentes.
- **Motor de estados:** tabla de verdad completa del semáforo.
- **Fugas de secretos:** el test da de alta claves canario y comprueba que no aparecen en respuestas de la API, logs, SSE ni en el SQLite.
- **Superficie local:** peticiones con `Host` ajeno o sin token devuelven error.
- **Frontend:** Vitest y Testing Library para la tarjeta y la mecha (incluido `prefers-reduced-motion`), y un smoke test con Playwright contra el modo demo.
- **Calidad:** ruff para lint y formato, mypy estricto en `core` y `providers`, Biome en `web` y gitleaks como pre-commit.
- **CI:** GitHub Actions con `uv sync`, `uv run pytest`, build del frontend y `uv build` en Linux, macOS y Windows.

## Plan por fases

Seis fases; cada una se cierra con los tests en verde y sus criterios cumplidos antes de abrir la siguiente.

### Fase 0 · Esqueleto

`uv init --package`, estructura del repo, app Vite con shadcn/ui, justfile y CI.

- [x] `uv run mecha serve` sirve una página vacía protegida por token de sesión.
- [x] `uv run pytest` y el build del frontend pasan en CI.

### Fase 1 · Núcleo offline

Modelos, detección, decodificación JWT, almacén cifrado, parte offline del motor de estados (sin red: `AIza` en ámbar, caducidad manual o de JWT, OAuth como `local`, el resto gris «sin comprobar»), capa de servicio en `core` (add, list, detect, remove; la Fase 3 solo la envuelve) y CLI con `add`, `ls`, `detect` y `rm`. `ls` ya usa los códigos de salida.

- [x] Cada patrón de la tabla de detección tiene test positivo y negativo.
- [x] Una clave canario no aparece en claro en el SQLite ni en los logs.

### Fase 2 · Comprobaciones pasivas

Plugins de Anthropic, OpenAI, Gemini y xAI con su endpoint gratuito, motor de estados, historial y planificador.

- [x] Cada fila del mapa de errores tiene una fixture que produce el estado esperado.
- [x] `mecha check --json` devuelve un esquema estable y los códigos de salida definidos.

### Fase 3 · Dashboard

Tarjetas, mecha, filtros, paleta ⌘K, panel de detalle, alta con detección en vivo, SSE y modo demo.

- [x] Con 20 credenciales demo, la pantalla carga en menos de 1 s y se puede usar solo con teclado.
- [x] Ningún estado se comunica solo con color.

### Fase 4 · Profundidad

Sondas activas, claves de administración y gestión con enriquecimiento, ficheros vinculados, importación de `.env`, OpenRouter y saldo estimado de OpenAI.


Una caducidad que venga de una API (admin key, gestión de xAI, OpenRouter) se guarda como dato de la credencial, igual que la manual, y no solo dentro de la última observación: si el siguiente check falla, la clave que caduca mañana debe seguir en ámbar, no pasar a gris.

Decisiones de la fase:

- **Hechos de la credencial.** Lo que informa una API (caducidad, estado, último uso, saldo) o descubre una sonda activa (saldo agotado, límites) se guarda como dato de la credencial con su fuente y su fecha (migración `002`), así un check pasivo posterior, que no sabe nada de saldo, no devuelve la tarjeta a verde. Los límites de una sonda cuentan como mucho una hora.
- **Sondas activas.** Una petición mínima de pago por proveedor, con el modelo fijado en `config.toml` o, en `auto`, el más barato del listado gratuito de modelos. Como mucho una por credencial y hora: el planificador nunca se lo salta (y usa la sonda cuando `active_probe` está activado); a mano sí, con confirmación explícita (`--force` en la CLI, un diálogo en la UI). Un 404 del modelo es «sonda mal configurada», no una clave inválida. Las sondas gratuitas (`count_tokens`, `countTokens`) existen como opción, pero no está documentado si reflejan el saldo agotado. El coste se estima con una tabla de precios fechada (`capabilities.toml`): tokens, USD por sonda y USD al mes con la frecuencia del planificador, redondeado hacia arriba.
- **Enriquecimiento.** La pista de la API de administración se cruza generando su formato desde el secreto descifrado (cabeza y cola); si una pista coincide con más de una clave guardada, no se enriquece ninguna. xAI cruza por `api_key_id`.
- **Saldo de xAI.** Prepago más el margen que queda hasta el límite mensual de pospago; rojo solo si no queda ni prepago ni margen. Se muestra en la clave de gestión y en cada clave del equipo, con su umbral.
- **Saldo estimado de OpenAI.** Presupuesto manual en la admin key menos la Costs API desde esa fecha, con la fecha del último día incluido. En las claves hijas es solo información («saldo de la organización, estimado») y nunca cambia el semáforo. El formulario avisa de que con recarga automática la estimación no sirve.
- **Ficheros vinculados.** Claude Code en macOS: si el fichero existe en la ruta por defecto o en `--path`, se lee; el Llavero queda fuera de la v1 y solo se menciona cuando no hay fichero. Un fichero que desaparece pone la tarjeta en ámbar.
- **Importar `.env`.** Vista previa sin estado con las claves ya redactadas para elegir cuáles importar; el servidor no guarda nada entre la vista previa y la importación.

- [ ] Una admin key de OpenAI rellena `expires_at` y `last_used_at` de las project keys guardadas.
- [ ] Una clave de gestión de xAI muestra el saldo prepago en USD.
- [ ] Un fichero de Codex vinculado muestra plan y fin de suscripción sin copiar tokens.

### Fase 5 · Distribución

Frontend dentro del wheel, `uvx mecha-keys`, README con GIF del modo demo y copias cifradas.

- [ ] `uvx mecha-keys` funciona en una máquina limpia con Linux, macOS y Windows.
- [ ] `mecha export` y `mecha restore` recuperan todas las credenciales.

## Instrucciones para Claude Code y riesgos

Implementa fase a fase y no inventes endpoints: lo marcado como «verificar» se confirma en la documentación oficial antes de escribir el código.

**Instrucciones**

- Una fase por rama y PR, con sus tests, antes de pasar a la siguiente.
- Antes de cada proveedor, lee su documentación oficial (sección Referencias). Si un endpoint no existe o ha cambiado, deja el dato en `unknown`, anota un TODO y documéntalo en `docs/providers.md`.
- La matriz de capacidades es el contrato: vive en `data/capabilities.toml` y de ella salen `GET /api/providers` y los textos de la UI.
- Nunca uses claves reales en tests, fixtures ni commits.
- Código, identificadores y README en inglés; la UI, en español e inglés.
- Si trabajas con Spec-Kit: «Visión y nombre» va a `/speckit.constitution`; Alcance, Modelo de estados, Detección y Capacidades, a `/speckit.specify`; Arquitectura, Datos y Seguridad, a `/speckit.plan`, y el Plan por fases, a `/speckit.tasks`.

**Riesgos**

| Riesgo | Mitigación |
| --- | --- |
| Los ficheros de sesión de las CLIs y los claims de ChatGPT no están documentados y pueden cambiar | Parsers tolerantes; datos marcados como `file` o `jwt` sin verificar |
| Aparecen formatos de clave nuevos, como `AQ.` en Gemini | Detección por datos en `detect_patterns.toml`, no por código |
| Términos de uso de los tokens OAuth de suscripción | Nunca se usan para llamar a una API; solo inspección local |
| Las sondas activas cuestan dinero | Opt-in por credencial, coste estimado visible y como máximo una sonda por credencial y hora |
| Cambios de versión de API, como `anthropic-version: 2026-10-21` | Versión en configuración y tests de contrato por proveedor |
| Muchas comprobaciones seguidas disparan rate limits | Concurrencia limitada e intervalo mínimo por credencial |
| Mensajes de error en texto libre (Anthropic, Gemini, xAI) | Comparación por subcadena con varias variantes y una fixture por variante |

## Referencias

Páginas consultadas el 4 de noviembre de 2026; las que no son documentación oficial se indican como tales.

- **Anthropic:** [List API Keys (Admin API)](https://platform.claude.com/docs/en/api/admin/api_keys/list) · [Legal y cumplimiento de Claude Code](https://code.claude.com/docs/es/legal-and-compliance) · [Claude API 2026-10-21, qué cambia (DEV, terceros)](https://dev.to/tokely/claude-api-2026-10-21-what-breaks-and-how-to-upgrade-1954) · [Error 400 de saldo insuficiente (Portkey, terceros)](https://portkey.ai/error-library/anthropic-400-credit-balance-too-low) · [Fin del login de suscripción en terceros (GIGAZINE)](https://gigazine.net/gsc_news/en/20260220-anthropic-third-party-block)
- **OpenAI:** [Admin API, Organization](https://developers.openai.com/api/reference/resources/admin/subresources/organization) · [Hilo sobre saldo vía Admin API (foro de OpenAI)](https://community.openai.com/t/access-billing-balance-costs-via-admin-api/1383654) · [Caducidad y vida máxima de claves (AI Cybr, terceros)](https://aicybr.com/blog/openai-api-key-expiration-maximum-lifetime-guide) · [Claims del id\_token de Codex (CLIProxyAPI, terceros)](https://pkg.go.dev/github.com/router-for-me/CLIProxyAPI/v6/internal/auth/codex)
- **Google Gemini:** [Using Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key?facet2=pdf) · [Prepay for the Gemini API (blog de Google)](https://blog.google/innovation-and-ai/technology/developers-tools/prepay-gemini-api/) · [Gemini API key abuse fix (DoiT, terceros)](https://www.doit.com/blog/the-gemini-api-key-abuse-fix-is-here-and-it-has-a-deadline) · [AQ. en lugar de AIza (DEV, terceros)](https://dev.to/rapls/i-created-a-gemini-api-key-and-got-aq-instead-of-aiza-3dp3) · [Corte de septiembre de las standard keys (DEV, terceros)](https://dev.to/truerate_tool/the-september-key-cutoff-gemini-is-already-rejecting-standard-api-keys-pilot-2-5ff0) · [429 por prepago agotado (foro de Google AI)](https://discuss.ai.google.dev/t/429-resource-exhausted-prepayment-credits-are-depleted-prepay-balance-reads-r-0-00-despite-funded-billing-account/170005) · [401 ACCESS\_TOKEN\_TYPE\_UNSUPPORTED con claves AQ. (foro de Google AI)](https://discuss.ai.google.dev/t/account-restricted-to-aq-keys-all-return-401-access-token-type-unsupported-on-generativelanguage-googleapis-com-requesting-fix-aiza-restoration/175424) · [Error de clave filtrada (Developers Digest, terceros)](https://www.developersdigest.tech/errors/api/gemini-api/leaked-api-key)
- **xAI:** [Management API](https://docs.x.ai/developers/rest-api-reference/management.md) · [Management API, cuentas y autorización](https://docs.x.ai/developers/rest-api-reference/management/auth.md) · [Management API, facturación](https://docs.x.ai/docs/management-api/billing) · [Facturación y créditos](https://docs.x.ai/docs/billing) · [GET /v1/api-key (ficha de One, terceros)](https://www.withone.ai/knowledge/x-ai/conn_mod_def%3A%3AGJ8XVvFfYLY%3A%3AQqyL4WznQNyIyPusb1XmRw/md)
- **OpenRouter:** [Get current API key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key.md)
- **Ficheros de sesión y frontend:** [Ficheros de credenciales de Claude Code (SciTeX, terceros)](https://scitex-agent-container.readthedocs.io/en/latest/credentials.html) · [shadcn/ui, Base UI y Radix en 2026 (PkgPulse, terceros)](https://www.pkgpulse.com/guides/shadcn-ui-vs-base-ui-vs-radix-components-2026)
