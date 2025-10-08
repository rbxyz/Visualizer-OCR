import streamlit as st
import time
import tempfile
import os
import re
import json
from datetime import datetime, UTC
from PIL import Image, ImageDraw
from google.cloud import secretmanager
from google.cloud import documentai_v1 as documentai
from google.cloud.documentai_v1 import (
    DocumentProcessorServiceClient,
    ProcessRequest,
    RawDocument,
)
from google.cloud.documentai_v1.types import ProcessOptions, OcrConfig
from google.oauth2 import service_account
from google.api_core.client_options import ClientOptions

# Carregamento exclusivo de secrets.toml (sem dotenv ou os.environ)
try:
    # Extrai seções do st.secrets
    app = st.secrets["app"]
    app_test = st.secrets["app"]["test"]
    google = st.secrets["google"]

    # Atribui variáveis globais
    APP_EMAIL = app["email"]
    APP_PASSWORD = app["password"]
    USAGE_LIMIT = app["usage_limit"]  # 950 (int do TOML)

    TEST_EMAIL = app_test["email"]
    TEST_PASSWORD = app_test["password"]
    TEST_USAGE_LIMIT = app_test["usage_limit"]  # 50 (int do TOML)

    PROJECT_ID_NUMERIC = google["project_id_numeric"]
    PROJECT_ID_STRING = google["project_id_string"]
    LOCATION = google["location"]
    PROCESSOR_ID = google["processor_id"]
    GOOGLE_APPLICATION_CREDENTIALS = google["application_credentials_path"]

    # PROJECT_ID para API (numeric)
    PROJECT_ID = PROJECT_ID_NUMERIC

except KeyError as e:
    st.error(f"❌ Erro no secrets.toml: Chave '{e}' não encontrada. Verifique o arquivo .streamlit/secrets.toml ou o dashboard de produção.")
    st.stop()
except Exception as e:
    st.error(f"❌ Erro ao carregar secrets.toml: {e}. Certifique-se de que o arquivo está no local correto (.streamlit/secrets.toml).")
    st.stop()

