# 🧠 Visualizer OCR
**App Streamlit com Google Cloud Document AI**

---

## 📘 Descrição

O **Visualizer OCR** é uma aplicação web desenvolvida com **Streamlit** que utiliza o **Google Cloud Document AI** para realizar OCR (Reconhecimento Óptico de Caracteres) otimizado em imagens com escrita manual ou cursiva.

Foco principal: **detecção e extração de texto em imagens (JPG, JPEG, PNG)**, com visualização de **bounding boxes** em torno de tokens ou caracteres.

> 🌐 **Idiomas suportados:** Português (pt) com fallback para Inglês (en).

Ideal para:
- Transcrição de notas e formulários manuscritos.  
- Digitalização de documentos históricos.  
- Análise visual de resultados de OCR com destaque de texto detectado.

---

## ⚙️ Recursos Principais

- **Upload de imagens:** JPG, JPEG, PNG com escrita cursiva ou manual.  
- **Processamento OCR:** via *Document AI Processor* com hints de idioma (`pt/en`).  
- **Extração de texto:** opção para texto corrido ou segmentado (linhas/parágrafos).  
- **Visualização:** bounding boxes vermelhos sobre caracteres/tokens (ativado na sidebar).  
- **Controle de uso:**  
  - Modo teste → 50 processamentos/mês  
- **Autenticação:** login simples (teste).  
- **Estatísticas:** tempo de processamento, número de tokens, linhas e entidades.  
- **Logs detalhados:** JSON com configs, tempos e uso.  
- **Fallbacks robustos:** credenciais locais, `secrets.toml` ou Secret Manager (GCP).

---

## 🧩 Requisitos

- **Python 3.9+**
- Conta no **Google Cloud Platform (GCP)** com:
  - Projeto habilitado para **Document AI**  
  - **Service Account** com roles:
    - `roles/documentai.user`
    - `roles/secretmanager.secretAccessor`
  - Processor criado

---

## 🧠 Instalação Local

### 1. Clone o repositório

```bash
git clone <seu-repo-url>
cd visualizer-ocr
```

### 2. Crie o arquivo `requirements.txt`

```text
streamlit>=1.28.0
pillow>=10.0.0
python-dotenv>=1.0.0
google-cloud-documentai>=2.20.0
google-cloud-secret-manager>=2.20.0
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

### 4. Configure as credenciais locais

Baixe o JSON da sua **Service Account** no GCP:

> IAM → Service Accounts →  
> `seujsonaccount@seu-projeto.iam.gserviceaccount.com` →  
> Keys → Create Key → JSON

Salve como:
```
./credentials/document-ai-key.json
```

### 5. Crie o arquivo `.streamlit/secrets.toml`

Veja o exemplo abaixo na seção **Configuração**.

### 6. Execute o app localmente

```bash
streamlit run main.py
```

Acesse em: [http://localhost:8501](http://localhost:8501)

---

## 🔧 Configuração

A configuração é feita via **secrets.toml**, tanto localmente (`.streamlit/secrets.toml`) quanto no **Streamlit Cloud**.

### Exemplo de `secrets.toml`

```toml
# Secrets.toml para Streamlit - Configurações (Document AI OCR)

[app]
email = ""
password = ""

[google]
# Configs do Google Cloud (Document AI e Secret Manager)
project_id_numeric = ""  # Para paths de API (secrets, processors)
project_id_string = ""  # Para validação do JSON da SA
location = ""
processor_id = ""

# Credenciais (fallback local; o Secret Manager é carregado dinamicamente no código)
application_credentials_path = ""
```

---

## ☁️ Deploy no Streamlit Cloud

1. **Crie um repositório no GitHub** com o código (incluindo `main.py`, `requirements.txt`).
2. **Conecte ao Streamlit Cloud:**  
   → [https://share.streamlit.io](https://share.streamlit.io)  
   → *New App* → Selecione o repo e o branch `main`  
   → Defina `main.py` como *Main file*.
3. **Configure os secrets:**  
   Vá em *Settings > Secrets* e cole o conteúdo do seu `secrets.toml`.
4. **Deploy automático:**  
   O app será implantado e disponibilizado por uma URL pública.

> 💰 **Custos:**  
> Free Tier GCP (Document AI 0 BRL / 1000 arquivos).  
> Streamlit Cloud gratuito para apps públicos.

---

## 🚀 Uso

1. **Acesse o App:** localmente (`streamlit run main.py`) ou na URL do Streamlit Cloud.  
2. **Login:** com as credenciais do `secrets.toml`:
3. **Sidebar:**
   - Ative “Exibir Bounding Boxes” e/ou “Extrair por Linhas”.
4. **Upload:** envie uma imagem (JPG/PNG com texto manual).  
5. **Processar:** clique em **🚀 Processar com Document AI**.

### Resultados

- Texto extraído (campo `text_area` + lista de linhas).  
- Imagem anotada com boxes (se ativado).  
- Estatísticas e logs JSON (uso, tempo, tokens).

---

## 🧰 Troubleshooting

| Problema | Solução |
|-----------|----------|
| **"No key could be detected"** | Verifique PEM inválido no `service_account_json` (escapes `\n`) ou taca fogo 🔥. |
| **Timeout ADC (Prod)** | O Streamlit Cloud usa fallback TOML — confirme `service_account_json`. |
| **Secret Manager 404/501** | Senta e chora |
| **Erro no Processador** | Verifique `processor_id` e permissões IAM. |
| **Erros de SDK/API** | Atualize dependências: `pip install --upgrade google-cloud-documentai`. |

---

## 🧪 Métricas e Logs

- Tempo total de processamento.  
- Tokens, linhas e entidades detectadas.  
- Uso mensal (persistente em JSON).  
- Logs em formato JSON com parâmetros e estatísticas.

---

## 🤝 Contribuições

1. Faça um **fork** do repositório.  
2. Crie uma **branch** (`feat/nova-funcionalidade`).  
3. Envie um **Pull Request**.  

Relate bugs ou ideias via **Issues**

---

## 🪪 Licença

**Apache 2.0**  
Veja o arquivo `LICENSE` para mais detalhes.  
Dev. por **rbxyz**.  
Contato: [rbcr4z1@gmail.com]

> Última atualização: **2025/10**  
> Testado em Streamlit **1.28+**
