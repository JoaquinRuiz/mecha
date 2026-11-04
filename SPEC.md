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

Al pegar un secreto, Mecha lo clasifica sin red por prefijo, forma JWT o estructura JSON, y esa clasificación decide qué comprobaciones son posibles. Los patrones viven en un fichero de datos (`detect_patterns.toml`), así un formato nuevo se añade sin tocar código.

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

Las claves de gestión de xAI no tienen prefijo documentado: el alta permite marcarlas como tales y se validan con `GET /auth/management-keys/validation`.

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
| Google Gemini | `GET /v1beta/models` | Sin endpoint; el prepago avisa con un 429 | No expuesta | `generateContent`, `maxOutputTokens: 1` |
| xAI | `GET /v1/api-key` | Saldo prepago con clave de gestión | `expireTime` con clave de gestión | `POST /v1/chat/completions` mínima |
| OpenRouter (fase 4) | `GET /api/v1/key` | `limit_remaining` con la propia clave | `expires_at` con la propia clave | No hace falta |

### Anthropic

- Base `https://api.anthropic.com`, autenticación con `x-api-key` (la API también acepta `Authorization: Bearer`). La cabecera `anthropic-version` va en configuración: por defecto `2026-10-21`, publicada el 21 de octubre de 2026; `2023-06-01` sigue funcionando. La versión nueva rechaza `temperature`, `top_p` y `top_k`, que la sonda no envía.
- **Pasiva:** `GET /v1/models`. 200 es válida (guardar nº de modelos y la cabecera `anthropic-organization-id` si viene), 401 inválida, 403 sin permiso. No informa de saldo.
- **Activa:** `POST /v1/messages` con el modelo más barato que liste `/v1/models` (configurable) y `max_tokens: 1`. Un 400 cuyo mensaje contenga «credit balance» o «insufficient credits» es saldo `empty`. Leer `anthropic-ratelimit-requests-*`, `anthropic-ratelimit-input-tokens-*` y `anthropic-ratelimit-output-tokens-*` (limit, remaining, reset). Verificar si `POST /v1/messages/count_tokens`, que es gratuito, devuelve el mismo error de saldo; si lo hace, pasa a ser la sonda por defecto.
- **Admin key:** `GET /v1/organizations/api_keys`, paginado con `after_id` y `limit` hasta 1000. Cada clave trae `status` (`active`, `inactive`, `archived`, `expired`), `expires_at`, `partial_key_hint`, `scope` (con `2026-10-21` sustituye a `workspace_id`) y `created_by`. Mecha cruza `partial_key_hint` con las claves guardadas para rellenar estado y caducidad.
- **OAuth `sk-ant-oat01-`:** sin llamadas remotas (principio 4).
- **Saldo:** no hay endpoint; se admite un presupuesto manual.

### OpenAI

- Base `https://api.openai.com/v1`, autenticación con `Authorization: Bearer`.
- **Pasiva:** `GET /v1/models`. 200 es válida, 401 (`invalid_api_key`) inválida (verificar si una clave caducada devuelve un código propio). Este endpoint no comprueba la cuota, así que no detecta falta de saldo.
- **Activa:** `POST /v1/embeddings` con el modelo de embeddings más barato disponible y una sola palabra. 429 `insufficient_quota` es saldo `empty`; 429 `rate_limit_exceeded` es `throttled`; un 403 por permisos del proyecto es «sonda no permitida» y no invalida la clave. Leer `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`, `x-ratelimit-limit-tokens`, `x-ratelimit-remaining-tokens` y `x-ratelimit-reset-*`.
- **Caducidad:** desde el 10 de septiembre de 2026 las project keys pueden caducar y la organización puede imponer una vida máxima. Solo es visible con admin key.
- **Admin key (`sk-admin-`):** `GET /v1/organization/projects` y, por proyecto, `GET /v1/organization/projects/{project_id}/api_keys`, que devuelve `expires_at` (null si no caduca), `last_used_at`, `redacted_value`, `owner` y `owner_project_access`. `GET /v1/organization/admin_api_keys` da lo mismo para las admin keys. Mecha cruza `redacted_value` con las claves guardadas.
- **Saldo:** no hay endpoint; el soporte de OpenAI lo confirmó en septiembre de 2026 y remite a la Costs API. Con admin key y un presupuesto manual (importe y fecha de compra), Mecha estima el saldo restando `GET /v1/organization/costs` desde esa fecha, y lo marca como `inferred`.
- **OAuth de ChatGPT (Codex):** solo JWT local.

### Google Gemini

