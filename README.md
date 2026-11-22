# WeatherBot 🌤️

Aplicação inteligente para consulta climática em linguagem natural desenvolvida com **FastAPI**, **Google Gemini (LLM com Tool Calling)** e a API externa real da **OpenWeatherMap**.

---

## 📂 Estrutura do Projeto

```
Atividade integradora do semestre/
├── app/
│   ├── main.py              # Backend FastAPI: ferramentas, LLM e Tool Calling
│   ├── check_setup.py       # Valida chaves e testa as ferramentas e o Gemini
│   ├── requirements.txt     # Dependências Python do projeto
│   ├── .env                 # Chaves de API (NÃO compartilhar)
│   ├── .env.example         # Modelo do arquivo .env
│   ├── templates/
│   │   └── index.html       # Frontend da interface de chat
│   └── static/
│       └── style.css        # Estilos
├── DIAGRAMA.pdf             # Diagrama da solução (arquitetura + fluxo de seleção de ferramenta)
├── README.md                # Este arquivo
└── run.py                   # Script de inicialização rápida (sobe o servidor na porta 8000)
```

| Arquivo | Função |
|---|---|
| `app/main.py` | Rotas `GET /`, `POST /chat` e `GET /health`; ferramentas `get_weather` e `get_forecast`; integração com o Gemini; validações e tratamento de erros |
| `app/templates/index.html` | Interface do chat (HTML, CSS e JavaScript): cards de clima atual e previsão, selo da ferramenta usada |
| `app/check_setup.py` | Confere as chaves do `.env` e testa as duas ferramentas e o Gemini antes de rodar |
| `app/.env.example` | Modelo do `.env`: copie para `app/.env` e preencha `GEMINI_API_KEY` e `OPENWEATHER_API_KEY` |
| `DIAGRAMA.pdf` | Diagrama da solução, entregável da atividade |

---

## 🚀 Como Executar

### 1. Instalar as dependências
```bash
pip install -r app/requirements.txt
```

### 2. Verificar a configuração (opcional, recomendado)
```bash
cd app
python check_setup.py
```
O script testa as duas ferramentas na OpenWeatherMap e uma chamada ao Gemini.

### 3. Iniciar a aplicação
Na raiz do projeto:
```bash
python run.py
```

### 4. Acessar a interface
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 🧪 Exemplos de Teste

1. **Clima atual → `get_weather`:**
   > *"Como está o tempo em São Paulo agora?"*
2. **Previsão → `get_forecast`:**
   > *"Vai chover amanhã em Londres?"*
   > *"Qual a previsão para os próximos 3 dias no Rio de Janeiro?"*
3. **Duas ferramentas na mesma pergunta:**
   > *"Como está Recife agora e qual a previsão para amanhã em Porto Alegre?"*
4. **Tratamento de erro (cidade inexistente):**
   > *"Como está o clima em XyzCidadeFantasma123?"*
5. **Pergunta fora de contexto (nenhuma ferramenta):**
   > *"Qual a capital da França?"* → selo "Nenhuma ferramenta acionada"

---

## ⚠️ Limite do plano gratuito do Gemini

O plano gratuito do Gemini limita o uso em três medidas: **requisições por minuto (RPM)**, **tokens por minuto (TPM)** e **requisições por dia (RPD)**. Passar de qualquer uma delas faz a API responder com erro 429. Pontos importantes ([documentação oficial](https://ai.google.dev/gemini-api/docs/rate-limits)):

- Os limites são **por modelo** e **por projeto do Google Cloud, não por chave de API**: criar outra chave no mesmo projeto não dá cota nova.
- O limite diário (RPD) é renovado à **meia-noite do horário do Pacífico** (≈ 4h/5h no horário de Brasília).
- Os valores exatos do plano gratuito variam por modelo e podem mudar sem aviso. Consulte os da sua conta em [AI Studio → Rate limits](https://aistudio.google.com/rate-limit).

**Neste projeto:** cada pergunta no chat usa **2 requisições** ao Gemini (uma para escolher a ferramenta e outra para redigir a resposta final). Quando a cota de um modelo acaba, a aplicação passa automaticamente para o próximo da lista `GEMINI_MODELS` em `main.py`. Se todos estiverem sem cota ou sobrecarregados, a interface mostra o motivo de cada um. Para evitar problemas na apresentação, faça poucos testes antes de gravar o vídeo.