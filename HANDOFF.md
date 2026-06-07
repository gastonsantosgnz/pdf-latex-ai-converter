# Contexto y tarea: continuar el repo "pdf-latex-ai-converter"

> Prompt de continuación para una nueva sesión desde cero. Cópialo tal cual al
> iniciar el chat, o pásalo como contexto.

## Quién eres / cómo responder
- Respóndeme siempre en español.
- Sistema operativo: Windows 10/11, shell PowerShell. Rutas con backslash.
- Aplica soluciones profesionales y elegantes (repo open source serio). Si tienes dudas, pregúntame.

## Qué es el proyecto
Repo open source llamado **pdf-latex-ai-converter**, ubicado en:
`C:\Users\HP\Desktop\pdf-latex-ai-converter`  (¡un solo guion en el nombre!)

Es una herramienta que convierte PDFs (sobre todo de matemáticas) a **LaTeX página por página**
usando un LLM con visión (OpenAI GPT-4o por defecto). Detecta y reconstruye ecuaciones,
tablas (`tabular`), alineaciones algebraicas, diagramas (`tikz`), figuras didácticas, etc.,
preservando el idioma original. Luego ensambla todo en un `.tex` monolítico, permite separar
por capítulos y compilar a PDF con pdflatex.

Nació de un proyecto sucio en `C:\Users\HP\Desktop\mate_01_saenz` (scripts por libro:
convertir_*.py, lexoid_worker.py, separar_por_capitulos*.py, fix_*.py). Eso se refactorizó
en un paquete genérico e instalable. NO trabajar sobre mate_01_saenz; ese fue solo el origen.

## Estructura actual del repo (ya creada y verificada)
```
pdf-latex-ai-converter/
├── README.md                 # guía de uso, SIN emojis (mantener así)
├── ROADMAP.md                # features 1-4 (1 = in progress, 2-4 = planned)
├── HANDOFF.md                # este documento
├── LICENSE                   # MIT
├── .gitignore                # ignora .env, /output/*, /sources/*, venv, aux LaTeX
├── .env.example              # OPENAI_API_KEY, PDF2LATEX_MODEL
├── requirements.txt
├── pyproject.toml            # instalable; comando `pdf2latex`; deps dev (ruff, pytest);
│                             # [tool.ruff] + [tool.ruff.lint] ignore=["E501","B008"];
│                             # [tool.pytest.ini_options] pythonpath=["src"], testpaths=["tests"]
├── examples/chapters.example.json
├── sources/.gitkeep          # el usuario suelta PDFs aquí
├── output/.gitkeep           # salida generada (git-ignored)
├── .github/workflows/ci.yml  # CI: job lint (ruff check) + job smoke (py 3.10/3.11/3.12)
└── src/pdf2latex/
    ├── __init__.py           # __version__ = "0.1.0"
    ├── __main__.py           # python -m pdf2latex
    ├── cli.py                # subcomandos: list, convert, assemble, split, compile
    ├── layout.py             # PROJECT_ROOT (parents[2]), slugify(), BookPaths, resolve_source()
    ├── prompts.py            # BASE_SYSTEM_PROMPT + perfiles PROFILES={"default","dense"}
    ├── worker.py             # render_page_to_base64(), convert_image_b64(), make_client(), CLI 1-página
    ├── converter.py          # convert_pdf(): lotes, resumible, ensambla al final
    ├── assemble.py           # assemble_monolith(), write_standalone() (plantilla con marcadores)
    ├── splitter.py           # split_from_config() (JSON) y split_auto() (por blank-page)
    ├── compile.py            # compile_pdf() con pdflatex x2
    └── templates/standalone.tex  # preámbulo + portada + TOC + \capitulo; placeholders @@TITLE@@/@@SUBTITLE@@/@@BODY@@
```

Convenciones clave:
- Salida por libro en `output/<slug>/` con `pages/`, `<slug>.tex` (monolito), `<slug>-standalone.tex`,
  `chapters/`, `log.txt`. El slug sale de `slugify(nombre_pdf)`.
