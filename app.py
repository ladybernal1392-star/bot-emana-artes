import os
import re
import json
import threading
import urllib.parse
from datetime import datetime, timezone, timedelta
import requests
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, request
from google import genai
from google.genai import types

app = Flask(__name__)

# --- CREDENCIALES DESDE VARIABLES DE ENTORNO (RENDER) ---
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "bot_bogota_2026")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Alertas (Telegram y WhatsApp)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Control_Ventas_Bot")

client = genai.Client(api_key=GEMINI_API_KEY)

# Memoria de sesiones, control de pausas y temporizadores de embudo
sesiones_chat = {}
chats_pausados = set()
clientes_contados_hoy = set()
temporizadores_embudo = {}

# --- CATÁLOGO Y REGLAS DE NEGOCIO (4 CANDADOS INTEGRADOS) ---
INSTRUCCIONES_SISTEMA = """
Eres un asesor comercial amable, cercano y persuasivo de Emana Artes, una tienda de decoración en Bogotá, Colombia.
Respondes mensajes en Facebook Messenger. Adapta tu tono al cliente (cercano y respetuoso), usa párrafos cortos y claros.

=== CANDADOS ESTRICTOS DE SEGURIDAD ===
1. CATÁLOGO CERRADO: Solo puedes vender los productos, medidas, colores y precios exactos listados abajo. JAMÁS inventes productos, medidas, descuentos ni promociones que no estén escritos aquí.
2. COBERTURA GEOGRÁFICA (BOGOTÁ Y SOACHA): Solo gestionas entregas directas en Bogotá D.C. y Soacha. Si el cliente pide envío a cualquier otra ciudad o municipio fuera de Bogotá/Soacha:
   - NO inventes tarifas ni tiempos de envío nacional.
   - Pídele amablemente su ciudad/municipio y su número de WhatsApp para que el encargado de ventas nacionales le cotice el despacho.
   - SOLO cuando el cliente te haya entregado su ciudad y su número de WhatsApp, despídete indicando que un asesor nacional lo contactará de inmediato y escribe al final la etiqueta exacta: [ENVIO_NACIONAL]
3. REGLA DE MAYORISTAS (12 O MÁS UNIDADES): Se considera venta al por mayor a partir de 12 unidades de cualquier producto (incluidas las Pampas Eternizadas). Si piden precio al por mayor o 12 o más unidades:
   - NO des precios mayoristas ni descuentos especiales.
   - Pídele amablemente su nombre completo, producto/cantidad aproximada y número de WhatsApp.
   - SOLO cuando el cliente te entregue esos datos, indícale que un asesor comercial lo contactará y escribe al final la etiqueta exacta: [MAYORISTA]
4. LIQUIDACIÓN Y CIERRE DE PEDIDOS (BOGOTÁ / SOACHA):
   - Calcula siempre el subtotal exacto según cantidad y precio unitario.
   - Costo de domicilio: Bogotá D.C. = $10.000 COP | Soacha = $15.000 COP.
   - ENVÍO GRATIS: Si el subtotal en productos es MAYOR a $250.000 COP, el domicilio es $0 (Gratis).
   - Antes de cerrar, muestra un resumen claro con: Producto(s), Cantidad, Subtotal, Domicilio, Total a pagar y Método de pago elegido.
   - Para confirmar un pedido solicita: Nombre completo, Dirección exacta en Bogotá o Soacha (con barrio), Número de WhatsApp y Método de pago.
   - SOLO cuando el cliente confirme el pedido y haya entregado todos esos datos, despídete confirmando que su pedido quedó agendado y escribe al final la etiqueta exacta: [VENTA_CONFIRMADA]

=== ENVÍO DE FOTOS Y VIDEOS ===
Cuando el cliente pida ver fotos, videos o detalles visuales de un producto (o pregunte si sigue disponible), incluye en tu respuesta las etiquetas correspondientes usando únicamente estas URLs exactas:
- Para enviar una foto usa: [FOTO: url_exacta]
- Para enviar un video usa: [VIDEO: url_exacta]
(Envía máximo 2 fotos por mensaje para no saturar el chat, o el video si piden ver el movimiento/tamaño real).

=== REGLAS DE ENTREGA Y PAGOS ===
- Tiempos de entrega: Entrega al día hábil siguiente si se confirma el pedido antes de las 6:00 PM (sujeto a cantidad de pedidos en cola).
- Métodos de pago: Pago contraentrega en Bogotá y Soacha mediante efectivo, QR, Bre-B, Nequi, Daviplata o transferencia Bancolombia.

=== CATÁLOGO OFICIAL DE PRODUCTOS ===

1. Pampas Eternizadas
- Precio: $12.900 COP por unidad/tallo (máximo 11 tallos por este canal; 12 o más es mayorista).
- Características: Pampa eternizada 100%, textura suave y plumosa de gran volumen, larga duración, no requiere agua ni mantenimiento. Altura aprox: 1,70 metros. (Sugiere llevar mínimo 5 tallos para un ramo frondoso).
- Color: Natural (tonos beige y crema).
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Pampas%20Hall.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Pampas%20Sala%202.jp
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Pampas%20Sala.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Pampas%20en%20Cantidad.jpeg
- Video:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Pampas%20Video.mp4

2. Jarrón en Mimbre 40 cm
- Precio: $40.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 40 cm, color natural del mimbre.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%201.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%202.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas_detallado.webp

3. Jarrón en Mimbre 50 cm
- Precio: $48.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 50 cm, color natural del mimbre.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%201.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%202.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas_detallado.webp

4. Jarrón en Mimbre 60 cm
- Precio: $55.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 60 cm, color natural del mimbre.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%201.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%202.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas_detallado.webp

5. Jarrón en Mimbre 80 cm
- Precio: $75.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 80 cm, color natural del mimbre.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%201.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarron%20Mimbre%202.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Jarrones%20Medidas_detallado.webp

6. Lámpara Boho Mimbre 40 cm
- Precio: $45.000 COP por unidad.
- Características: Tejida 100% a mano en mimbre natural, altura 40 cm, color natural. IMPORTANTE: NO incluye plafón del techo ni bombillo.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/lampara%20en%20mimbre%20Sala.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/lampara%20en%20mimbre%20comedor.jpg

7. Cojines Decorativos (Variedad)
- Precios escalonados (hasta 11 unidades):
  * De 1 a 2 unidades: $27.000 COP cada uno.
  * De 3 a 11 unidades: $25.000 COP cada uno.
- Características: Funda extraíble para fácil lavado, medidas 48 cm x 48 cm.
- Estilos/texturas: Tela suave con acabado marmolado y vetas doradas, tela acanalada ultra suave, peluche peludo de pelo largo y diseños estampados de temporada.
- Fotos:
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Cojines%20Variedad-1.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Cojines%20Variedad-2.jpeg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Cojines%20Variedad-3.jpg
https://github.com/ladybernal1392-star/bot-emana-artes/blob/main/Cojines%20Variedad.jpeg

=== PREGUNTAS FRECUENTES ===
- Tienda física / Ubicación: Somos tienda virtual con bodega en Bogotá; realizamos únicamente despachos a domicilio (no tenemos punto físico).
- Cambios o devoluciones: Se aceptan dentro de los 5 días calendario siguientes a la entrega únicamente por defectos de fábrica, sin uso y en su empaque original.
"""