- Base `https://generativelanguage.googleapis.com/v1beta`, con la clave siempre en la cabecera `x-goog-api-key` y nunca en la URL, para que no acabe en logs.
- **Tipos de clave:** desde el 28 de mayo de 2026 AI Studio solo crea auth keys (`AQ.`), ligadas a una cuenta de servicio. Google bloquea las standard keys (`AIza`) sin restringir y está retirando el resto; fuentes de terceros sitúan el corte total en septiembre de 2026. Toda `AIza` lleva aviso ámbar aunque responda 200.
- **Pasiva:** `GET /v1beta/models`. 200 es válida; 400 `API_KEY_INVALID` inválida; 403 `PERMISSION_DENIED` sin permiso o API no habilitada; un mensaje «reported as leaked» es `blocked` por filtración. Un 401 `ACCESS_TOKEN_TYPE_UNSUPPORTED` con una clave `AQ.` es un problema de cuenta conocido desde julio de 2026: se muestra como rechazada con enlace a la explicación.
- **Activa:** `POST /v1beta/models/{modelo}:generateContent` con el modelo más barato disponible (configurable) y `maxOutputTokens: 1`. 429 `RESOURCE_EXHAUSTED` con «prepayment credits are depleted» es saldo `empty`; con el mensaje de tope de gasto mensual es `capped` (verificar texto exacto); con detalles de cuota (`QuotaFailure`) es `throttled`, y si el `quotaId` contiene `FreeTier` se marca plan gratuito como `inferred`. Verificar si `countTokens`, que es gratuito, refleja el agotamiento del prepago.
- **Saldo:** el prepago existe desde abril de 2026, pero sin API de lectura: `unknown` salvo que un error diga otra cosa.
- **Caducidad:** no expuesta; fecha manual opcional.

### xAI

- Base `https://api.x.ai/v1`, autenticación con `Authorization: Bearer`.
- **Pasiva:** `GET /v1/api-key` describe la propia clave: `api_key_blocked`, `api_key_disabled`, `team_blocked`, `acls`, `name`, `create_time`, `team_id`, `api_key_id` y `redacted_api_key`. Cualquier flag a `true` es `blocked`; `team_blocked` se muestra como «equipo bloqueado», porque la API no dice el motivo.
- **Activa (opcional):** `POST /v1/chat/completions` mínima. Con el prepago agotado y el límite de facturación mensual a 0 USD, xAI rechaza las peticiones; el texto exacto del error está por verificar y se mapea a `empty`.
- **Clave de gestión:** base `https://management-api.x.ai`, autenticación con `Authorization: Bearer`. `GET /auth/management-keys/validation` la valida. `GET /auth/teams/{teamId}/api-keys` lista las claves con `expireTime`, `disabled`, `aclStrings` y `createTime`; un `expireTime` pasado significa clave rechazada. Bajo `/v1/billing/teams/{team_id}/` están `prepaid/balance` (`total.val`, céntimos de USD como string), `postpaid/spending-limits` y `postpaid/invoice/preview` (`prepaidCredits`, `prepaidCreditsUsed`, límite efectivo). La doc no explicita el método HTTP de estas lecturas: verificar.
- `GET /v1/api-key` devuelve gratis el `team_id`, así que una clave de gestión enriquece sola a todas las claves de su equipo, cruzando por `api_key_id`.

### OpenRouter (fase 4)

- `GET https://openrouter.ai/api/v1/key` con la propia clave devuelve `limit`, `limit_remaining`, `usage`, `usage_daily`, `usage_weekly`, `usage_monthly`, `is_free_tier`, `is_management_key`, `expires_at` y `effective_budget`. Es el único proveedor que da saldo y caducidad sin clave especial.

### Mapa de errores

Cada proveedor tiene su `errors.py` con este mapeo, probado con fixtures; los mensajes se comparan por subcadena, sin distinguir mayúsculas.

| Proveedor | Señal | Estado en Mecha |
| --- | --- | --- |
| Todos | 401 en el endpoint de validación | Validez `invalid` |
| Todos | Timeout, error de red o 5xx | Gris; 3 reintentos con backoff exponencial |
| Anthropic | 400 con «credit balance» o «insufficient credits» | Saldo `empty` |
| Anthropic (admin) | `status` `inactive` / `archived` / `expired` | Validez `blocked` / `revoked` / caducidad `expired` |
| OpenAI | 429 `insufficient_quota` | Saldo `empty` |
| OpenAI | 429 `rate_limit_exceeded` | Límites `throttled` |
| Gemini | 400 `API_KEY_INVALID` | Validez `invalid` |
| Gemini | «reported as leaked» | Validez `blocked` |
| Gemini | 429 «prepayment credits are depleted» | Saldo `empty` |
| Gemini | 429 de tope de gasto mensual | Saldo `capped` |
| xAI | `api_key_disabled`, `api_key_blocked` o `team_blocked` a `true` | Validez `blocked` |
| xAI (gestión) | `expireTime` en el pasado | Caducidad `expired` |
| OpenRouter | `limit_remaining` ≤ 0 | Saldo `empty` |

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
| Datos en el frontend | TanStack Query, y EventSource para el progreso |
| Tablas y gráficos | TanStack Table y los charts de shadcn/ui (Recharts) |
| Iconos, animación y avisos | lucide-react, Motion (con moderación) y sonner |

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

    async def check_passive(self, cred: Credential, http: httpx.AsyncClient) -> CheckResult: ...
    async def check_active(self, cred: Credential, http: httpx.AsyncClient) -> CheckResult: ...
    async def enrich(
        self, admin: Credential, children: list[Credential], http: httpx.AsyncClient
    ) -> dict[CredentialId, Enrichment]: ...
