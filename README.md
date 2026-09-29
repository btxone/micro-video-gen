# H3 Food Image-to-Video API

Proyecto FastAPI independiente para el flujo:

`imagen principal obligatoria → Gemini describe → Nano Banana mejora → Gemini verifica → MiniMax H3 Ref2VA`

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

El flujo usa siempre Ref2VA. La imagen principal se mejora y se envía como `<Picture 1>`; es la
autoridad absoluta para la comida, el plato y la composición. Las referencias opcionales se envían
en el orden recibido como `<Picture 2>` a `<Picture 5>` y sólo se usan como `weak_reference`. No
pasan por Nano Banana ni pueden reemplazar o modificar lo visible en la imagen principal.

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