# puxar credenciais do secret manager
def get_credentials():
    """Carrega credenciais: Prioriza TOML em prod, Secret Manager/arquivo em local. Fix para PEM no TOML."""
    project_numeric = PROJECT_ID_NUMERIC
    project_string = PROJECT_ID_STRING
    print(f"🔍 Projeto Numérico (para API): {project_numeric}")
    print(f"🔍 Projeto String (para SA JSON): {project_string}")

    # Detecção de Ambiente: Se arquivo local não existe, assume prod e força TOML (evita ADC timeout)
    json_path = GOOGLE_APPLICATION_CREDENTIALS
    is_local = os.path.exists(json_path) if json_path else False
    print(f"🔍 Ambiente detectado: {'Local (arquivo existe)' if is_local else 'Prod (forçando TOML)'}")

    if not is_local:
        print("🔍 Prod detectado – pulando ADC/Secret Manager e indo direto para fallback TOML.")
        # Fallback direto: TOML (com fix para PEM)
        try:
            sa_json_str = st.secrets["google"]["service_account_json"]
            print(f"🔍 TOML string length: {len(sa_json_str)}")
            credentials_info = json.loads(sa_json_str)
            print(f"🔍 TOML parseado: project_id = '{credentials_info.get('project_id')}'")

            # FIX PEM: Substitui '\\n' (literal) por '\n' (newline real) – resolve escapes errados no TOML/dashboard
            if 'private_key' in credentials_info:
                original_pk = credentials_info['private_key']
                credentials_info['private_key'] = original_pk.replace('\\n', '\n')
                num_lines = len(credentials_info['private_key'].split('\n'))
                print(f"🔍 PEM reparado: {num_lines} lines (antes: {len(original_pk.split('\\n'))} chunks). Preview: {credentials_info['private_key'][:60]}...")
                if num_lines < 10:
                    raise ValueError(f"❌ PEM inválido após fix (só {num_lines} lines). Verifique colagem no dashboard.")
            else:
                raise ValueError("❌ 'private_key' ausente no TOML.")

            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch project_id no TOML! Esperado: '{project_string}'.")

            # Validação final PEM
            private_key = credentials_info.get("private_key", "")
            if not (private_key.startswith("-----BEGIN PRIVATE KEY-----") and private_key.endswith("-----END PRIVATE KEY-----")):
                raise ValueError("❌ PEM inválido após fix (sem -----BEGIN/END).")

            print("✅ Credenciais carregadas do TOML (prod). PEM válido após reparo.")
            return credentials_info
        except (KeyError, json.JSONDecodeError, ValueError) as toml_e:
            print(f"❌ TOML falhou em prod: {toml_e}")
            raise Exception(
                f"❌ Erro no TOML em prod: {toml_e}\n"
                f"💡 Verifique se 'service_account_json' está no dashboard. Se PEM falhar, cole com \\n literais."
            )

    # Se local: Tenta Secret Manager/arquivo como antes (com ADC robusto)
    try:
        # Tenta ADC com timeout curto (5s) para evitar hang
        from google.auth import default
        import google.auth.transport.requests
        request = google.auth.transport.requests.Request(timeout=5.0)
        credentials, _ = default(request=request)
        print("🔍 ADC disponível para local/Secret Manager.")
    except Exception as adc_error:
        print(f"⚠️ ADC falhou em local: {adc_error}. Pulando Secret Manager.")
        credentials = None

    # Tenta Secret Manager se ADC OK
    if credentials:
        try:
            from google.api_core.client_options import ClientOptions
            client_options = ClientOptions(api_endpoint="us-secretmanager.googleapis.com")
            client = secretmanager.SecretManagerServiceClient(credentials=credentials, client_options=client_options)
            name = f"projects/{project_numeric}/secrets/DocumentAiTeste/versions/latest"
            print(f"🔍 Acessando secret: {name} (timeout 120s)")
            response = client.access_secret_version(request={"name": name}, timeout=120.0)
            credentials_info = json.loads(response.payload.data.decode("UTF-8"))

            # Fix PEM no secret também (caso precise)
            if 'private_key' in credentials_info:
                credentials_info['private_key'] = credentials_info['private_key'].replace('\\n', '\n')

            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch no secret! Esperado: '{project_string}'.")

            print("✅ Secret Manager sucesso (local).")
            return credentials_info
        except Exception as sm_error:
            print(f"⚠️ Secret Manager falhou: {sm_error}. Tentando arquivo local.")

    # Fallback final: Arquivo local
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                credentials_info = json.load(f)
            # Fix PEM no arquivo também
            if 'private_key' in credentials_info:
                credentials_info['private_key'] = credentials_info['private_key'].replace('\\n', '\n')
            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch no arquivo! Esperado: '{project_string}'.")
            print(f"✅ Arquivo local carregado: {json_path}.")
            return credentials_info
        except Exception as file_e:
            print(f"❌ Arquivo falhou: {file_e}")

    # Se chegar aqui em local: Erro total
    raise Exception("❌ Nenhum fallback funcionou em local. Verifique arquivo ou secret.")    """Carrega credenciais: Prioriza TOML em prod, Secret Manager/arquivo em local."""
    project_numeric = PROJECT_ID_NUMERIC
    project_string = PROJECT_ID_STRING
    print(f"🔍 Projeto Numérico (para API): {project_numeric}")
    print(f"🔍 Projeto String (para SA JSON): {project_string}")

    # Detecção de Ambiente: Se arquivo local não existe, assume prod e força TOML (evita ADC timeout)
    json_path = GOOGLE_APPLICATION_CREDENTIALS
    is_local = os.path.exists(json_path) if json_path else False
    print(f"🔍 Ambiente detectado: {'Local (arquivo existe)' if is_local else 'Prod (forçando TOML)'}")

    if not is_local:
        print("🔍 Prod detectado – pulando ADC/Secret Manager e indo direto para fallback TOML.")
        # Fallback direto: TOML (sem try Secret Manager)
        try:
            sa_json_str = st.secrets["google"]["service_account_json"]
            print(f"🔍 TOML string length: {len(sa_json_str)}")
            credentials_info = json.loads(sa_json_str)
            print(f"🔍 TOML parseado: project_id = '{credentials_info.get('project_id')}'")
            private_key_preview = credentials_info.get('private_key', '')[:60]  # Preview seguro
            print(f"🔍 Private key preview: {private_key_preview}... (começa com -----BEGIN?) {'Sim' if private_key_preview.startswith('-----BEGIN') else 'NÃO – erro PEM!'}")

            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch project_id no TOML! Esperado: '{project_string}'.")

            private_key = credentials_info.get("private_key", "")
            if not (private_key.startswith("-----BEGIN PRIVATE KEY-----") and private_key.endswith("-----END PRIVATE KEY-----")):
                raise ValueError("❌ PEM inválido no TOML (sem -----BEGIN/END ou \n ausentes). Re-cole o JSON.")

            print("✅ Credenciais carregadas do TOML (prod). PEM válido.")
            return credentials_info
        except (KeyError, json.JSONDecodeError, ValueError) as toml_e:
            print(f"❌ TOML falhou em prod: {toml_e}")
            raise Exception(
                f"❌ Erro no TOML em prod: {toml_e}\n"
                f"💡 Cole o TOML completo no dashboard (incluindo service_account_json com \n literais na private_key)."
            )

    # Se local: Tenta Secret Manager/arquivo como antes (com ADC robusto)
    try:
        # Tenta ADC com timeout curto (5s) para evitar hang
        from google.auth import default
        import google.auth.transport.requests
        request = google.auth.transport.requests.Request(timeout=5.0)
        credentials, _ = default(request=request)
        print("🔍 ADC disponível para local/Secret Manager.")
    except Exception as adc_error:
        print(f"⚠️ ADC falhou em local: {adc_error}. Pulando Secret Manager.")
        credentials = None

    # Tenta Secret Manager se ADC OK
    if credentials:
        try:
            client_options = ClientOptions(api_endpoint="us-secretmanager.googleapis.com")
            client = secretmanager.SecretManagerServiceClient(credentials=credentials, client_options=client_options)
            name = f"projects/{project_numeric}/secrets/DocumentAiTeste/versions/latest"
            print(f"🔍 Acessando secret: {name} (timeout 120s)")
            response = client.access_secret_version(request={"name": name}, timeout=120.0)
            credentials_info = json.loads(response.payload.data.decode("UTF-8"))

            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch no secret! Esperado: '{project_string}'.")

            print("✅ Secret Manager sucesso (local).")
            return credentials_info
        except Exception as sm_error:
            print(f"⚠️ Secret Manager falhou: {sm_error}. Tentando arquivo local.")

    # Fallback final: Arquivo local
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, "r") as f:
                credentials_info = json.load(f)
            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch no arquivo! Esperado: '{project_string}'.")
            print(f"✅ Arquivo local carregado: {json_path}.")
            return credentials_info
        except Exception as file_e:
            print(f"❌ Arquivo falhou: {file_e}")

    # Se chegar aqui em local: Erro total
    raise Exception("❌ Nenhum fallback funcionou em local. Verifique arquivo ou secret.")
    """Carrega credenciais do Secret Manager com fallback para arquivo/TOML (prod-friendly)."""
    # Usa variáveis globais de secrets.toml
    project_numeric = PROJECT_ID_NUMERIC
    project_string = PROJECT_ID_STRING
    print(f"🔍 Projeto Numérico (para API): {project_numeric}")
    print(f"🔍 Projeto String (para SA JSON): {project_string}")

    # Tenta Secret Manager primeiro (com timeout e endpoint regional para prod)
    try:
        # Config client para prod: endpoint 'us' (ajuste se sua região for diferente), timeout 120s
        # Primeiro tenta usar credenciais do ambiente (ADC ou GOOGLE_APPLICATION_CREDENTIALS)
        credentials = None
        try:
            from google.auth import default
            credentials, _ = default()
            print("🔍 Usando Application Default Credentials para Secret Manager")
        except Exception as adc_error:
            print(f"⚠️ ADC não disponível: {adc_error}. Pulando Secret Manager e indo direto para fallback TOML.")
            raise Exception("ADC_UNAVAILABLE")  # Força fallback imediato

        client_options = ClientOptions(api_endpoint="us-secretmanager.googleapis.com")
        client = secretmanager.SecretManagerServiceClient(
            credentials=credentials,
            client_options=client_options
        )
        name = f"projects/{project_numeric}/secrets/DocumentAiTeste/versions/latest"
        print(f"🔍 Tentando acessar secret: {name} (timeout: 120s)")

        response = client.access_secret_version(request={"name": name}, timeout=120.0)
        credentials_info = json.loads(response.payload.data.decode("UTF-8"))
        
        # Validação
        if credentials_info.get("project_id") != project_string:
            raise ValueError(
                f"❌ Mismatch de Project ID no secret! JSON tem '{credentials_info.get('project_id')}', "
                f"mas esperado: '{project_string}'. Verifique o JSON da SA."
            )
        
        print("✅ Credenciais carregadas do Secret Manager com sucesso (Project ID validado).")
        return credentials_info
    
    except Exception as sm_error:
        error_msg = str(sm_error)
        print(f"⚠️ Erro no Secret Manager: {error_msg} (código: {getattr(sm_error, 'code', 'unknown')})")

        # Se ADC não está disponível, pula diretamente para TOML
        if "ADC_UNAVAILABLE" in error_msg:
            print("🔍 ADC indisponível - pulando para fallback TOML direto.")
        elif "504" in error_msg or "DEADLINE_EXCEEDED" in error_msg:
            print("🔍 Detectado timeout 504 – comum em prod por latência. Tentando fallback.")
        elif "PERMISSION_DENIED" in error_msg:
            print("❌ Permissões insuficientes para Secret Manager. Adicione 'Secret Manager Secret Accessor' à SA no IAM.")

        # Só tenta fallbacks se não for erro de ADC
        if "ADC_UNAVAILABLE" not in error_msg:
            # Fallback 1: Arquivo local (para dev)
            json_path = GOOGLE_APPLICATION_CREDENTIALS
            if json_path and os.path.exists(json_path):
                try:
                    with open(json_path, "r") as f:
                        credentials_info = json.load(f)
                    if credentials_info.get("project_id") != project_string:
                        raise ValueError(f"❌ Mismatch no fallback JSON! Esperado: '{project_string}'.")
                    print(f"✅ Credenciais carregadas do arquivo local: {json_path}.")
                    return credentials_info
                except Exception as fallback_e:
                    print(f"❌ Erro no fallback local: {fallback_e}")

        # Fallback 2: JSON direto do secrets.toml (para prod – com debug extra) - SEMPRE tenta se ADC falhar
        try:
            sa_json_str = st.secrets["google"]["service_account_json"]
            print(f"🔍 Tentando fallback TOML: String length = {len(sa_json_str)}")  # Debug: tamanho da string
            credentials_info = json.loads(sa_json_str)
            print(f"🔍 Fallback TOML parseado: project_id = {credentials_info.get('project_id')}")
            print(f"🔍 Private key starts with: {credentials_info.get('private_key', '')[:50]}...")  # Debug: preview da key (sem expor toda)

            if credentials_info.get("project_id") != project_string:
                raise ValueError(f"❌ Mismatch no fallback TOML! Esperado: '{project_string}'.")

            # Validação extra: Verifica se private_key tem formato PEM válido (começa e termina correto)
            private_key = credentials_info.get("private_key", "")
            if not (private_key.startswith("-----BEGIN PRIVATE KEY-----") and private_key.endswith("-----END PRIVATE KEY-----")):
                raise ValueError("❌ Formato da private_key inválido no TOML (PEM corrompido). Verifique escapes de \\n.")

            print("✅ Credenciais carregadas diretamente do secrets.toml (fallback prod). Private key válida.")
            return credentials_info
        except (KeyError, json.JSONDecodeError) as toml_e:
            print(f"❌ Fallback TOML falhou: {toml_e}. Adicione 'service_account_json' no secrets.toml.")
        except ValueError as ve:
            print(f"❌ Erro na validação TOML: {ve}")
        
        # Se todos falharem
        raise Exception(
            f"❌ Falha total no carregamento de credenciais. Erro principal: {error_msg}\n"
            f"💡 1. Verifique permissões IAM: 'Secret Manager Secret Accessor' no projeto {project_numeric}.\n"
            f"💡 2. Em prod: Adicione 'service_account_json' no secrets.toml com JSON válido (verifique private_key PEM).\n"
            f"💡 3. Teste CLI: gcloud secrets versions access latest --secret=DocumentAiTeste --project=marcaai-469014\n"
            f"💡 4. Debug: Verifique logs para 'Private key starts with' – deve mostrar '-----BEGIN PRIVATE KEY-----'."
        )