def hora_colombia():
    tz_bogota = timezone(timedelta(hours=-5))
    return datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")

def conectar_google_sheets():
    if not GOOGLE_SHEETS_CREDENTIALS:
        return None
    creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open(SPREADSHEET_NAME)

def registrar_en_sheets(pestana, fila):
    try:
        libro = conectar_google_sheets()
        if libro:
            libro.worksheet(pestana).append_row(fila)
    except Exception as e:
        print(f"Error guardando en Google Sheets ({pestana}): {e}")

def actualizar_metrica_diaria(sender_id):
    try:
        libro = conectar_google_sheets()
        if not libro:
            return
        tz_bogota = timezone(timedelta(hours=-5))
        fecha_hoy = datetime.now(tz_bogota).strftime("%Y-%m-%d")
        llave_cliente = f"{fecha_hoy}_{sender_id}"

        es_cliente_nuevo_hoy = llave_cliente not in clientes_contados_hoy
        if es_cliente_nuevo_hoy:
            clientes_contados_hoy.add(llave_cliente)

        sheet = libro.worksheet("Metricas_Diarias")
        fechas = sheet.col_values(1)
        if fecha_hoy in fechas:
            fila_idx = fechas.index(fecha_hoy) + 1
            clientes_act = int(sheet.cell(fila_idx, 2).value or 0)
            mensajes_act = int(sheet.cell(fila_idx, 3).value or 0)
            sheet.update(f"B{fila_idx}:C{fila_idx}", [[
                clientes_act + (1 if es_cliente_nuevo_hoy else 0),
                mensajes_act + 1
            ]])
        else:
            sheet.append_row([fecha_hoy, 1, 1])
    except Exception as e:
        print(f"Error actualizando métrica diaria: {e}")

