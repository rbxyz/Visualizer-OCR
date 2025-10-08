import streamlit as st
import time
import tempfile
import os
import re
import json
from datetime import datetime, UTC
from PIL import Image, ImageDraw
from dotenv import load_dotenv
from google.cloud import secretmanager
from google.cloud import documentai_v1 as documentai
from google.cloud.documentai_v1 import (
    DocumentProcessorServiceClient,
    ProcessRequest,
    RawDocument,
)
from google.cloud.documentai_v1.types import ProcessOptions, OcrConfig
from google.oauth2 import service_account

load_dotenv()

# Defina PROJECT_ID cedo para get_credentials() (com fallback)
PROJECT_ID = os.environ.get("PROJECT_ID", "811447882024")

# puxar credenciais do secret manager (corrigida com nome "DocumentAiTeste" e fallback)
def get_credentials():
    """Carrega credenciais do Secret Manager com fallback para arquivo local."""
    # Usa numeric para paths de API, string para validação de JSON
    project_numeric = os.environ.get("PROJECT_ID_NUMERIC", "811447882024")
    project_string = os.environ.get("PROJECT_ID_STRING", "marcaai-469014")  # ← CORRETO: Project ID string do seu projeto
    print(f"🔍 Projeto Numérico (para API): {project_numeric}")
    print(f"🔍 Projeto String (para SA JSON): {project_string}")
    # Tenta Secret Manager primeiro (path usa numeric)
    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_numeric}/secrets/DocumentAiTeste/versions/latest"  # Path correto com numeric
        print(f"🔍 Tentando acessar secret: {name}")
        response = client.access_secret_version(request={"name": name})
        credentials_info = json.loads(response.payload.data.decode("UTF-8"))
        
        # Validação CORRIGIDA: Compara com Project ID string (não numeric)
        if credentials_info.get("project_id") != project_string:
            raise ValueError(
                f"❌ Mismatch de Project ID no secret! JSON tem '{credentials_info.get('project_id')}', "
                f"mas esperado: '{project_string}'. Verifique o JSON da SA."
            )
        
        print("✅ Credenciais carregadas do Secret Manager com sucesso (Project ID validado).")
        return credentials_info
    except Exception as e:
        print(f"⚠️ Erro ao carregar do Secret Manager: {e}. Tentando fallback para arquivo local.")
        
        # Fallback: Usa GOOGLE_APPLICATION_CREDENTIALS se definido
        json_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if json_path and os.path.exists(json_path):
            try:
                with open(json_path, "r") as f:
                    credentials_info = json.load(f)
                # Validação corrigida no fallback
                if credentials_info.get("project_id") != project_string:
                    raise ValueError(
                        f"❌ Mismatch de Project ID no fallback JSON! Tem '{credentials_info.get('project_id')}', "
                        f"esperado: '{project_string}'."
                    )
                print(f"✅ Credenciais carregadas do arquivo local: {json_path} (Project ID validado).")
                return credentials_info
            except Exception as fallback_e:
                print(f"❌ Erro no fallback local: {fallback_e}")
        else:
            print(f"❌ Sem GOOGLE_APPLICATION_CREDENTIALS definido ou arquivo não encontrado: {json_path}")
        
        raise Exception(
            f"❌ Falha total no carregamento de credenciais. Erro principal: {e}\n"
        )
            

# Estado de sessão (inicia cronômetro e login)
if "tempo_start_total" not in st.session_state:
    st.session_state["tempo_start_total"] = time.time()
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "is_test_user" not in st.session_state:
    st.session_state["is_test_user"] = False

# Credenciais via env
DEFAULT_EMAIL = os.environ.get("APP_EMAIL")
DEFAULT_PASSWORD = os.environ.get("APP_PASSWORD")

# Credenciais do usuário de teste
TEST_EMAIL = os.environ.get("TEST_EMAIL", "test@example.com")
TEST_PASSWORD = os.environ.get("TEST_PASSWORD", "test123")
TEST_USAGE_LIMIT = int(os.environ.get("TEST_USAGE_LIMIT", "50"))

