"""
Script de validação do WeatherBot.
Execute antes de rodar a aplicação para verificar chaves e conexões:

    python check_setup.py

Usa as MESMAS funções da aplicação (main.py), então o que passar aqui funciona no app.
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PLACEHOLDERS = ("COLE_SUA_CHAVE", "sua_chave")


def check_key(label: str) -> bool:
    value = os.getenv(label, "")
    if not value or any(p in value for p in PLACEHOLDERS):
        print(f"  ❌ {label}: NÃO configurada")
        return False
    print(f"  ✅ {label}: configurada ({value[:6]}...)")
    return True


async def main():
    print("\n🔍 WeatherBot — Verificação de Configuração\n" + "=" * 45)

    print("\n📋 Chaves no arquivo app/.env:")
    keys_ok = check_key("GEMINI_API_KEY") & check_key("OPENWEATHER_API_KEY")
    if not keys_ok:
        print("\n⚠️  Preencha as chaves no arquivo app/.env e execute novamente.")
        sys.exit(1)

    sys.path.insert(0, str(BASE_DIR))
    import main as app_main  # importa só depois de validar as chaves

    all_ok = True
    print("\n🌐 Testando as ferramentas (OpenWeatherMap):")

    current = await app_main.execute_tool("get_weather", {"city": "London"})
    if "error" in current:
        print(f"  ❌ get_weather: {current['error']}")
        all_ok = False
    else:
        print(f"  ✅ get_weather OK — {current['city']}: {current['temperature']}{current['unit_symbol']}, "
              f"{current['description']}")

    forecast = await app_main.execute_tool("get_forecast", {"city": "London", "days": 2})
    if "error" in forecast:
        print(f"  ❌ get_forecast: {forecast['error']}")
        all_ok = False
    else:
        resumo = ", ".join(f"{d['label']} {d['temp_min']}–{d['temp_max']}{forecast['unit_symbol']}"
                           for d in forecast["days"])
        print(f"  ✅ get_forecast OK — {forecast['city']}: {resumo}")

    print("\n🤖 Testando o LLM (Google Gemini):")
    try:
        resp = await app_main.call_gemini([{"role": "user", "parts": [{"text": "Responda apenas: OK"}]}])
        texto = app_main.extract_text(resp)
        modelo = resp.get("modelVersion", "?")
        print(f"  ✅ Gemini OK — modelo {modelo}, resposta: {texto[:40]!r}")
    except Exception as e:
        print(f"  ❌ Gemini: {e}")
        all_ok = False

    print("\n" + "=" * 45)
    if all_ok:
        print("🚀 Tudo certo! Na raiz do projeto, execute: python run.py")
        print("   Depois acesse: http://localhost:8000\n")
    else:
        print("⚠️  Corrija os erros acima antes de iniciar a aplicação.\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