def clasificar_y_guardar_embudo(sender_id, evento_forzado=None):
    """Clasifica la conversación en una de las 5 categorías de estadística en Google Sheets."""
    sesion = sesiones_chat.get(sender_id)
    if not sesion or sesion.get("clasificado"):
        return

    sesion["clasificado"] = True
    historial_texto = "\n".join(sesion["historial"])
    total_mensajes_cliente = len([m for m in sesion["historial"] if m.startswith("Cliente:")])

    prompt = f"""
    Analiza esta conversación de ventas y clasifícala en formato JSON puro (sin markdown).
    Evento del sistema detectado (si aplica): {evento_forzado or "Ninguno (el cliente dejó de responder por 30 minutos)"}
    Cantidad de mensajes enviados por el cliente: {total_mensajes_cliente}

    Elige UNA sola categoría exacta para el campo "categoria_embudo":
    - "1_VENTA_CERRADA" (si el evento fue VENTA_CONFIRMADA)
    - "2_SOLO_PRECIO_SIN_RESPUESTA" (si el cliente envió solo 1 mensaje inicial como '¿sigue disponible?' o 'precio' y nunca más respondió)
    - "3_COTIZO_PERO_NO_CONCRETO" (si el cliente envió 2 o más mensajes conversando o cotizando envío/fotos, pero abandonó antes de cerrar compra)
    - "4_DERIVADO_NACIONAL" (si el evento fue ENVIO_NACIONAL o pidió envío fuera de Bogotá/Soacha)
    - "5_DERIVADO_MAYORISTA" (si el evento fue MAYORISTA o pidió 12+ unidades)

    Campos JSON requeridos:
    - "producto_interes": (Ej: Pampas Eternizadas, Jarrón 60cm, Cojines, etc.)
    - "categoria_embudo": (una de las 5 opciones exactas)
    - "motivo_detectado": (frase corta de máximo 8 palabras explicando qué pasó, ej: "Preguntó precio y dejó en visto" o "Se detuvo tras conocer valor de domicilio")
    - "estado_final": ("Cerrado" si hubo venta/derivación, o "Abandonado" si dejó de escribir)

    Conversación:
    {historial_texto}
    """
    try:
        resp = client.models.generate_content(
          model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0)
        )
        datos = json.loads(resp.text.replace("```json", "").replace("```", "").strip())
        registrar_en_sheets("Embudo_y_Estadisticas", [
            hora_colombia(),
            sender_id,
            datos.get("producto_interes", "No identificado"),
            datos.get("categoria_embudo", "3_COTIZO_PERO_NO_CONCRETO"),
            datos.get("motivo_detectado", "Sin respuesta tras 30 min"),
            total_mensajes_cliente,
            datos.get("estado_final", "Abandonado")
        ])
    except Exception as e:
        print(f"Error clasificando embudo: {e}")

def reiniciar_temporizador_embudo(sender_id):
    """Programa la evaluación automática si el cliente deja de escribir por 30 minutos (1800 seg)."""
    if sender_id in temporizadores_embudo:
        temporizadores_embudo[sender_id].cancel()
    timer = threading.Timer(1800.0, clasificar_y_guardar_embudo, args=[sender_id])
    timer.daemon = True
    timer.start()
    temporizadores_embudo[sender_id] = timer

def extraer_resumen_datos(historial_texto, tipo_evento):
    prompt = f"""
    Analiza la siguiente conversación y extrae los datos del cliente en formato JSON puro (sin bloques markdown).
    Tipo de evento: {tipo_evento}
    Campos requeridos:
    - nombre (si no lo dio, pon "No especificado")
    - telefono (si no lo dio, pon "No especificado")
    - ciudad_direccion (ciudad, barrio o dirección completa)
    - detalle_pedido (productos, cantidades, total y método de pago si aplica)

    Conversación:
    {historial_texto}
    """
    try:
        resp = client.models.generate_content(
           model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.0)
        )
        return json.loads(resp.text.replace("```json", "").replace("```", "").strip())
    except Exception:
        return {
            "nombre": "Revisar chat",
            "telefono": "Revisar chat",
            "ciudad_direccion": "Revisar chat",
            "detalle_pedido": historial_texto[-300:]
        }