```

La detección no está en el plugin: la resuelve el núcleo con `detect_patterns.toml`. Un plugin nunca lanza excepciones crudas; devuelve señales, y un fallo de red es un `CheckResult` gris.

**Flujo de una comprobación**

1. El planificador o el usuario pide comprobar una credencial.
2. El núcleo descifra el secreto en memoria y elige comprobación pasiva o activa según su configuración.
3. El plugin hace una única petición al host permitido de su proveedor y devuelve señales.
4. El motor de estados calcula el semáforo, guarda el resultado y lo emite por SSE.
5. Si hay una clave de administración o gestión vinculada, su enriquecimiento se fusiona con `source: api`.

Concurrencia: 4 comprobaciones a la vez en total y 2 por proveedor, con un intervalo mínimo de 5 minutos por credencial. El planificador lanza comprobaciones pasivas cada 6 horas por defecto.

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

class Signal(BaseModel, Generic[T]):
    value: T
    source: Literal["api", "header", "jwt", "file", "manual", "inferred"]
    observed_at: datetime

class CheckResult(BaseModel):
    credential_id: str
    checked_at: datetime
    mode: Literal["offline", "passive", "active", "enrich"]
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
| GET | `/api/events` | SSE con el progreso de las comprobaciones |
| GET | `/api/health` | Estado del servidor |

Los errores tienen la forma `{code, message}` y nunca incluyen el secreto ni cabeceras de autorización.

## Seguridad

Mecha guarda secretos de alto valor, así que el diseño asume que cualquier web abierta en el navegador puede intentar hablar con él.

- **Cifrado en reposo.** Cada secreto se cifra con AES-256-GCM. La clave maestra vive en el llavero del sistema vía `keyring`; si no hay llavero (Linux sin escritorio), se deriva de una frase de paso con scrypt o Argon2id y se pide al arrancar.
- **Disco.** La base de datos está en el directorio de datos de usuario (platformdirs) con permisos 0600. Ningún secreto queda en claro en disco, logs ni historial.
- **Solo localhost.** El servidor escucha en 127.0.0.1 y rechaza cualquier cabecera `Host` distinta de `127.0.0.1:<puerto>` o `localhost:<puerto>`, contra DNS rebinding.
- **Token de sesión.** Cada arranque genera un token aleatorio y la CLI abre `http://127.0.0.1:7878/#t=<token>`. El frontend lo lee del fragmento, lo borra de la barra de direcciones y lo envía en `Authorization`. Sin token, 401; sin CORS.
- **CSP estricta.** El HTML servido no carga nada de terceros: fuentes e iconos van empaquetados.
- **Sin vuelta atrás.** Tras el alta, el frontend solo ve `redacted` y `fingerprint`. En la v1 no existe «mostrar clave».
- **Logs.** Un filtro de logging sustituye cualquier coincidencia de los patrones de detección por su forma redactada, y httpx nunca registra cabeceras.
- **Salidas controladas.** Cada plugin declara su lista de hosts permitidos y el cliente HTTP rechaza cualquier otro destino.
- **Ficheros vinculados.** Solo lectura; los tokens se leen en cada comprobación y nunca se copian.
- **Copias.** `mecha export` produce un fichero cifrado con frase de paso; no hay export en claro.

## UI/UX

La pantalla principal responde «¿qué está roto o va a romperse?» en menos de dos segundos; todo el detalle vive en un panel lateral.

**Dashboard**

- Cabecera con cuatro contadores (verde, ámbar, rojo, gris) animados con un number ticker, el botón «Comprobar todo» con progreso en vivo por SSE y la hora de la última comprobación global.
- Barra de filtros por proveedor, estado, tipo y etiqueta, con búsqueda. ⌘K abre una paleta de comandos (Command de shadcn/ui) para saltar a una clave o lanzar acciones; `N` abre el alta.
- Rejilla de tarjetas con vista alternativa en tabla. Orden por defecto: rojo, ámbar, gris y verde, y dentro de cada grupo, lo que caduca antes.

**Tarjeta de credencial**

