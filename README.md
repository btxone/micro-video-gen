# micro-video-gen

## H3 Food Image-to-Video API

Proyecto FastAPI independiente para el flujo:

`imagen principal → master publicitaria → referencias normalizadas → QC de imágenes → H3 Ref2VA → QC orbital`

El proyecto es exclusivamente image-to-image. No genera imágenes desde texto y no requiere
`OPENAI_API_KEY`.

## Estructura

- `app/prompts.py`: todos los prompts, esquemas y la plantilla H3, con comentarios sobre su rol.
- `app/clients/gemini.py`: análisis visual y control de preservación.
- `app/clients/runpod.py`: Nano Banana 2 Edit y MiniMax H3 Serverless.
- `app/services/pipeline.py`: paralelismo, reintento estricto, render H3 y coordinación.
- `app/main.py`: API FastAPI y publicación de artefactos.
- `app/api/jobs.py`: API asíncrona `/v2/jobs` con idempotencia, polling, retry y cancelación.
- `app/domain/` + `app/db.py`: jobs, etapas, artefactos, generaciones, prompts y costes.
- `app/storage/`: backend local y adaptador S3/R2/MinIO.
- `app/tasks/`: ejecución local en background o Celery + Redis.

## Configuración

```powershell
Copy-Item .env.example .env
```

Completa `GEMINI_API_KEY` y `RUNPOD_API_KEY`. El modelo de visión por defecto es
`gemini-3.5-flash-lite`.

El modo de producción usa PostgreSQL, Redis y Celery (`CELERY_ENABLED=true`). Para desarrollo
local se puede usar SQLite y el ejecutor en background (`DATABASE_URL=sqlite:///./outputs/h3_api.db`
y `CELERY_ENABLED=false`). `PIPELINE_MODE=mock` activa el pipeline determinista de QA y no llama
a Gemini ni Runpod. `STORAGE_BACKEND=local` es el backend por defecto; `s3` acepta S3, R2 o MinIO
mediante las variables `S3_*`.

## Ejecución

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Ejecución con Docker

Primero crea el archivo local de secretos:

```powershell
Copy-Item .env.example .env
```

Completa `GEMINI_API_KEY` y `RUNPOD_API_KEY` en `.env`, y luego ejecuta:

```powershell
docker compose up --build
```

La API quedará disponible en `http://localhost:8000`. Los resultados se persisten en la carpeta
local `outputs/`, que se monta como `/app/outputs` dentro del contenedor. Las claves no se copian
a la imagen Docker.

Para detener el servicio:

```powershell
docker compose down
```

## Uso

```powershell
curl.exe -X POST http://localhost:8000/v1/generate-video `
  -F "image=@C:\ruta\comida.jpg" `
  -F "reference_images=@C:\ruta\referencia-1.jpg" `
  -F "reference_images=@C:\ruta\referencia-2.jpg" `
  -F "title_plate=Gnocchi al burro e salvia" `
  -F "description_plate=Gnocchi artesanales de papa con manteca noisette y hojas de salvia." `
  -F "image_edit_prompt=Improve lighting and shadows while preserving the food exactly."
```

`image`, `title_plate` y `description_plate` son obligatorios. Se pueden adjuntar entre cero y
cuatro `reference_images` repitiendo el mismo campo multipart. `image_edit_prompt` es opcional y
usa un prompt seguro por defecto.

El flujo usa siempre Ref2VA. La imagen principal crea la master publicitaria y define un único set
de estudio profesional. Cada referencia pasa por Nano Banana junto con la master como segunda
imagen: su comida y su ángulo permanecen bloqueados, mientras adopta solamente el fondo, superficie,
paleta, iluminación y acabado de la master. H3 recibe exclusivamente estas versiones normalizadas;
nunca mezcla una master profesional con una referencia cruda.

Antes de contactar Gemini o Runpod se conserva cada original sin cambios y se crea una copia JPEG
normalizada, con orientación EXIF aplicada, metadatos EXIF retirados y lado mayor limitado por
`PROVIDER_IMAGE_MAX_EDGE`. `reference_manifest.json` es la fuente canónica del orden: Picture 1 es
la principal y Pictures 2–5 son las referencias 1–4. La descripción de Gemini, la edición de Nano
Banana, el control de integridad y el arreglo `input.images` de H3 quedan asociados a esos mismos
índices; el payload de H3 se registra sin guardar sus imágenes base64 en
`*_payload_manifest.json`.

Las solicitudes multimodales de Gemini usan datos inline solo mientras el cuerpo serializado quede
bajo `GEMINI_INLINE_REQUEST_BUDGET_MB` (12 MiB por defecto). Por encima, todo el contenido multimedia
de esa solicitud se carga mediante Gemini Files API para no acercarse al límite de 20 MB de la
solicitud. Gemini conserva esos archivos temporales durante un máximo aproximado de 48 horas; el
servicio registra hash, tipo y tamaño en `gemini_file_uploads.json`, pero no persiste sus URI
temporales.