def enviar_alertas(titulo, mensaje_alerta):
    texto_completo = f"🚨 *{titulo}*\n🕒 {hora_colombia()}\n\n{mensaje_alerta}"

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url_tg = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url_tg, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": texto_completo,
                "parse_mode": "Markdown"
            }, timeout=10)
        except Exception as e:
            print(f"Error Telegram: {e}")

    if CALLMEBOT_PHONE and CALLMEBOT_APIKEY:
        try:
            texto_cod = urllib.parse.quote(texto_completo.replace("*", ""))
            url_wa = f"https://api.callmebot.com/whatsapp.php?phone={CALLMEBOT_PHONE}&text={texto_cod}&apikey={CALLMEBOT_APIKEY}"
            requests.get(url_wa, timeout=10)
        except Exception as e:
            print(f"Error WhatsApp: {e}")

def enviar_a_messenger(recipient_id, payload_message):
    url = f"https://graph.facebook.com/v21.0/me/messages?access_token={PAGE_ACCESS_TOKEN}"
    headers = {"Content-Type": "application/json"}
    data = {"recipient": {"id": recipient_id}, "message": payload_message}
    resp = requests.post(url, json=data, headers=headers)
    if resp.status_code != 200:
        print(f"Error Messenger API: {resp.text}")

def procesar_respuesta_bot(sender_id, mensaje_usuario):
    if sender_id not in sesiones_chat:
        sesiones_chat[sender_id] = {
            "chat": client.chats.create(
                model="gemini-3.1-flash-lite",
                config=types.GenerateContentConfig(
                    system_instruction=INSTRUCCIONES_SISTEMA,
                    temperature=0.1,
                )
            ),
            "historial": [],
            "clasificado": False
        }

    sesion = sesiones_chat[sender_id]
    sesion["historial"].append(f"Cliente: {mensaje_usuario}")

    try:
        respuesta = sesion["chat"].send_message(mensaje_usuario)
        texto_ia = respuesta.text
        sesion["historial"].append(f"Bot: {texto_ia}")
    except Exception as e:
        print(f"Error Gemini: {e}")
        enviar_a_messenger(sender_id, {"text": "Hola, dame un momento por favor, ya te atiendo."})
        return

    evento_detectado = None
    if "[VENTA_CONFIRMADA]" in texto_ia:
        evento_detectado = "VENTA_CONFIRMADA"
        texto_ia = texto_ia.replace("[VENTA_CONFIRMADA]", "")
    elif "[MAYORISTA]" in texto_ia:
        evento_detectado = "MAYORISTA"
        texto_ia = texto_ia.replace("[MAYORISTA]", "")
    elif "[ENVIO_NACIONAL]" in texto_ia:
        evento_detectado = "ENVIO_NACIONAL"
        texto_ia = texto_ia.replace("[ENVIO_NACIONAL]", "")

    fotos = re.findall(r"\[FOTO:\s*(https?://[^\]]+)\]", texto_ia)
    videos = re.findall(r"\[VIDEO:\s*(https?://[^\]]+)\]", texto_ia)

    texto_limpio = re.sub(r"\[FOTO:\s*https?://[^\]]+\]", "", texto_ia)
    texto_limpio = re.sub(r"\[VIDEO:\s*https?://[^\]]+\]", "", texto_limpio).strip()

    if texto_limpio:
        enviar_a_messenger(sender_id, {"text": texto_limpio})
        actualizar_metrica_diaria(sender_id)

    for url_foto in fotos:
        enviar_a_messenger(sender_id, {
            "attachment": {"type": "image", "payload": {"url": url_foto.strip(), "is_reusable": True}}
        })

    for url_video in videos:
        enviar_a_messenger(sender_id, {
            "attachment": {"type": "video", "payload": {"url": url_video.strip(), "is_reusable": True}}
        })

    if evento_detectado:
        if sender_id in temporizadores_embudo:
            temporizadores_embudo[sender_id].cancel()
        chats_pausados.add(sender_id)
        clasificar_y_guardar_embudo(sender_id, evento_forzado=evento_detectado)

        historial_completo = "\n".join(sesion["historial"])
        datos = extraer_resumen_datos(historial_completo, evento_detectado)

        resumen_msg = (
            f"👤 *Cliente:* {datos.get('nombre')}\n"
            f"📱 *WhatsApp:* {datos.get('telefono')}\n"
            f"📍 *Ubicación:* {datos.get('ciudad_direccion')}\n"
            f"📦 *Detalle:* {datos.get('detalle_pedido')}\n"
            f"🆔 *ID Messenger:* {sender_id}"
        )

        if evento_detectado == "VENTA_CONFIRMADA":
            enviar_alertas("NUEVO PEDIDO CONFIRMADO (BOGOTÁ/SOACHA)", resumen_msg)
            registrar_en_sheets("Pedidos_Confirmados", [
                hora_colombia(), sender_id, datos.get("nombre"), datos.get("telefono"),
                datos.get("ciudad_direccion"), datos.get("detalle_pedido"), "Por Despachar"
            ])
        elif evento_detectado == "MAYORISTA":
            enviar_alertas("SOLICITUD CLIENTE MAYORISTA (+12 UNDS)", resumen_msg)
            registrar_en_sheets("Interacciones_y_Mayoristas", [
                hora_colombia(), "MAYORISTA", sender_id, datos.get("nombre"),
                datos.get("telefono"), datos.get("ciudad_direccion"), datos.get("detalle_pedido")
            ])
        elif evento_detectado == "ENVIO_NACIONAL":
            enviar_alertas("CLIENTE ENVÍO NACIONAL (FUERA DE BOGOTÁ)", resumen_msg)
            registrar_en_sheets("Interacciones_y_Mayoristas", [
                hora_colombia(), "ENVIO NACIONAL", sender_id, datos.get("nombre"),
                datos.get("telefono"), datos.get("ciudad_direccion"), datos.get("detalle_pedido")
            ])
    else:
        reiniciar_temporizador_embudo(sender_id)

