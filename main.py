import streamlit as st
import easyocr
import cv2
import numpy as np
from PIL import Image
import time
import io

# página
st.set_page_config(page_title="Visualizador EasyOCR", page_icon="🔍", layout="wide")
st.title("🔍 Visualizador de Processamento de Imagens com EasyOCR")
st.markdown("Esta ferramenta visualiza o processo de OCR em etapas, com bounding boxes e logs de tempo para aprendizado e depuração.")

st.sidebar.header("Configurações")
langs = st.sidebar.multiselect(
    "Idiomas para OCR (ex: 'en' para inglês, 'pt' para português)",
    ['en', 'pt'],
    default=['en', 'pt']
)
bbox_threshold = st.sidebar.slider("Threshold para Bounding Boxes (0.0-1.0)", 0.0, 1.0, 0.5)

# upload de imagem
uploaded_file = st.file_uploader("Carregue uma imagem (.jpg, .png, etc.)", type=['jpg', 'jpeg', 'png'])
if uploaded_file is not None:
    # converter para OpenCV
    image = Image.open(uploaded_file)
    image_cv = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    st.image(image, caption="Imagem Carregada", use_column_width=True)

    # Botão para processar
    if st.button("Iniciar Processamento"):
        with st.spinner("Carregando modelo EasyOCR... (pode demorar na primeira vez)"):
            reader = easyocr.Reader(langs, gpu=False)  # gpu=True se tiver CUDA

        # etapa 1: imagem original
        st.subheader("Etapa 1: Imagem Original")
        tempo_inicio = time.time()
        st.image(image, caption="Imagem Original", use_column_width=True)
        tempo_fim = time.time()
        st.info(f"Tempo: {tempo_fim - tempo_inicio:.3f} segundos")

        # etapa 2: pré-processamento (exemplo simples: escala de cinza)
        st.subheader("Etapa 2: Pré-processamento (Escala de Cinza)")
        tempo_inicio = time.time()
        image_gray = cv2.cvtColor(image_cv, cv2.COLOR_BGR2GRAY)
        image_gray_rgb = cv2.cvtColor(image_gray, cv2.COLOR_GRAY2RGB)
        st.image(Image.fromarray(image_gray_rgb), caption="Imagem em Escala de Cinza", use_column_width=True)
        tempo_fim = time.time()
        st.info(f"Tempo: {tempo_fim - tempo_inicio:.3f} segundos")
        st.markdown("*Nota: EasyOCR faz pré-processamento interno; aqui mostramos um exemplo manual para visualização.*")

        # etapa 3: detecção de texto (bounding boxes)
        st.subheader("Etapa 3: Detecção de Texto (Bounding Boxes)")
        tempo_inicio = time.time()
        results = reader.readtext(image_gray, detail=1, paragraph=False, width_ths=0.7, height_ths=0.7, 
                                  decoder='greedy', beamWidth=5, batch_size=1, workers=0, 
                                  allowlist=None, blocklist=None, rotation_info=None, 
                                  min_size=20, slope_ths=0.1, ycenter_ths=0.5, 
                                  add_margin=0.1, output_format='standard')
        results_filtered = [(bbox, text, prob) for (bbox, text, prob) in results if prob > bbox_threshold]
        tempo_fim = time.time()

        # pergunttar claude
        # Desenhar bounding boxes na imagem original
        image_with_bbox = image_cv.copy()
        for (bbox, text, prob) in results_filtered:
            # bbox é [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            pts = np.array(bbox, np.int32).reshape((-1, 1, 2))
            cv2.polylines(image_with_bbox, [pts], True, (0, 255, 0), 2)  # Verde para retângulo
            # calcular centro para texto
            (tlx, tly) = bbox[0]
            cv2.putText(image_with_bbox, f"{text} ({prob:.2f})", (int(tlx), int(tly)-10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        # converter para RGB
        image_with_bbox_rgb = cv2.cvtColor(image_with_bbox, cv2.COLOR_BGR2RGB)
        st.image(Image.fromarray(image_with_bbox_rgb), caption="Imagem com Bounding Boxes e Texto Detectado", use_column_width=True)
        st.info(f"Tempo de Detecção + Reconhecimento: {tempo_fim - tempo_inicio:.3f} segundos")
        st.markdown(f"*Detectados {len(results_filtered)} blocos de texto com probabilidade > {bbox_threshold}*")

        # etapa 4: reconhecimento de texto (detalhes)
        st.subheader("Etapa 4: Reconhecimento de Texto")
        for i, (bbox, text, prob) in enumerate(results_filtered):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**Bloco {i+1}:**")
                st.write(f"Texto: {text}")
                st.write(f"Probabilidade: {prob:.2f}")
            with col2:
                # recortar a região do bbox para visualização (opcional, para depuração)
                x_coords = [point[0] for point in bbox]
                y_coords = [point[1] for point in bbox]
                x_min, x_max = int(min(x_coords)), int(max(x_coords))
                y_min, y_max = int(min(y_coords)), int(max(y_coords))
                cropped = image_cv[y_min:y_max, x_min:x_max]
                if cropped.size > 0:
                    cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
                    st.image(Image.fromarray(cropped_rgb), caption=f"Região Cortada do Bloco {i+1}", use_column_width=True)

        # etapa 5: resultado final
        st.subheader("Etapa 5: Resultado Final")
        texto_extraido_total = "\n".join([text for (bbox, text, prob) in results_filtered])
        st.text_area("Texto Extraído Completo", texto_extraido_total, height=200)
        tempo_total = time.time() - tempo_start_total
        st.success(f"Tempo Total de Processamento: {tempo_total:.3f} segundos")

        # log de desempenho
        st.subheader("Log de Desempenho")
        st.json({
            "Idiomas Usados": langs,
            "Threshold BBox": bbox_threshold,
            "Número de Resultados": len(results_filtered),
            "Tempos por Etapa": {
                "1 - Original": "Instantâneo",
                "2 - Pré-processamento": "Rápido (manual)",
                "3-4 - OCR Completo": f"{tempo_fim - tempo_inicio:.3f}s",
                "Total": f"{tempo_total:.3f}s"
            }
        })

else:
    st.info("Upload de imagem!")
    st.markdown("Exemplo de uso: Imagens com texto claro funcionam melhor.")