# Estado de sessão (inicia cronômetro e login)
if "tempo_start_total" not in st.session_state:
    st.session_state["tempo_start_total"] = time.time()
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "is_test_user" not in st.session_state:
    st.session_state["is_test_user"] = False

# Função de login simples (ajustada para usar variáveis de secrets.toml)
def login():
    st.title("🔐 Login Necessário")
    st.info("Para acessar o sistema de OCR com Document AI, faça login com suas credenciais.")
    
    email = st.text_input("📧 Email", placeholder="Digite seu email")
    password = st.text_input("🔑 Senha", type="password", placeholder="Digite sua senha")
    
    if st.button("Entrar", type="primary"):
        if email == TEST_EMAIL and password == TEST_PASSWORD:
            st.session_state["logged_in"] = True
            st.session_state["is_test_user"] = True
            st.success("✅ Login como usuário de teste realizado! (Limite: 50 usos) Redirecionando...")
            st.rerun()
        elif email == APP_EMAIL and password == APP_PASSWORD:
            st.session_state["logged_in"] = True
            st.session_state["is_test_user"] = False
            st.success("✅ Login realizado com sucesso! Redirecionando...")
            st.rerun()
        else:
            st.error("❌ Email ou senha incorretos. Tente novamente.")
    
    st.markdown("---")
    st.info(
        "**👨‍💻 Usuário de Teste (para experimentação rápida):**\n"
        f"- Email: `{TEST_EMAIL}`\n"
        f"- Senha: `{TEST_PASSWORD}`\n"
        f"- Limite: {TEST_USAGE_LIMIT} processamentos por mês (contador separado)\n\n"
    )

