"""
WeatherBot - Aplicação de consulta climática com LLM + Tool Calling
Tecnologias: FastAPI + Google Gemini REST API + OpenWeatherMap

Ferramentas disponíveis para o LLM:
  - get_weather  → condições climáticas ATUAIS de uma cidade
  - get_forecast → PREVISÃO do tempo para hoje e os próximos dias (até 5)
"""

import asyncio
import inspect
import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Configuração ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
# Modelos em ordem de preferência (fallback automático em caso de sobrecarga/indisponibilidade)
GEMINI_MODELS = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.8-flash", "gemini-flash-latest"]
GEMINI_REQUEST_TIMEOUT = 20.0   # segundos por tentativa
GEMINI_TOTAL_BUDGET = 45.0      # segundos no total, somando todas as tentativas de fallback
OWM_BASE_URL = "https://api.openweathermap.org/data/2.5"
OWM_TIMEOUT = 10.0

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY não encontrada. Verifique o arquivo .env")
if not OPENWEATHER_API_KEY:
    raise RuntimeError("OPENWEATHER_API_KEY não encontrada. Verifique o arquivo .env")

app = FastAPI(title="WeatherBot", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ============================================================
# DECLARAÇÃO DAS FERRAMENTAS (formato Gemini Function Calling)
# ============================================================

UNITS_PARAM = {
    "type": "STRING",
    "enum": ["metric", "imperial"],
    "description": "Unidade: 'metric' para Celsius (padrão), 'imperial' para Fahrenheit.",
}
CITY_PARAM = {
    "type": "STRING",
    "description": "Nome da cidade. Ex: 'São Paulo', 'London', 'New York'.",
}

WEATHER_TOOL_DECLARATION = {
    "name": "get_weather",
    "description": (
        "Obtém as condições climáticas ATUAIS (neste momento) de uma cidade: temperatura, "
        "sensação térmica, mínima/máxima, umidade, vento e descrição do céu. "
        "Use quando o usuário perguntar como está o tempo AGORA."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {"city": CITY_PARAM, "units": UNITS_PARAM},
        "required": ["city"],
    },
}

FORECAST_TOOL_DECLARATION = {
    "name": "get_forecast",
    "description": (
        "Obtém a PREVISÃO do tempo de uma cidade para hoje e os próximos dias (no máximo 5), "
        "com condição do céu, mínima/máxima, chance de chuva e volume de chuva por dia. "
        "Use quando a pergunta envolver o FUTURO: 'amanhã', 'depois de amanhã', 'fim de semana', "
        "'próximos dias', um dia da semana específico ou 'vai chover?'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": CITY_PARAM,
            "days": {
                "type": "INTEGER",
                "description": (
                    "Quantos dias à frente, contando a partir de hoje (1 a 5). "
                    "Ex.: 'amanhã' = 1, 'próximos 3 dias' = 3, 'fim de semana' = dias até domingo. Padrão: 3."
                ),
            },
            "units": UNITS_PARAM,
        },
        "required": ["city"],
    },
}


def build_system_instruction() -> str:
    """Monta a instrução de sistema com a data atual (necessária para entender 'amanhã', 'sábado' etc.)."""
    agora = datetime.now()
    hoje = f"{DIAS_SEMANA[agora.weekday()]}, {agora:%d/%m/%Y}"
    return f"""Você é o WeatherBot, um assistente especializado em informações climáticas.
Data de hoje: {hoje}.

Ferramentas disponíveis:
- get_weather: condições climáticas ATUAIS de uma cidade.
- get_forecast: PREVISÃO para hoje e os próximos dias (até 5 dias).

Regras:
1. Escolha a ferramenta pelo período da pergunta: presente/agora → get_weather; futuro (amanhã, fim de semana, próximos dias, "vai chover?") → get_forecast.
2. Nunca use dados atuais para responder sobre o futuro e nunca invente dados. Se o período pedido passar de 5 dias, explique que a previsão cobre no máximo 5 dias e mostre o que estiver disponível.
3. Se uma ferramenta retornar "error", explique o problema de forma amigável e sugira como o usuário pode corrigir.
4. Use exatamente os valores e as unidades retornados pelas ferramentas (não converta unidades).
5. Clima atual: mencione temperatura, sensação térmica, mínima/máxima, umidade e vento.
   Previsão: resuma dia a dia a condição, a mínima/máxima e a chance de chuva.
6. Termine com uma dica contextual curta (ex.: "Leve um guarda-chuva!").
7. Se a pergunta não for sobre clima, responda brevemente e lembre que sua especialidade é o clima.
8. Responda em português brasileiro, de forma concisa. Use **negrito** e listas com "* "; não use tabelas."""


