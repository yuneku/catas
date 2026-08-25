# -*- coding: utf-8 -*-
"""Analiza un screenshot con el modelo de visión LOCAL (Ollama qwen-local-vision)
usando la API NATIVA /api/chat con los parámetros recomendados por Unsloth para
Qwen3.5 Small (non-thinking / instruct, tareas generales):

    temperature=0.7, top_p=0.8, top_k=20, min_p=0.0,
    presence_penalty=1.5, repeat_penalty=1.0, num_predict=2048, think=False

Uso: python _vision_local.py <ruta_imagen.png> ["instrucción extra"]
"""
import sys, base64, json, urllib.request, urllib.error, time

OLLAMA = "http://localhost:11434"
MODELO = "qwen-local-vision"

PARAMS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
    "num_predict": 2048,
}


def _prompt_base(extra: str = "") -> str:
    p = (
        "Eres un experto en UI/UX mobile-first. Analiza esta captura de una app web "
        "Streamlit de catas de cannabis (tema oscuro). Responde CONCISO en español: "
        "1) elementos visibles (encabezado, tarjetas, filtros, notas), "
        "2) problemas visuales concretos (alineación, contraste, solapamientos, "
        "elementos cortados o rotos, scroll, tamaños), 3) sugerencias accionables "
        "(colores, tamaños, jerarquía, espaciado). Máximo 180 palabras."
    )
    if extra:
        p += "\n\nContexto adicional: " + extra
    return p


def _nativo(ruta_img: str, extra: str) -> str:
    """API nativa de Ollama: full control de opciones (top_k, min_p, presence_penalty)
    y 'think': False — el modo correcto para Qwen3.5 Small."""
    with open(ruta_img, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    body = {
        "model": MODELO,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": _prompt_base(extra),
                      "images": [b64]}],
        "options": dict(PARAMS),
    }
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return (resp.get("message", {}).get("content") or "").strip()


def _openai_compat(ruta_img: str, extra: str) -> str:
    """Fallback: endpoint OpenAI-compatible (sin top_k/min_p/presence_penalty).
    Requiere thinking:{type:disabled} + max_tokens amplio, o Qwen3.5 devuelve
    content vacío por razonar todo el presupuesto."""
    with open(ruta_img, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = "image/png" if ruta_img.lower().endswith(".png") else "image/jpeg"
    body = {
        "model": MODELO,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": _prompt_base(extra)},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64}"}}]}],
        "max_tokens": 2500,
        "temperature": 0.7,
        "top_p": 0.8,
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        f"{OLLAMA}/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"]["content"].strip()


def analizar(ruta_img: str, extra: str = ""):
    """Primero API nativa (parámetros completos); si falla, fallback OpenAI-compat.
    Reintenta ante respuestas vacías (Ollama a veces devuelve content vacío)."""
    for i in range(4):
        for fn in (_nativo, _openai_compat):
            try:
                out = fn(ruta_img, extra)
                if out:
                    return out
            except urllib.error.HTTPError as e:
                print(f"[vision] intento {i + 1} HTTP {e.code} ({fn.__name__})",
                      file=sys.stderr)
            except Exception as e:
                print(f"[vision] intento {i + 1} {fn.__name__}: "
                      f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            time.sleep(0.6)
    return "(el modelo local de visión no respondió)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python _vision_local.py <imagen> [extra]")
        sys.exit(1)
    extra = sys.argv[2] if len(sys.argv) > 2 else ""
    print(analizar(sys.argv[1], extra))