# Função de logout
def logout():
    if st.sidebar.button("🚪 Sair", type="secondary"):
        st.session_state["logged_in"] = False
        st.session_state["is_test_user"] = False
        st.success("Logout realizado. Volte quando quiser!")
        st.rerun()

# Verifica login e mostra app ou form
if not st.session_state["logged_in"]:
    login()
else:
    logout()
    
    is_test = st.session_state.get("is_test_user", False)
    
    # UI principal
    st.title("Visualizer OCR")
    st.markdown(
        f"Sistema usando Google Cloud Document AI para OCR otimizado em Português, voltado para transcriçaõ de escritas manuais. "
        f"{'(Modo Teste: Limite 50 usos)' if is_test else 'Uso limitado a 950 processamentos por mês (Free Tier)'}."
    )

    # Sidebar
    st.sidebar.header("⚙️ Configurações")
    enable_symbol_detection = st.sidebar.checkbox(
        "Exibir Bounding Boxes (caracteres/tokens)", value=True, help="Mostra retângulos vermelhos em volta dos tokens detectados"
    )
    extract_by_lines = st.sidebar.checkbox(
        "Extrair Texto por Linhas/Parágrafos", value=True, help="Separa o texto detectado por parágrafos/linhas"
    )
    st.sidebar.markdown("Idioma OCR: priorizado para Português (pt) com fallback em Inglês (en).")

    # Configs de uso baseadas no usuário (usa globais de secrets.toml)
    USAGE_LIMIT_CURRENT = TEST_USAGE_LIMIT if is_test else USAGE_LIMIT
    USAGE_STATE_PATH = ".usage_state_test.json" if is_test else ".usage_state.json"

    def _current_month_key() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def _load_usage_state() -> dict:
        now_month = _current_month_key()
        state = {"month": now_month, "used": 0}
        try:
            if os.path.exists(USAGE_STATE_PATH):
                with open(USAGE_STATE_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f) or {}
                    state["month"] = loaded.get("month", now_month)
                    state["used"] = int(loaded.get("used", 0))
        except Exception as e:
            print(f"⚠️ Erro carregando estado de uso: {e}")

        if state["month"] != now_month:
            state = {"month": now_month, "used": 0}
            _save_usage_state(state)
        return state

    def _save_usage_state(state: dict) -> None:
        tmp_path = USAGE_STATE_PATH + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, USAGE_STATE_PATH)
        except Exception as e:
            print(f"⚠️ Erro salvando estado de uso: {e}")

    def can_process(units: int = 1) -> tuple[bool, int, dict]:
        state = _load_usage_state()
        remaining = max(0, USAGE_LIMIT_CURRENT - state["used"])
        allowed = units <= remaining
        return allowed, remaining, state

    def record_usage(units: int = 1) -> dict:
        state = _load_usage_state()
        state["used"] = int(state["used"]) + int(units)
        _save_usage_state(state)
        return state

    # Mostrar status de uso no sidebar
    usage_state = _load_usage_state()
    remaining = max(0, USAGE_LIMIT_CURRENT - usage_state["used"])
    usage_ratio = min(1.0, usage_state["used"] / USAGE_LIMIT_CURRENT) if USAGE_LIMIT_CURRENT else 0.0
    st.sidebar.subheader("🧮 Controle de Uso (Mensal)")
    limit_type = "Teste" if is_test else "Normal"
    st.sidebar.metric(f"Usos consumidos ({limit_type})", f"{usage_state['used']} / {USAGE_LIMIT_CURRENT}")
    st.sidebar.progress(usage_ratio, text=f"Restantes: {remaining}")
    if remaining == 0:
        st.sidebar.error(f"Limite de {limit_type} ({USAGE_LIMIT_CURRENT} usos) atingido. Novos processamentos serão bloqueados.")

    def get_mime_type(file_extension: str) -> str:
        mime_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".pdf": "application/pdf",
        }
        return mime_types.get(file_extension.lower(), "application/octet-stream")

    def _text_from_anchor(text_anchor, full_text: str) -> str:
        if not text_anchor or not full_text:
            return ""
        segments = getattr(text_anchor, "text_segments", None)
        if segments:
            parts = []
            for seg in segments:
                start = seg.start_index if seg.start_index is not None else 0
                end = seg.end_index
                if end is None:
                    continue
                parts.append(full_text[start:end])
            return "".join(parts).strip()

        content_locations = getattr(text_anchor, "content_locations", None)
        if content_locations:
            try:
                loc = content_locations[0].location
                start = getattr(getattr(loc, "segment", None), "index", 0) or 0
                length = getattr(content_locations[0], "length", 0) or 0
                return full_text[start : start + length].strip()
            except Exception:
                return ""
        return ""

    def extract_text_by_paragraphs(document) -> list[str]:
        if not getattr(document, "pages", None):
            return [document.text.strip()] if document.text else ["Nenhum texto detectado."]

        lines = []
        full_text = document.text or ""

        for page in document.pages:
            for p in getattr(page, "paragraphs", []):
                para_text = _text_from_anchor(getattr(p.layout, "text_anchor", None), full_text)
                para_text = re.sub(r"\s+", " ", para_text).strip()
                if para_text:
                    lines.append(para_text)

        if not lines:
            for page in document.pages:
                for b in getattr(page, "blocks", []):
                    block_text = _text_from_anchor(getattr(b.layout, "text_anchor", None), full_text)
                    block_text = re.sub(r"\s+", " ", block_text).strip()
                    if block_text:
                        lines.append(block_text)

        if not lines:
            return [full_text.strip()] if full_text else ["Nenhum texto detectado."]
        return lines

    def draw_bounding_boxes(image: Image.Image, document) -> Image.Image:
        if not getattr(document, "pages", None):
            return image
        if not getattr(document.pages[0], "tokens", None):
            return image

        draw = ImageDraw.Draw(image)
        page = document.pages[0]
        width, height = image.size

        for token in page.tokens:
            try:
                bpoly = getattr(token.layout, "bounding_poly", None)
                if not bpoly:
                    continue

                vertices = getattr(bpoly, "normalized_vertices", None)
                if vertices and len(vertices) >= 2:
                    x_coords = [v.x * width for v in vertices]
                    y_coords = [v.y * height for v in vertices]
                else:
                    abs_vertices = getattr(bpoly, "vertices", None)
                    if not abs_vertices or len(abs_vertices) < 2:
                        continue
                    x_coords = [v.x for v in abs_vertices]
                    y_coords = [v.y for v in abs_vertices]

                x_min, x_max = min(x_coords), max(x_coords)
                y_min, y_max = min(y_coords), max(y_coords)
                draw.rectangle([x_min, y_min, x_max, y_max], outline="red", width=2)

                token_text = _text_from_anchor(getattr(token.layout, "text_anchor", None), document.text or "")
                if token_text:
                    label = token_text[:10] + ("..." if len(token_text) > 10 else "")
                    draw.text((x_min, max(0, y_min - 14)), label, fill="red")

            except Exception as e:
                print(f"⚠️ Erro ao desenhar token: {e}")
                continue

        return image

    # CORRIGIDO: Definição da função ANTES da chamada (process_document_sample)
    def process_document_sample(project_id: str, location: str, processor_id: str, file_path: str, mime_type: str):
        """
        Processa documento com Document AI usando endpoint regional.
        - CORRIGIDO: Usa credenciais do Secret Manager com fallback + timeout no client
        """
        try:
            credentials_info = get_credentials()
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            print("✅ Credenciais aplicadas ao client do Document AI.")
        except Exception as e:
            st.error(f"❌ Erro ao carregar credenciais: {e}")
            st.stop()

        client = DocumentProcessorServiceClient(
            credentials=credentials,
            client_options=ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
        )

        name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"

        with open(file_path, "rb") as f:
            content = f.read()

        raw_document = RawDocument(content=content, mime_type=mime_type)

        ocr_config = OcrConfig(
            hints=documentai.OcrConfig.Hints(language_hints=["pt", "en"])
        )

        process_options = ProcessOptions(ocr_config=ocr_config)

        request = ProcessRequest(
            name=name,
            raw_document=raw_document,
            process_options=process_options,
        )

        result = client.process_document(request=request, timeout=120.0)
        return result.document

    # Upload (agora a chamada da função é válida, pois definida acima)
    uploaded_file = st.file_uploader("📤 Carregue uma imagem com escrita cursiva", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        file_extension = os.path.splitext(uploaded_file.name)[1]
        mime_type = get_mime_type(file_extension)

        image = Image.open(uploaded_file)
        st.image(image, caption="📸 Imagem Carregada (Original)", width='stretch')

        if st.button("🚀 Processar com Document AI", type="primary"):
            allowed, remaining, _ = can_process(units=1)
            if not allowed:
                limit_type = "Teste" if is_test else "Normal"
                st.error(f"❌ Limite de uso mensal atingido! ({USAGE_LIMIT_CURRENT} processamentos - Modo {limit_type}). Restantes: 0")
                st.info("💡 Aguarde o próximo mês ou contate o administrador para reset manual.")
                st.stop()  # Para o fluxo

            st.session_state["tempo_start_total"] = time.time()

            # Salva arquivo temporário
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as tmp_file:
                uploaded_file.seek(0)
                tmp_file.write(uploaded_file.read())
                tmp_path = tmp_file.name

            try:
                st.subheader("🔄 Processando com Google Cloud Document AI...")
                tempo_process = time.time()

                with st.spinner("Enviando para o endpoint e processando... (PT como hint de idioma)"):
                    document = process_document_sample(
                        project_id=PROJECT_ID,
                        location=LOCATION,
                        processor_id=PROCESSOR_ID,
                        file_path=tmp_path,
                        mime_type=mime_type,
                    )

                tempo_process_fim = time.time()

                # Calcula unidades consumidas (1 por imagem, ou por número de páginas se multi-página)
                units_used = len(getattr(document, "pages", [])) if getattr(document, "pages", []) else 1
                record_usage(units=units_used)  # Atualiza contador após sucesso

                # Atualiza sidebar com novo estado (para refletir o uso, com tipo de limite)
                usage_state = _load_usage_state()
                remaining = max(0, USAGE_LIMIT_CURRENT - usage_state["used"])
                limit_type = "Teste" if is_test else "Normal"
                st.sidebar.metric(f"Usos consumidos ({limit_type})", f"{usage_state['used']} / {USAGE_LIMIT_CURRENT}")
                st.sidebar.progress(min(1.0, usage_state["used"] / USAGE_LIMIT_CURRENT), text=f"Restantes: {remaining}")
                if remaining == 0:
                    st.sidebar.error(f"Limite de {limit_type} ({USAGE_LIMIT_CURRENT} usos) atingido. Novos processamentos serão bloqueados.")

                # Extração de texto (linhas/parágrafos ou texto corrido)
                if extract_by_lines:
                    paragraphs = extract_text_by_paragraphs(document)
                    extracted_text = "\n".join(paragraphs)
                    st.success(
                        f"✅ Processamento concluído em {tempo_process_fim - tempo_process:.3f}s "
                        f"(extraído em {len(paragraphs)} linhas/parágrafos) | Unidades usadas: {units_used}"
                    )
                else:
                    extracted_text = document.text if document.text else "Nenhum texto detectado."
                    extracted_text = re.sub(r"\s+", " ", extracted_text).strip()
                    paragraphs = [extracted_text]
                    st.success(f"✅ Processamento concluído em {tempo_process_fim - tempo_process:.3f}s | Unidades usadas: {units_used}")

                # ETAPA: Resultado Final
                st.subheader("📄 Texto Reconhecido pelo Document AI (Separado por Linhas)")
                st.text_area("Texto extraído (com quebras de linha)", extracted_text, height=300)

                # Mostra como lista bulletada para clareza
                st.subheader("📋 Linhas/Parágrafos Individuais")
                for i, para in enumerate(paragraphs, 1):
                    st.write(f"**Linha {i}:** {para}")

                # Visualização de Bounding Boxes (se ativada)
                if enable_symbol_detection and getattr(document, "pages", None) and getattr(document.pages[0], "tokens", None):
                    annotated_image = image.copy()
                    annotated_image = draw_bounding_boxes(annotated_image, document)
                    st.subheader("🔍 Imagem com Bounding Boxes (Detecção de Caracteres/Símbolos)")
                    st.image(
                        annotated_image,
                        caption="📸 Imagem Anotada com Retângulos Vermelhos (Tokens Detectados)",
                        width='stretch',
                    )
                    st.info(f"📊 Tokens/caracteres com boxes: {len(document.pages[0].tokens)}")
                else:
                    st.info("ℹ️ Detecção de símbolos desativada ou sem tokens detectados. Ative no sidebar para visualizar boxes.")

                # Estatísticas simples
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("MIME Type Usado", mime_type)
                with col2:
                    st.metric("Tempo de Processamento", f"{tempo_process_fim - tempo_process:.3f}s")

                # Tempo total
                tempo_total = time.time() - st.session_state["tempo_start_total"]
                st.success(f"🎉 Processamento total: {tempo_total:.2f}s")

                # LOG DETALHADO
                st.subheader("📊 Detalhes da Resposta do Document AI")
                num_tokens = len(document.pages[0].tokens) if getattr(document, "pages", None) and getattr(document.pages[0], "tokens", None) else 0
                num_paragraphs = len(paragraphs)
                st.json(
                    {
                        "Configuração": {
                            "Project ID": PROJECT_ID,
                            "Location": LOCATION,
                            "Processor ID": PROCESSOR_ID,
                            "MIME Type": mime_type,
                            "Arquivo": uploaded_file.name,
                            "Hints de Idioma (OCR)": ["pt", "en"],
                            "Exibir Bounding Boxes": enable_symbol_detection,
                            "Extração por Linhas": extract_by_lines,
                            "Tokens Detectados (Bounding Boxes)": num_tokens,
                            "Parágrafos/Linhas Detectados": num_paragraphs,
                            "Unidades Consumidas Neste Processamento": units_used,
                            "Modo de Usuário": f"{'Teste (Limite 50)' if is_test else 'Normal (Limite 950)'}",
                        },
                        "Tempos (segundos)": {
                            "Processamento Document AI": f"{tempo_process_fim - tempo_process:.3f}",
                            "TOTAL": f"{tempo_total:.3f}",
                        },
                        "Estatísticas": {
                            "Palavras Reconhecidas (total)": len(re.sub(r"\s+", " ", extracted_text).split()),
                            "Caracteres Totais": len(extracted_text),
                            "Linhas/Parágrafos (preview)": [
                                para[:50] + "..." if len(para) > 50 else para for para in paragraphs
                            ],
                        },
                        "Uso Mensal": {
                            "Consumidos": usage_state["used"],
                            "Limite": USAGE_LIMIT_CURRENT,
                            "Restantes": remaining,
                            "Tipo de Limite": limit_type,
                        },
                    }
                )

                # Opcional: Mostrar entidades se disponíveis (depende do processador)
                if getattr(document, "entities", None):
                    st.subheader("🔍 Entidades Detectadas (se aplicável)")
                    entities_info = []
                    for entity in document.entities:
                        entities_info.append(
                            {
                                "Tipo": getattr(entity, "type_", ""),
                                "Menção": getattr(entity, "mention_text", ""),
                                "Confiança": f"{getattr(entity, 'confidence', 0.0):.2f}",
                            }
                        )
                    st.json(entities_info)
                else:
                    st.info("ℹ️ Nenhuma entidade específica detectada (processador focado em texto geral).")

            except Exception as e:
                st.error(f"❌ Erro no processamento: {str(e)}")
                st.info(
                    "💡 Verifique: SDK instalado? Secret Manager configurado com 'DocumentAiTeste'? "
                    "Permissões (roles/documentai.user e secretmanager.secretAccessor) no projeto e tipo de processador compatível?"
                )
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    else:
        st.info("Faça upload de uma imagem com escrita cursiva")
        st.markdown(
            """
            ### Visualizer OCR

            - Suporte a imagens JPG, JPEG e PNG
            - Extração de texto com hints de idioma (PT/EN) para melhor acurácia
            - Separação por linhas/parágrafos (evita texto corrido)
            - Visualização de bounding boxes (tokens/caracteres) na imagem
            - **Controle de Uso: Limitado a 950 processamentos por mês (Free Tier) para modo normal; 50 para teste**
            - **Autenticação: Login requerido para acesso**

            #### Dicas
            - Se o texto vier corrido, mantenha a opção "Extrair por Linhas/Parágrafos" ativada.
            - Se boxes atrapalharem a visualização, desative "Exibir Bounding Boxes".
            - **Uso Mensal:** O contador reseta automaticamente no início de cada mês (UTC). Arquivos de estado: .usage_state.json (normal) ou .usage_state_test.json (teste)
            """
        )

#
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣶⣶⣿⢿⣿⣿⣷⣶⣦⣤⡀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢾⡻⣶⣾⣿⣿⣛⣻⣮⡉⣿⣿⣿⠟⠋⠉⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⢿⢿⣿⡿⠁⣀⠀⢛⣿⣿⣿⣷⣦⣄⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⠈⣿⣿⠁⠀⣿⡇⢸⡏⢻⣿⣿⣿⣿⣷⡄⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢰⣦⣝⠁⡀⠀⢙⠡⠚⠣⣾⣿⡿⠿⠿⠿⢿⡄
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠡⡀⠀⠀⠀⠄⠚⣰⣿⣿⣷⡄⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡔⡈⡲⠂⠰⠶⢟⡉⠿⢿⣿⣧⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠫⣓⠣⢀⡣⡀⠀⡔⣹⣧⠀⠉⠃⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠑⢄⣀⣀⣶⣶⠟⠛⠿⡀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⡏⢿⡏⠓⠀⠀⠀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢠⠉⠻⠏⣺⣷⠔⡄⠀⠀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⡒⢤⣀⡆⠀⠀⠀⢐⠀⠀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⡾⣋⣵⣾⡀⣿⣿⣶⢂⡌⣍⠆⠀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠘⠛⠛⠛⠛⠃⠉⠙⢏⣾⣧⢹⣿⠀⠀⠀⠀⠀⠀⠀
#⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⠿⣾⡏⠀⠀⠀⠀⠀⠀⠀
#