# ============================================================
# FERRAMENTAS: consultas reais à API OpenWeatherMap
# ============================================================

def _normalize_units(units: str) -> str:
    return units if units in ("metric", "imperial") else "metric"


def _unit_info(units: str) -> tuple[str, str, float]:
    """Retorna (símbolo da temperatura, unidade do vento, fator de conversão do vento)."""
    if units == "imperial":
        return "°F", "mph", 1.0      # OpenWeatherMap já devolve mph
    return "°C", "km/h", 3.6         # OpenWeatherMap devolve m/s → converte para km/h


async def _owm_request(endpoint: str, city: str, units: str) -> tuple[dict | None, str | None]:
    """Faz a requisição à OpenWeatherMap e traduz os códigos HTTP em mensagens de erro amigáveis."""
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": units, "lang": "pt_br"}
    async with httpx.AsyncClient(timeout=OWM_TIMEOUT) as client:
        response = await client.get(f"{OWM_BASE_URL}/{endpoint}", params=params)

    if response.status_code == 200:
        return response.json(), None
    errors = {
        404: f"Cidade '{city}' não encontrada. Verifique o nome e tente novamente.",
        401: "Chave da API OpenWeatherMap inválida ou ainda não ativada.",
        429: "Limite de consultas da API de clima atingido. Aguarde um instante e tente novamente.",
    }
    return None, errors.get(response.status_code,
                            f"Erro ao consultar API de clima (HTTP {response.status_code}).")


async def get_weather(city: str, units: str = "metric") -> dict:
    """Condições climáticas atuais (endpoint /weather)."""
    units = _normalize_units(units)
    data, error = await _owm_request("weather", city, units)
    if error:
        return {"error": error}

    temp_symbol, wind_unit, wind_factor = _unit_info(units)
    return {
        "type": "current",
        "city": data["name"],
        "country": data["sys"]["country"],
        "temperature": round(data["main"]["temp"], 1),
        "feels_like": round(data["main"]["feels_like"], 1),
        "temp_min": round(data["main"]["temp_min"], 1),
        "temp_max": round(data["main"]["temp_max"], 1),
        "humidity": data["main"]["humidity"],
        "description": data["weather"][0]["description"],
        "wind_speed": round(data["wind"]["speed"] * wind_factor, 1),
        "wind_unit": wind_unit,
        "unit_symbol": temp_symbol,
        "icon": data["weather"][0]["icon"],
    }


async def get_forecast(city: str, days: int = 3, units: str = "metric") -> dict:
    """
    Previsão para hoje + próximos `days` dias (endpoint /forecast, intervalos de 3 horas).
    Os intervalos são agrupados por dia no fuso horário local da cidade.
    """
    units = _normalize_units(units)
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 3
    days = max(1, min(days, 5))

    data, error = await _owm_request("forecast", city, units)
    if error:
        return {"error": error}

    temp_symbol, wind_unit, wind_factor = _unit_info(units)
    tz_offset = timedelta(seconds=data["city"].get("timezone", 0))
    hoje = (datetime.now(timezone.utc) + tz_offset).date()

    # Agrupa os intervalos de 3h por data local da cidade
    grupos: dict = {}
    for item in data["list"]:
        local_dt = datetime.fromtimestamp(item["dt"], timezone.utc) + tz_offset
        grupos.setdefault(local_dt.date(), []).append((local_dt, item))

    dias = []
    for dia in sorted(grupos):
        offset = (dia - hoje).days
        if offset < 0 or offset > days:
            continue
        itens = grupos[dia]
        _, representativo = min(itens, key=lambda t: abs(t[0].hour - 12))  # mais próximo do meio-dia
        descricoes = Counter(i["weather"][0]["description"] for _, i in itens)

        if offset == 0:
            label = "Hoje"
        elif offset == 1:
            label = "Amanhã"
        else:
            label = DIAS_SEMANA[dia.weekday()].capitalize()

        dias.append({
            "label": label,
            "weekday": DIAS_SEMANA[dia.weekday()],
            "date": dia.strftime("%d/%m"),
            "description": descricoes.most_common(1)[0][0],
            "icon": representativo["weather"][0]["icon"],
            "temp_min": round(min(i["main"]["temp_min"] for _, i in itens), 1),
            "temp_max": round(max(i["main"]["temp_max"] for _, i in itens), 1),
            "rain_chance": round(max(i.get("pop", 0) for _, i in itens) * 100),
            "rain_mm": round(sum(i.get("rain", {}).get("3h", 0) for _, i in itens), 1),
            "humidity": round(sum(i["main"]["humidity"] for _, i in itens) / len(itens)),
            "wind_max": round(max(i["wind"]["speed"] for _, i in itens) * wind_factor, 1),
        })

    return {
        "type": "forecast",
        "city": data["city"]["name"],
        "country": data["city"].get("country", ""),
        "unit_symbol": temp_symbol,
        "wind_unit": wind_unit,
        "note": "A linha 'Hoje' considera apenas as horas restantes do dia.",
        "days": dias,
    }