Cada referencia pasa por controles individuales y por un control visual conjunto del fondo,
iluminación y preservación de la comida antes de generar el video. Los artefactos asíncronos incluyen
`reference_index`, `picture_number` y el vínculo al artefacto original. Los manifiestos de estado de
Runpod guardan una clave de operación: si un worker se reinicia después de persistir el ID de Runpod,
un retry vuelve a consultar ese mismo trabajo y reutiliza el archivo final existente. No se puede
garantizar exactamente una ejecución si la conexión cae justo después de que Runpod acepta una
petición pero antes de que la API reciba y guarde su ID.

El prompt H3 describe una sola toma con órbita física horaria de 360 grados, checkpoints en
0/90/180/270/360 grados, radio, altura y focal constantes, plato fijo y regreso al encuadre inicial.
Gemini inspecciona el MP4 completo. Si no detecta la órbita, el fondo cambia, la comida se deforma o
el inicio y el final no coinciden, el pipeline genera un único reintento dirigido por las fallas del
QC. `VIDEO_MAX_ATTEMPTS` e `IMAGE_MAX_ATTEMPTS` permiten ajustar esos límites.

Gemini analiza conjuntamente la imagen principal y las referencias, pero utiliza el título y la
descripción solamente como contexto semántico. Si el texto del menú entra en conflicto claro con
la imagen principal, el job se detiene antes de enviar el video a MiniMax.

La respuesta devuelve URLs para la imagen original, la imagen mejorada, cada referencia y el
video; también incluye `video_mode: "ref2va"`, `reference_count`, el prompt H3, el contexto
normalizado del menú y el resultado de la verificación de integridad. Los diagnósticos quedan en
`outputs/`, incluyendo `request_metadata.json`.

## API asíncrona v2

Para no mantener una conexión HTTP abierta mientras Gemini, Nano Banana o H3 trabajan, la versión
asíncrona devuelve `202` y un `job_id`:

```powershell
curl.exe -X POST http://localhost:8000/v2/jobs `
  -H "Idempotency-Key: menu-item-001" `
  -F "image=@C:\ruta\comida.jpg" `
  -F "reference_images=@C:\ruta\referencia.jpg" `
  -F "title_plate=Gnocchi al burro e salvia" `
  -F "description_plate=Gnocchi artesanales con manteca noisette y salvia."
```

Luego consulta `GET /v2/jobs/{job_id}`. La respuesta incluye la etapa actual, el historial de
etapas y URLs de artefactos. También existen `GET /v2/jobs/{job_id}/artifacts`,
`POST /v2/jobs/{job_id}/retry` y `POST /v2/jobs/{job_id}/cancel`. La misma clave de idempotencia
devuelve el mismo job y evita duplicar llamadas a proveedores.

Las instalaciones existentes que ya usan una base de datos creada por la aplicación agregan la
columna de ordenamiento de forma aditiva al iniciar, sin borrar filas. Para una base nueva que se
administrará con Alembic, aplica las migraciones antes de iniciar la API. En una base ya poblada sin
`alembic_version`, respalda primero la base y marca la revisión inicial una sola vez antes de
actualizar:

```powershell
python -m alembic stamp 1cbcb4f45d3f
python -m alembic upgrade head
```

## Pruebas locales

```powershell
python -m pytest -q
```

La suite incluye QA sin Runpod. Para una prueba negra del API local, inicia el servidor con
`PIPELINE_MODE=mock` y usa:

```powershell
python scripts/qa_v2.py --base-url http://127.0.0.1:8000 --image "C:\ruta\principal.png"
```

Para validar directamente que el endpoint H3 acepta la imagen principal más cuatro referencias:

```powershell
python -m scripts.qa_ref2va `
  --primary "C:\ruta\principal.png" `
  --reference "C:\ruta\ref-1.jpg" `
  --reference "C:\ruta\ref-2.jpg" `
  --reference "C:\ruta\ref-3.jpg" `
  --reference "C:\ruta\ref-4.jpg" `
  --title "Gnocchi crocante" `
  --description "Gnocchi artesanales con presentación premium."
```

Esta prueba genera un video real y, por tanto, consume GPU del endpoint serverless.

Para probar el flujo real completo —mejora de principal, normalización de referencias, QC visual,
H3 y QC orbital— usa:

```powershell
python -m scripts.qa_full_pipeline `
  --image "C:\ruta\principal.jpg" `
  --reference "C:\ruta\referencia.jpg" `
  --title "Arroz con atún" `
  --description "Arroz con atún, queso parmesano, huevo revuelto y garbanzos."
```

Este QA usa endpoints reales y genera costes. Los archivos `image_integrity_*.json` y
`video_qc.json` explican exactamente por qué cada resultado fue aprobado o rechazado.