- Marcadores entre páginas en el monolito: `% ===== Page N =====`.
- Bloque de capítulos en el standalone entre `% --- chapters begin (pdf2latex) ---` y `% --- chapters end (pdf2latex) ---`.
- Perfiles: `default` y `dense` (libros densos: omite fotos decorativas, prioriza tablas/diagramas, sin colores decorativos).
- Dependencias: openai, pypdf, pypdfium2, pillow, python-dotenv. Dev: ruff, pytest.

## Estado de git
- `git init` ya hecho dentro de la carpeta; hubo un primer staging.
- Tras renombrar la carpeta y añadir ROADMAP/CI/pyproject, hay cambios SIN commitear todavía.
- NO se ha hecho ningún commit final ni push. NO existe repo remoto aún.

## Lo que YA está hecho en la sesión anterior
1. Estructura completa del repo + README sin emojis + LICENSE MIT.
2. ROADMAP.md con features 1-4 detalladas.
3. Feature 1 (CI) implementada: `.github/workflows/ci.yml` (lint con ruff + smoke multi-Python).
   - Se quitó a propósito el paso `ruff format --check` para no arriesgar CI rojo sin poder
     formatear localmente. Queda `ruff check` como puerta.
4. pyproject.toml con grupo dev, config ruff y pytest. Se corrigió orden de imports en worker._cli.

## BLOQUEO de la sesión anterior
La terminal/shell del entorno quedó inoperante (todo comando devolvía "no exit status", incluso
un echo). Por eso NO se pudo: (a) correr `ruff check .` localmente, ni (b) hacer push a GitHub
(tampoco respondió `gh auth status`, así que no se conoce el usuario de GitHub).

## TAREA EN ESTA SESIÓN (en orden)
1. Verifica que el shell responde con un comando trivial. Si no, pídeme reiniciar terminal/IDE.
2. Verifica lint y que el paquete carga:
   - `cd C:\Users\HP\Desktop\pdf-latex-ai-converter`
   - `pip install -e ".[dev]"`
   - `ruff check .`  (arregla cualquier hallazgo de forma mínima y elegante)
   - `python -m pdf2latex --help`  y  `python -m pdf2latex list`
3. Sube a MI GitHub como repo PÚBLICO llamado `pdf-latex-ai-converter`:
   - Confirma cuenta con `gh auth status` (si hay varias, pregúntame cuál).
   - `git add -A`
   - `git commit -m "Add CI workflow, roadmap, and dev tooling"`
   - `gh repo create pdf-latex-ai-converter --public --source=. --remote=origin --push`
   - Después del push, añade el badge de CI al README (con el owner real) y commitea/push ese cambio.
   - Devuélveme la URL del repo.
4. Cuando el push esté confirmado y CI en verde, dime y seguimos con las features 2, 3 y 4 del ROADMAP,
   EN ESTE ORDEN y por separado (no todas de golpe):
   - Feature 2: suite de tests con pytest (mockeando el cliente OpenAI). Probar slugify, resolve_range,
     strip_code_fences, iter_page_files (orden), split_from_config con un monolito sintético. Cobertura >=80%.
     Conectar pytest al CI.
   - Feature 3: archivos de comunidad (CONTRIBUTING.md, CODE_OF_CONDUCT.md, CHANGELOG.md,
     .github/ISSUE_TEMPLATE/, PULL_REQUEST_TEMPLATE.md), metadatos ricos en pyproject (authors, keywords,
     classifiers, project.urls) y badges en README.
   - Feature 4: estimación de costo + `--dry-run` + `--yes/-y` en el subcomando `convert`; resumen previo
     (nº páginas en rango, modelo, perfil, costo aprox con tabla de precios configurable y disclaimer).

## Reglas de trabajo
- Solo commitear/push cuando yo lo pida (ya lo pedí para el paso 3).
- README sin emojis.
- Mantén las decisiones de diseño existentes (slug, marcadores de página/capítulos, perfiles).
- Si el shell vuelve a fallar, dímelo claramente y dame los comandos manuales como respaldo.

Empieza por el paso 1.