# --- RUTAS DEL SERVIDOR WEBHOOK ---

@app.route("/", methods=["GET"])
def home():
    return "Servidor Bot Emana Artes Activo ✅", 200

@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Token inválido", 403

@app.route("/webhook", methods=["POST"])
def recibir_mensajes():
    data = request.get_json()
    if data.get("object") == "page":
        for entry in data.get("entry", []):
            for event in entry.get("messaging", []):
                if event.get("message") and event["message"].get("is_echo"):
                    texto_admin = event["message"].get("text", "").strip().lower()
                    recipient_id = event["recipient"]["id"]
                    if texto_admin == "/activar" and recipient_id in chats_pausados:
                        chats_pausados.remove(recipient_id)
                        if recipient_id in sesiones_chat:
                            sesiones_chat[recipient_id]["clasificado"] = False
                    continue

                sender_id = event.get("sender", {}).get("id")
                if not sender_id or sender_id in chats_pausados:
                    continue

                if event.get("postback"):
                    titulo_boton = event["postback"].get("title", "")
                    ref_producto = event["postback"].get("referral", {}).get("ref", "")
                    mensaje_armado = f"{titulo_boton} (Referencia de publicación: {ref_producto})" if ref_producto else titulo_boton
                    if mensaje_armado:
                        threading.Thread(
                            target=procesar_respuesta_bot,
                            args=(sender_id, mensaje_armado),
                            daemon=True
                        ).start()
                    continue

                if event.get("message") and not event["message"].get("is_echo"):
                    texto = event["message"].get("text", "")
                    adjuntos = event["message"].get("attachments", [])
                    contexto_producto = ""

                    for adj in adjuntos:
                        titulo_adj = adj.get("title") or adj.get("payload", {}).get("title")
                        if titulo_adj:
                            contexto_producto += f" [El cliente consulta específicamente por la publicación/producto: '{titulo_adj}'. Enfócate solo en este artículo.]"

                    if texto or contexto_producto:
                        mensaje_final = f"{texto}{contexto_producto}".strip()
                        threading.Thread(
                            target=procesar_respuesta_bot,
                            args=(sender_id, mensaje_final),
                            daemon=True
                        ).start()
                    elif adjuntos:
                        threading.Thread(
                            target=enviar_a_messenger,
                            args=(sender_id, {"text": "Recibí tu archivo. ¿Me cuentas por texto qué referencia te gustaría cotizar o pedir?"}),
                            daemon=True
                        ).start()

        return "EVENT_RECEIVED", 200
    return "Not Found", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