- Icono del proveedor, etiqueta, clave redactada en monoespaciada e insignia de tipo (API key, Admin, Gestión, OAuth).
- Píldora de estado con icono y texto, nunca solo color.
- La mecha, una línea de saldo («12,40 USD», «Sin saldo», «No disponible por API»), una línea de límites y «comprobada hace 3 h».
- Acciones: comprobar, sonda activa (con su coste estimado en el tooltip) y abrir detalle.
- Las tarjetas rojas llevan un borde animado sutil; el resto, quietas.

**La mecha, componente firma**

- Barra horizontal que se consume según el tiempo restante hasta caducar; si no hay caducidad, según el saldo restante frente al presupuesto.
- Una chispa animada marca el extremo que arde, y el color pasa de verde a ámbar y a rojo con los umbrales del semáforo.
- Sin caducidad ni saldo conocidos: barra punteada gris con el texto «sin fecha conocida».
- Respeta `prefers-reduced-motion`: sin chispa ni animaciones.

**Panel de detalle (Sheet)**

- Todas las señales con un chip de fuente: «API», «JWT», «fichero», «manual» o «inferido».
- Límites de uso, datos de cuenta u organización y, si aplica, la clave de administración que la enriquece.
- Historial con una franja de estados y una sparkline de latencia.
- Cabeceras de la última respuesta, ya redactadas y plegadas por defecto, y notas editables.

**Alta (Dialog)**

- Al pegar el secreto, `/api/detect` muestra al instante proveedor, tipo y qué se podrá saber de él («validez: sí · saldo: no · caducidad: con admin key»).
- Campos: etiqueta, etiquetas, caducidad o presupuesto manual y casilla de sonda activa con su coste estimado.
- Pestañas alternativas para importar un `.env` o vincular un fichero de sesión; si existen las rutas por defecto, se ofrecen directamente.

**Estilo**

- Tema oscuro por defecto con conmutador; estética de herramienta para desarrolladores, densa y sobria, al estilo de Linear o Vercel.
- Geist Sans y Geist Mono, empaquetadas; un único color de acento (el ámbar de una mecha encendida) y los colores de estado reservados para el estado.
- Contraste AA, foco visible y uso completo con teclado.
- Textos en español e inglés según el idioma del navegador.
- Modo demo (`mecha serve --demo`) con datos falsos, para capturas, el README y vídeos.

## CLI

La CLI comparte núcleo con la web y sirve para cron, CI y para quien vive en la terminal.

| Comando | Qué hace |
| --- | --- |
| `mecha` o `mecha serve` | Arranca el servidor y abre el navegador; `--port`, `--no-open`, `--demo` |
| `mecha add` | Alta interactiva con entrada oculta; `--label`, `--tag` |
| `mecha ls` | Tabla con semáforo, tipo, caducidad, saldo y última comprobación |
| `mecha check [ID…]` | Comprueba; `--active` para sondas, `--json` para scripts, `--strict` para tratar el gris como fallo |
| `mecha detect` | Clasifica un secreto leído de stdin, sin red ni guardado |
| `mecha import .env` | Importa `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY` y cualquier valor con prefijo reconocido |
| `mecha link <herramienta>` | Vincula el fichero de sesión de `codex`, `claude-code` o `gemini-cli`; `--path` para rutas propias |
| `mecha rm ID` | Borra una credencial y su historial |
| `mecha export` y `mecha restore` | Copia cifrada con frase de paso |

`check` y `ls` salen con 0 si todo está verde, gris o solo local, 1 si hay algún ámbar, 2 si hay algún rojo y 3 si falla Mecha.

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

- [ ] `uv run mecha serve` sirve una página vacía protegida por token de sesión.
- [ ] `uv run pytest` y el build del frontend pasan en CI.

### Fase 1 · Núcleo offline

Modelos, detección, decodificación JWT, almacén cifrado y CLI con `add`, `ls`, `detect` y `rm`.

- [ ] Cada patrón de la tabla de detección tiene test positivo y negativo.
- [ ] Una clave canario no aparece en claro en el SQLite ni en los logs.

### Fase 2 · Comprobaciones pasivas

Plugins de Anthropic, OpenAI, Gemini y xAI con su endpoint gratuito, motor de estados, historial y planificador.

- [ ] Cada fila del mapa de errores tiene una fixture que produce el estado esperado.
- [ ] `mecha check --json` devuelve un esquema estable y los códigos de salida definidos.

### Fase 3 · Dashboard

Tarjetas, mecha, filtros, paleta ⌘K, panel de detalle, alta con detección en vivo, SSE y modo demo.

- [ ] Con 20 credenciales demo, la pantalla carga en menos de 1 s y se puede usar solo con teclado.
- [ ] Ningún estado se comunica solo con color.

### Fase 4 · Profundidad

Sondas activas, claves de administración y gestión con enriquecimiento, ficheros vinculados, importación de `.env`, OpenRouter y saldo estimado de OpenAI.

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