if not DEFAULT_EMAIL or not DEFAULT_PASSWORD:
    print("⚠️ APP_EMAIL e APP_PASSWORD não configuradas – apenas modo teste disponível.")

# Função de login simples (atualizada com usuário de teste e exibição de credenciais)
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
        elif DEFAULT_EMAIL and DEFAULT_PASSWORD and email == DEFAULT_EMAIL and password == DEFAULT_PASSWORD:
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
        "**⚠️ Modo Produção:** Configure APP_EMAIL/APP_PASSWORD no .env para usuários reais.\n"
        "Use autenticação externa (ex: Google OAuth) para segurança. Remova esta exibição em produção."
    )
    if not DEFAULT_EMAIL or not DEFAULT_PASSWORD:
        st.warning("⚠️ Credenciais normais não configuradas – use o usuário de teste.")
    else:
        st.info(
            "**⚠️ Para produção:** Use autenticação externa (ex: Google OAuth). Credenciais configuradas via .env."
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
        f"{'(Modo Teste: Limite 50 usos)' if is_test else 'Uso limitado a 1000 processamentos por mês (Free Tier)'}."
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

    # Config Document AI
    LOCATION = os.environ.get("LOCATION", "us")
    PROCESSOR_ID = os.environ.get("PROCESSOR_ID", "8d9d68ebce2afb84")

    # Configs de uso baseadas no usuário
    USAGE_LIMIT = TEST_USAGE_LIMIT if is_test else int(os.environ.get("USAGE_LIMIT", "1000"))
    USAGE_STATE_PATH = ".usage_state_test.json" if is_test else os.environ.get(
        "USAGE_STATE_PATH",
        os.path.join(os.path.dirname(__file__), ".usage_state.json")
    )

    def _current_month_key() -> str:
        # CORRIGIDO: Usa datetime.now(UTC) em vez de utcnow() (sem warning)
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
        remaining = max(0, USAGE_LIMIT - state["used"])
        allowed = units <= remaining
        return allowed, remaining, state

    def record_usage(units: int = 1) -> dict:
        state = _load_usage_state()
        state["used"] = int(state["used"]) + int(units)
        _save_usage_state(state)
        return state

    # Mostrar status de uso no sidebar
    usage_state = _load_usage_state()
    remaining = max(0, USAGE_LIMIT - usage_state["used"])
    usage_ratio = min(1.0, usage_state["used"] / USAGE_LIMIT) if USAGE_LIMIT else 0.0
    st.sidebar.subheader("🧮 Controle de Uso (Mensal)")
    limit_type = "Teste" if is_test else "Normal"
    st.sidebar.metric(f"Usos consumidos ({limit_type})", f"{usage_state['used']} / {USAGE_LIMIT}")
    st.sidebar.progress(usage_ratio, text=f"Restantes: {remaining}")
    if remaining == 0:
        st.sidebar.error(f"Limite de {limit_type} ({USAGE_LIMIT} usos) atingido. Novos processamentos serão bloqueados.")

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
        - CORRIGIDO: Usa credenciais do Secret Manager com fallback
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
            client_options={"api_endpoint": f"{location}-documentai.googleapis.com"}
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

        result = client.process_document(request=request)
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
                st.error(f"❌ Limite de uso mensal atingido! ({USAGE_LIMIT} processamentos - Modo {limit_type}). Restantes: 0")
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
                remaining = max(0, USAGE_LIMIT - usage_state["used"])
                limit_type = "Teste" if is_test else "Normal"
                st.sidebar.metric(f"Usos consumidos ({limit_type})", f"{usage_state['used']} / {USAGE_LIMIT}")
                st.sidebar.progress(min(1.0, usage_state["used"] / USAGE_LIMIT), text=f"Restantes: {remaining}")
                if remaining == 0:
                    st.sidebar.error(f"Limite de {limit_type} ({USAGE_LIMIT} usos) atingido. Novos processamentos serão bloqueados.")

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
                            "Modo de Usuário": f"{'Teste (Limite 50)' if is_test else 'Normal (Limite 1000)'}",
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
                            "Limite": USAGE_LIMIT,
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
            - **Controle de Uso: Limitado a 1000 processamentos por mês (Free Tier) para modo normal; 50 para teste**
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