# Registro central: nome da ferramenta → função + declaração enviada ao LLM
TOOLS = {
    "get_weather": {"fn": get_weather, "declaration": WEATHER_TOOL_DECLARATION},
    "get_forecast": {"fn": get_forecast, "declaration": FORECAST_TOOL_DECLARATION},
}
TOOL_DECLARATIONS = [t["declaration"] for t in TOOLS.values()]


async def execute_tool(name: str, args: dict | None) -> dict:
    """Valida a chamada pedida pelo LLM e executa a ferramenta correspondente."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"A ferramenta '{name}' não existe nesta aplicação."}

    args = args or {}
    city = str(args.get("city", "")).strip()
    if not city:
        return {"error": "Nenhuma cidade foi informada. Diga qual cidade deseja consultar."}

    accepted = inspect.signature(tool["fn"]).parameters
    kwargs = {k: v for k, v in args.items() if k in accepted}
    kwargs["city"] = city

    try:
        return await tool["fn"](**kwargs)
    except httpx.TimeoutException:
        return {"error": f"Tempo esgotado ao consultar a API de clima para '{city}'. Tente novamente."}
    except httpx.RequestError as e:
        return {"error": f"Falha de conexão com a API de clima ({e.__class__.__name__})."}
    except (KeyError, IndexError, TypeError, ValueError):
        return {"error": f"A API de clima retornou dados em formato inesperado para '{city}'."}


# ============================================================
# LÓGICA PRINCIPAL: Gemini REST API + Tool Calling
# ============================================================

# Modelos cuja cota acabou: nome → instante (time.monotonic) em que podem ser tentados de novo.
# Evita gastar tempo chamando um modelo que certamente responderá 429.
_quota_blocked_until: dict[str, float] = {}


def _describe_429(resp: httpx.Response) -> tuple[str, float]:
    """Lê o erro 429 do Gemini e devolve (descrição amigável, segundos até poder tentar de novo)."""
    try:
        details = resp.json().get("error", {}).get("details", [])
    except ValueError:
        details = []
    retry_s, per_day = 60.0, False
    for d in details:
        if d.get("@type", "").endswith("RetryInfo"):
            try:
                retry_s = float(str(d.get("retryDelay", "60s")).rstrip("s"))
            except ValueError:
                pass
        for v in d.get("violations", []):
            if "PerDay" in v.get("quotaId", ""):
                per_day = True
    if per_day:
        return f"cota diária esgotada (libera em ~{retry_s / 3600:.0f}h)", retry_s
    return "limite de requisições por minuto atingido", retry_s


async def call_gemini(contents: list, tools: list | None = None) -> dict:
    """Chama a Gemini REST API com fallback entre modelos, limitado a GEMINI_TOTAL_BUDGET segundos."""
    payload = {
        "system_instruction": {"parts": [{"text": build_system_instruction()}]},
        "contents": contents,
    }
    if tools:
        payload["tools"] = [{"function_declarations": tools}]
        payload["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}

    headers = {"x-goog-api-key": GEMINI_API_KEY}  # chave no header, não na URL
    deadline = time.monotonic() + GEMINI_TOTAL_BUDGET
    failures: list[str] = []

    async with httpx.AsyncClient() as client:
        for model in GEMINI_MODELS:
            if _quota_blocked_until.get(model, 0) > time.monotonic():
                failures.append(f"{model}: cota esgotada")
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                failures.append(f"{model}: não tentado (tempo máximo de espera excedido)")
                continue
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                resp = await client.post(url, json=payload, headers=headers,
                                         timeout=min(GEMINI_REQUEST_TIMEOUT, remaining))
            except httpx.TimeoutException:
                failures.append(f"{model}: sem resposta (timeout)")
                continue
            except httpx.RequestError as e:
                failures.append(f"{model}: falha de conexão ({e.__class__.__name__})")
                continue

            if resp.status_code == 200:
                return resp.json()
            # Cota/rate limit, modelo inexistente, sobrecarga ou erro do servidor → tenta o próximo modelo
            if resp.status_code == 429:
                reason, retry_s = _describe_429(resp)
                _quota_blocked_until[model] = time.monotonic() + retry_s
                failures.append(f"{model}: {reason}")
                continue
            if resp.status_code in (404, 500, 503, 504):
                reason = {404: "modelo não encontrado", 503: "sobrecarregado"}.get(
                    resp.status_code, f"erro do servidor (HTTP {resp.status_code})")
                failures.append(f"{model}: {reason}")
                continue
            # Demais erros (ex.: chave inválida, requisição malformada) são fatais
            raise RuntimeError(f"Gemini API erro {resp.status_code}: {resp.text[:300]}")

    raise RuntimeError("o serviço de IA (Google Gemini) está indisponível no momento. "
                       "Tente novamente em instantes. Detalhes: " + "; ".join(failures))


def _parts(gemini_response: dict) -> list:
    try:
        return gemini_response["candidates"][0]["content"].get("parts", [])
    except (KeyError, IndexError, TypeError):
        return []


def extract_text(gemini_response: dict) -> str:
    """Extrai o texto da resposta do Gemini."""
    return " ".join(p["text"] for p in _parts(gemini_response) if "text" in p).strip()


def extract_function_calls(gemini_response: dict) -> list[dict]:
    """Extrai todas as chamadas de função da resposta do Gemini (uma ou várias)."""
    return [p["functionCall"] for p in _parts(gemini_response) if "functionCall" in p]


async def process_with_llm(user_message: str) -> dict:
    """
    Orquestra: LLM interpreta → seleciona ferramenta(s) → executa → LLM formula resposta.
    Várias chamadas (ex.: 'clima em Recife e previsão para Curitiba') são executadas em paralelo.
    """
    contents = [{"role": "user", "parts": [{"text": user_message}]}]

    # --- Passo 1: LLM interpreta a pergunta e decide qual(is) ferramenta(s) usar ---
    try:
        response1 = await call_gemini(contents, tools=TOOL_DECLARATIONS)
    except Exception as e:
        return {"response": None, "tools_used": [], "tool_results": [],
                "error": f"Erro ao conectar ao LLM: {e}"}

    tool_calls = extract_function_calls(response1)

    if not tool_calls:
        # LLM respondeu diretamente, sem usar ferramenta
        final_text = extract_text(response1) or \
            "Desculpe, não entendi. Tente perguntar sobre o clima de uma cidade!"
        return {"response": final_text, "tools_used": [], "tool_results": [], "error": None}

    # --- Passo 2: executa as ferramentas escolhidas pelo LLM (em paralelo) ---
    tool_results = await asyncio.gather(
        *(execute_tool(fc.get("name", ""), fc.get("args")) for fc in tool_calls)
    )
    tools_used = list(dict.fromkeys(fc.get("name") for fc in tool_calls if fc.get("name") in TOOLS))

    response_parts = []
    for fc, result in zip(tool_calls, tool_results):
        f_resp = {"name": fc.get("name", ""), "response": {"result": result}}
        if "id" in fc:
            f_resp["id"] = fc["id"]
        response_parts.append({"functionResponse": f_resp})

    contents.append(response1["candidates"][0]["content"])  # turno do modelo com as chamadas
    contents.append({"role": "user", "parts": response_parts})  # resultados das ferramentas

    # --- Passo 3: LLM formula a resposta final com os dados reais ---
    try:
        response2 = await call_gemini(contents)
    except Exception as e:
        return {"response": None, "tools_used": tools_used, "tool_results": list(tool_results),
                "error": f"Erro ao formular resposta final: {e}"}

    final_text = extract_text(response2) or \
        "Consegui os dados, mas não consegui redigir a resposta. Confira os detalhes abaixo."
    return {"response": final_text, "tools_used": tools_used,
            "tool_results": list(tool_results), "error": None}


# ============================================================
# ROTAS DA API
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "JSON inválido."})

    try:
        if not isinstance(body, dict) or not isinstance(body.get("message", ""), str):
            return JSONResponse(status_code=400,
                                content={"error": "Formato inválido. Envie {\"message\": \"texto\"}."})
        user_message = body.get("message", "").strip()

        if not user_message:
            return JSONResponse(status_code=400, content={"error": "Mensagem não pode estar vazia."})
        if len(user_message) > 500:
            return JSONResponse(status_code=400,
                                content={"error": "Mensagem muito longa (máximo 500 caracteres)."})

        result = await process_with_llm(user_message)

        if result["error"] and not result["response"]:
            return JSONResponse(status_code=502, content={"error": result["error"]})
        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Erro interno: {e}"})


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version, "models": GEMINI_MODELS, "tools": list(TOOLS)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
