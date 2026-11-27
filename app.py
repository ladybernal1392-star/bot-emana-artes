import os
import re
import json
import time
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

# Configuración Multi-Página (EmanaTrend, Emana Trend y futura 3ra página sin tocar código)
PAGE_ID_1 = os.environ.get("PAGE_ID_EMANA_ARTE", "").strip()
PAGE_NAME_1 = os.environ.get("PAGE_NAME_EMANA_ARTE", "EmanaTrend").strip()
PAGE_TOKEN_1 = os.environ.get("PAGE_TOKEN_EMANA_ARTE", "").strip()

PAGE_ID_2 = os.environ.get("PAGE_ID_EMANA_TREND", "").strip()
PAGE_NAME_2 = os.environ.get("PAGE_NAME_EMANA_TREND", "Emana Trend").strip()
PAGE_TOKEN_2 = os.environ.get("PAGE_TOKEN_EMANA_TREND", "").strip()

PAGE_ID_3 = os.environ.get("PAGE_ID_EMANA_3", "").strip()
PAGE_NAME_3 = os.environ.get("PAGE_NAME_EMANA_3", "Emana Artes").strip()
PAGE_TOKEN_3 = os.environ.get("PAGE_TOKEN_EMANA_3", "").strip()

PAGINAS_CONFIG = {}
for pid, pname, ptoken in (
    (PAGE_ID_1, PAGE_NAME_1, PAGE_TOKEN_1),
    (PAGE_ID_2, PAGE_NAME_2, PAGE_TOKEN_2),
    (PAGE_ID_3, PAGE_NAME_3, PAGE_TOKEN_3),
):
    if pid and ptoken:
        PAGINAS_CONFIG[pid] = {"nombre": pname, "token": ptoken}

def obtener_info_pagina(page_id):
    """Devuelve nombre y token de la página origen, con respaldo a PAGE_ACCESS_TOKEN."""
    if page_id and page_id in PAGINAS_CONFIG:
        return PAGINAS_CONFIG[page_id]["nombre"], PAGINAS_CONFIG[page_id]["token"]
    return "EmanaTrend", PAGE_ACCESS_TOKEN

# Alertas (Telegram y WhatsApp)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "")

# WhatsApp Atención Personalizada
WHATSAPP_ATENCION = "https://wa.me/573229640360"

# Google Sheets
GOOGLE_SHEETS_CREDENTIALS = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Control_Ventas_Bot")

client = genai.Client(api_key=GEMINI_API_KEY)

# Modelos IA (Primario y Respaldo por turno ante saturación 503/429)
MODELO_PRIMARIO = "gemini-3.1-flash-lite"
MODELO_RESPALDO = "gemini-2.5-flash"

# Memoria de sesiones y temporizadores de embudo/remarketing/recuperación (SIN PAUSAS NI STANDBY)
sesiones_chat = {}
clientes_contados_hoy = set()
temporizadores_embudo = {}
temporizadores_remarketing = {}
temporizadores_recuperacion = {}

# --- CATÁLOGO Y REGLAS DE NEGOCIO (CANDADOS INTEGRADOS) ---
INSTRUCCIONES_SISTEMA = """

Eres un asesor comercial amable, cercano y persuasivo de {NOMBRE_TIENDA}, una tienda de decoración en Bogotá, Colombia.
Respondes mensajes en Facebook Messenger.

=== ESTILO Y EXTENSIÓN EQUILIBRADA (OBLIGATORIO) ===
- Extensión ideal: Responde en máximo 2 párrafos muy breves (3 a 5 líneas en total). Ni frío/cortante, ni bloques largos de texto.
- Claridad y refuerzo comercial: Puedes mencionar o recordar datos clave (como el pago contraentrega o beneficios del producto) de forma muy breve y natural, sin extenderte.
- Estructura limpia: Usa viñetas cortas solo para listar precios o resúmenes de compra, y cierra siempre con UNA sola pregunta breve para avanzar hacia el pedido.


=== CANDADOS ESTRICTOS DE SEGURIDAD ===
1. CATÁLOGO CERRADO: Solo puedes vender los productos, medidas, colores y precios exactos listados abajo. JAMÁS inventes productos, medidas, descuentos ni promociones que no estén escritos aquí.
2. COBERTURA GEOGRÁFICA (BOGOTÁ Y SOACHA): Solo gestionas entregas directas en Bogotá D.C. y Soacha. Si el cliente pide envío a cualquier otra ciudad o municipio fuera de Bogotá/Soacha:
  - NO inventes tarifas ni tiempos de envío nacional.
  - Pídele amablemente su ciudad/municipio y su número de WhatsApp para que el encargado de ventas nacionales le cotice el despacho, o compártele nuestro WhatsApp de atención personalizada: https://wa.me/573229640360
  - SOLO cuando el cliente te haya entregado su ciudad y su número de WhatsApp, indícale que pasará a chat especializado para cotizar su envío nacional y escribe al final la etiqueta exacta: [ENVIO_NACIONAL]
3. REGLA DE MAYORISTAS (12 O MÁS UNIDADES): Se considera venta al por mayor a partir de 12 unidades de cualquier producto (incluidas las Pampas Eternizadas). Si piden precio al por mayor o 12 o más unidades:
  - NO des precios mayoristas ni descuentos especiales.
  - Pídele amablemente su nombre completo, producto/cantidad aproximada y número de WhatsApp, o compártele el enlace de WhatsApp: https://wa.me/573229640360
  - SOLO cuando el cliente te entregue esos datos, indícale que pasará a chat especializado con un asesor mayorista y escribe al final la etiqueta exacta: [MAYORISTA]
4. LIQUIDACIÓN Y CIERRE DE PEDIDOS (BOGOTÁ / SOACHA):
  - Recuerda mencionar siempre como beneficio que en Bogotá D.C. y Soacha manejamos **pago contraentrega** (pagas al recibir tu pedido en casa).
  - Calcula siempre el subtotal exacto según cantidad y precio unitario.
  - Costo de domicilio: Bogotá D.C. = $10.000 COP | Soacha = $15.000 COP.
  - ENVÍO GRATIS: Si el subtotal en productos es MAYOR a $250.000 COP, el domicilio es $0 (Gratis).
  - Antes de cerrar, muestra un resumen claro con: Producto(s), Cantidad, Subtotal, Domicilio, Total a pagar y Método de pago elegido.
  - Para confirmar un pedido solicita: Nombre completo, Dirección exacta en Bogotá o Soacha (con barrio), Número de WhatsApp y Método de pago.
  - SOLO cuando el cliente confirme el pedido y haya entregado todos esos datos, despídete confirmando que su pedido quedó agendado y escribe al final la etiqueta exacta: [VENTA_CONFIRMADA]
  - POST-VENTA Y DUDAS POSTERIORES: Si el cliente vuelve a escribir después de haber confirmado su pedido (para preguntar por horarios, medios de pago o estado), respóndele amablemente usando los datos que ya te entregó sin volver a pedirlos ni repetir la etiqueta [VENTA_CONFIRMADA]. Emite [VENTA_CONFIRMADA] nuevamente SOLO si el cliente pide modificar o agregar productos a su pedido ya confirmado.
5. ATENCIÓN ESPECIALIZADA (COJINES POR COLOR/MODELO ESPECÍFICO O ASESORÍA PERSONALIZADA):
  - Si el cliente pide un color, textura o diseño específico de los cojines decorativos (para verificar disponibilidad puntual en bodega), o solicita atención personalizada/especializada:
  - Infórmale con amabilidad que pasará a chat especializado para verificar disponibilidad exacta y enviarle opciones disponibles.
  - Compártele nuestro WhatsApp directo de atención personalizada: https://wa.me/573229640360 e indícale que también puede dejarnos su nombre y WhatsApp por aquí para escribirle de inmediato.
  - Escribe al final de esa respuesta la etiqueta exacta: [ATENCION_ESPECIALIZADA]
6. DETECCIÓN DE RECHAZO O DESINTERÉS:
  - Si el cliente indica explícitamente que no está interesado, que le parece muy caro, que no lo quiere o que desiste de la compra, despídete de forma muy cordial dejando las puertas abiertas (y nuestro WhatsApp https://wa.me/573229640360 por si más adelante se anima) y escribe al final la etiqueta exacta: [NO_INTERESADO]

=== ENVÍO DE FOTOS Y VIDEOS ===
Cuando el cliente pida ver fotos o detalles visuales de un producto (o pregunte si sigue disponible), incluye en tu respuesta las etiquetas correspondientes usando únicamente estas URLs exactas:
- Para enviar una foto usa: [FOTO: url_exacta]
(Envía máximo 2 fotos por mensaje para no saturar el chat. Si piden video, compárteles directamente el enlace de Instagram en el texto, NUNCA uses la etiqueta [VIDEO:]).

=== REGLAS DE ENTREGA Y PAGOS ===
- Pago contraentrega: En Bogotá D.C. y Soacha realizamos pagos 100% contraentrega (pagas al momento de recibir el producto).
- Tiempos de entrega: Entrega al día hábil siguiente si se confirma el pedido antes de las 6:00 PM (sujeto a cantidad de pedidos en cola).
- Métodos de pago contraentrega: Efectivo, QR, Bre-B, Nequi, Daviplata o transferencia Bancolombia.
- Atención personalizada por WhatsApp: https://wa.me/573229640360

=== CATÁLOGO OFICIAL DE PRODUCTOS ===

1. Pampas Eternizadas
- Precio: $12.900 COP por unidad/tallo (máximo 11 tallos por este canal; 12 o más es mayorista).
- Características: Pampa eternizada 100%, textura suave y plumosa de gran volumen, larga duración, no requiere agua ni mantenimiento. Altura aprox: 1,70 metros. (Sugiere llevar mínimo 5 tallos para un ramo frondoso).
- Color: Natural (tonos beige y crema).
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Pampas%20Hall.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Pampas%20Sala%202.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Pampas%20Sala.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Pampas%20en%20Cantidad.jpeg
- Video (SIEMPRE que el cliente pregunte o pida Pampas Eternizadas, incluye obligatoriamente en el texto de tu respuesta este enlace de Instagram para que se previsualice en el chat, NO uses etiqueta [VIDEO:]):
  https://www.instagram.com/reel/DeiSH9SNaiy/?jplx=M3dsemJvbG9vZzFh

2. Jarrón en Mimbre 40 cm
- Precio: $40.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 40 cm, color natural del mimbre.
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%201.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%202.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarrones%20Medidas.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Medidas%20Jarrones.jpeg

3. Jarrón en Mimbre 50 cm
- Precio: $48.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 50 cm, color natural del mimbre.
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%201.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%202.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarrones%20Medidas.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Medidas%20Jarrones.jpeg

4. Jarrón en Mimbre 60 cm
- Precio: $55.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 60 cm, color natural del mimbre.
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%201.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%202.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarrones%20Medidas.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Medidas%20Jarrones.jpeg

5. Jarrón en Mimbre 80 cm
- Precio: $75.000 COP por unidad.
- Características: Tejido 100% a mano en mimbre natural, altura 80 cm, color natural del mimbre.
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%201.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarron%20Mimbre%202.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Jarrones%20Medidas.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Medidas%20Jarrones.jpeg

6. Lámpara Boho Mimbre 40 cm
- Precio: $45.000 COP por unidad.
- Características: Tejida 100% a mano en mimbre natural, altura 40 cm, color natural. Incluye cable de 70 cm. IMPORTANTE: NO incluye plafón del techo ni bombillo.
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/lampara%20en%20mimbre%20Sala.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/lampara%20en%20mimbre%20comedor.jpg

7. Cojines Decorativos (Variedad)
- Precios escalonados (hasta 11 unidades):
  - De 1 a 2 unidades: $27.000 COP cada uno.
  - De 3 a 11 unidades: $25.000 COP cada uno.
- Características: Funda extraíble para fácil lavado, medidas 48 cm x 48 cm.
- Estilos/texturas: Tela suave con acabado marmolado y vetas doradas, tela acanalada ultra suave, peluche peludo de pelo largo y diseños estampados de temporada.
- Nota importante: Si el cliente pide un color o modelo específico, activa la regla 5 ([ATENCION_ESPECIALIZADA]).
- Fotos:
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Cojines%20Variedad-1.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Cojines%20Variedad-2.jpeg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Cojines%20Variedad-3.jpg
  https://raw.githubusercontent.com/ladybernal1392-star/bot-emana-artes/main/Cojines%20Variedad.jpeg
  
=== PREGUNTAS FRECUENTES ===
- Tienda física / Ubicación: Somos tienda virtual con bodega en Bogotá; realizamos únicamente despachos a domicilio con pago contraentrega en Bogotá y Soacha (no tenemos punto físico).
- Cambios o devoluciones: Se aceptan dentro de los 5 días calendario siguientes a la entrega únicamente por defectos de fábrica, sin uso y en su empaque original.
- Contacto directo WhatsApp: https://wa.me/573229640360
"""

def hora_colombia():
    tz_bogota = timezone(timedelta(hours=-5))
    return datetime.now(tz_bogota).strftime("%Y-%m-%d %H:%M:%S")

def llamar_gemini_con_respaldo(contents, system_instruction=None, temperature=0.1):
    """Intenta 1 vez en MODELO_PRIMARIO y si hay saturación/cuota intenta 1 vez en MODELO_RESPALDO."""
    config = types.GenerateContentConfig(temperature=temperature)
    if system_instruction:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature
        )

    try:
        resp = client.models.generate_content(
            model=MODELO_PRIMARIO,
            contents=contents,
            config=config
        )
        return resp.text
    except Exception as e:
        err_str = str(e)
        if any(cod in err_str for cod in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED")):
            print(f"Saturación en {MODELO_PRIMARIO} ({err_str[:80]}). Usando respaldo {MODELO_RESPALDO} para este turno...")
            resp = client.models.generate_content(
                model=MODELO_RESPALDO,
                contents=contents,
                config=config
            )
            return resp.text
        raise

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
            fila_limpia = [
                json.dumps(celda, ensure_ascii=False) if isinstance(celda, (dict, list)) else str(celda or "")
                for celda in fila
            ]
            libro.worksheet(pestana).append_row(fila_limpia)
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

def clasificar_y_guardar_embudo(session_key, evento_forzado=None):
    """Clasifica la conversación en una de las 5 categorías de estadística en Google Sheets."""
    sesion = sesiones_chat.get(session_key)
    if not sesion or sesion.get("clasificado"):
        return

    sesion["clasificado"] = True
    sender_id = sesion["sender_id"]
    nombre_pagina = sesion.get("nombre_pagina", "EmanaTrend")
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
- "5_DERIVADO_MAYORISTA" (si el evento fue MAYORISTA, ATENCION_ESPECIALIZADA o pidió 12+ unidades)

    Campos JSON requeridos:
- "producto_interes": (Ej: Pampas Eternizadas, Jarrón 60cm, Cojines, etc.)
- "categoria_embudo": (una de las 5 opciones exactas)
- "motivo_detectado": (frase corta de máximo 8 palabras explicando qué pasó, ej: "Preguntó precio y dejó en visto" o "Se detuvo tras conocer valor de domicilio")
- "estado_final": ("Cerrado" si hubo venta/derivación, o "Abandonado" si dejó de escribir)

    Conversación:
    {historial_texto}
    """
    try:
        texto_resp = llamar_gemini_con_respaldo(contents=prompt, temperature=0.0)
        datos = json.loads(texto_resp.replace("```json", "").replace("```", "").strip())
        registrar_en_sheets("Embudo_y_Estadisticas", [
            hora_colombia(),
            sender_id,
            datos.get("producto_interes", "No identificado"),
            datos.get("categoria_embudo", "3_COTIZO_PERO_NO_CONCRETO"),
            datos.get("motivo_detectado", "Sin respuesta tras 30 min"),
            total_mensajes_cliente,
            datos.get("estado_final", "Abandonado"),
            nombre_pagina
        ])
    except Exception as e:
        print(f"Error clasificando embudo: {e}")

def reiniciar_temporizador_embudo(session_key):
    """Programa la evaluación automática si el cliente deja de escribir por 30 minutos (1800 seg)."""
    if session_key in temporizadores_embudo:
        temporizadores_embudo[session_key].cancel()
    timer = threading.Timer(1800.0, clasificar_y_guardar_embudo, args=[session_key])
    timer.daemon = True
    timer.start()
    temporizadores_embudo[session_key] = timer

def cancelar_remarketing(session_key):
    """Cancela cualquier mensaje de remarketing pendiente para el cliente."""
    timers = temporizadores_remarketing.pop(session_key, None)
    if timers:
        for t in timers.values():
            t.cancel()

def enviar_mensaje_remarketing(session_key, toque):
    """Envía el mensaje de recuperación a 1 hora (toque 1) o a 24 horas (toque 2) si sigue sin cerrar venta."""
    sesion = sesiones_chat.get(session_key)
    if not sesion:
        return
    if sesion.get("venta_registrada") or sesion.get("no_interesado") or sesion.get("derivado"):
        return

    sender_id = sesion["sender_id"]
    page_token = sesion["page_token"]
    nombre_pagina = sesion["nombre_pagina"]

    if toque == 1:
        mensaje_rmk = (
            f"¡Hola! 👋 ¿Te quedó alguna duda sobre lo que vimos en *{nombre_pagina}*? ✨\n"
            f"Recuerda que en Bogotá y Soacha pagas 100% contraentrega. Si prefieres atención directa, escríbenos aquí: {WHATSAPP_ATENCION}"
        )
    else:
        mensaje_rmk = (
            f"¡Hola! 😊 Aún tenemos unidades disponibles en *{nombre_pagina}* para entrega mañana con pago contraentrega en Bogotá o Soacha.\n"
            f"¿Te ayudo a agendar tu pedido o nos escribes por WhatsApp? {WHATSAPP_ATENCION} 🌿"
        )

    enviar_a_messenger(sender_id, {"text": mensaje_rmk}, page_token)
    sesion["historial"].append(f"Bot (Remarketing {toque}): {mensaje_rmk}")

def programar_remarketing(session_key):
    """Programa toque 1 a 1 hora del último mensaje y toque 2 a 23.5 horas del chat inicial."""
    sesion = sesiones_chat.get(session_key)
    if not sesion or sesion.get("venta_registrada") or sesion.get("no_interesado") or sesion.get("derivado"):
        cancelar_remarketing(session_key)
        return

    if session_key not in temporizadores_remarketing:
        temporizadores_remarketing[session_key] = {}

    if "toque_1" in temporizadores_remarketing[session_key]:
        temporizadores_remarketing[session_key]["toque_1"].cancel()
    t1 = threading.Timer(3600.0, enviar_mensaje_remarketing, args=[session_key, 1])
    t1.daemon = True
    t1.start()
    temporizadores_remarketing[session_key]["toque_1"] = t1

    if "toque_2" not in temporizadores_remarketing[session_key]:
        t2 = threading.Timer(84600.0, enviar_mensaje_remarketing, args=[session_key, 2])
        t2.daemon = True
        t2.start()
        temporizadores_remarketing[session_key]["toque_2"] = t2

def cancelar_recuperacion(session_key):
    """Cancela cualquier recuperación pendiente cuando el cliente escribe."""
    timer = temporizadores_recuperacion.pop(session_key, None)
    if timer:
        timer.cancel()
    if session_key in sesiones_chat:
        sesiones_chat[session_key]["pendiente_recuperacion"] = False

def ejecutar_recuperacion_chat(session_key, intento=1):
    """Retoma automáticamente la conversación cuando pasa la saturación de IA sin perder la memoria."""
    sesion = sesiones_chat.get(session_key)
    if not sesion or not sesion.get("pendiente_recuperacion"):
        return
    if sesion.get("no_interesado"):
        cancelar_recuperacion(session_key)
        return

    nombre_pagina = sesion["nombre_pagina"]
    instrucciones_pagina = INSTRUCCIONES_SISTEMA.replace("{NOMBRE_TIENDA}", nombre_pagina)
    historial_formateado = "\n".join(sesion["historial"])
    prompt_recuperacion = (
        "Historial de la conversación hasta ahora:\n"
        f"{historial_formateado}\n\n"
        "INSTRUCCIÓN DE CONTINUIDAD: Hace unos minutos hubo una breve intermitencia mientras atendías el último mensaje del Cliente "
        "y se le envió el resumen preventivo de precios. Retoma la conversación exactamente desde ese punto con naturalidad, "
        "responde la consulta específica que quedó pendiente en el último mensaje del Cliente usando toda la memoria previa "
        "y avanza la venta (sin volver a saludar desde cero ni repetir todo el catálogo):"
    )

    try:
        texto_ia = llamar_gemini_con_respaldo(
            contents=prompt_recuperacion,
            system_instruction=instrucciones_pagina,
            temperature=0.1
        )
        sesion["pendiente_recuperacion"] = False
        temporizadores_recuperacion.pop(session_key, None)
        sesion["historial"].append(f"Bot: {texto_ia}")
        procesar_salida_ia(session_key, sesion, texto_ia)
    except Exception as e:
        print(f"Recuperación intento {intento} aún con saturación para {session_key}: {e}")
        if intento == 1:
            t2 = threading.Timer(180.0, ejecutar_recuperacion_chat, args=[session_key, 2])
            t2.daemon = True
            t2.start()
            temporizadores_recuperacion[session_key] = t2
        else:
            sesion["pendiente_recuperacion"] = False
            temporizadores_recuperacion.pop(session_key, None)

def programar_recuperacion_chat(session_key):
    """Programa el primer intento de recuperación automática a los 3 minutos (180 seg)."""
    cancelar_recuperacion(session_key)
    if session_key in sesiones_chat:
        sesiones_chat[session_key]["pendiente_recuperacion"] = True
    t1 = threading.Timer(180.0, ejecutar_recuperacion_chat, args=[session_key, 1])
    t1.daemon = True
    t1.start()
    temporizadores_recuperacion[session_key] = t1

def extraer_resumen_datos(historial_texto, tipo_evento):
    prompt = f"""
    Analiza la siguiente conversación y extrae los datos del cliente en formato JSON puro (sin bloques markdown).
    Tipo de evento: {tipo_evento}
    Campos requeridos:
- nombre (si no lo dio, pon "No especificado")
- telefono (si no lo dio, pon "No especificado")
- ciudad_direccion (ciudad, barrio o dirección completa)
- detalle_pedido (texto con saltos de línea usando \\n, formato lista estricta por cada producto así:
"• [Cantidad] | [Nombre exacto del artículo] | $[Subtotal del artículo]"
Y en la última línea agrega:
"Domicilio: $[valor] | Total: $[valor] | Pago: [método]" si es pedido confirmado, o los datos relevantes si es mayorista/nacional/especializado)

    Conversación:
    {historial_texto}
    """
    try:
        texto_resp = llamar_gemini_con_respaldo(contents=prompt, temperature=0.0)
        return json.loads(texto_resp.replace("```json", "").replace("```", "").strip())
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

def enviar_a_messenger(recipient_id, payload_message, page_token=None):
    token = page_token or PAGE_ACCESS_TOKEN
    url = f"https://graph.facebook.com/v21.0/me/messages?access_token={token}"
    headers = {"Content-Type": "application/json"}
    data = {"recipient": {"id": recipient_id}, "message": payload_message}
    resp = requests.post(url, json=data, headers=headers)
    if resp.status_code != 200:
        print(f"Error Messenger API: {resp.text}")

def configurar_menu_messenger():
    """Configura el botón persistente 'Venta Especializada' en todas las páginas conectadas."""
    tokens_a_configurar = set()
    if PAGE_ACCESS_TOKEN:
        tokens_a_configurar.add(PAGE_ACCESS_TOKEN)
    for info in PAGINAS_CONFIG.values():
        if info.get("token"):
            tokens_a_configurar.add(info["token"])

    payload = {
        "get_started": {"payload": "INICIAR_CHAT"},
        "persistent_menu": [
            {
                "locale": "default",
                "composer_input_disabled": False,
                "call_to_actions": [
                    {
                        "type": "postback",
                        "title": "Venta Especializada",
                        "payload": "VENTA_ESPECIALIZADA_BTN"
                    },
                    {
                        "type": "web_url",
                        "title": "WhatsApp Personalizado",
                        "url": WHATSAPP_ATENCION,
                        "webview_height_ratio": "full"
                    }
                ]
            }
        ]
    }
    for token in tokens_a_configurar:
        try:
            url = f"https://graph.facebook.com/v21.0/me/messenger_profile?access_token={token}"
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Error configurando menú Messenger: {e}")

def obtener_o_crear_sesion(sender_id, page_id):
    nombre_pagina, page_token = obtener_info_pagina(page_id)
    session_key = f"{page_id or 'default'}_{sender_id}"
    if session_key not in sesiones_chat:
        sesiones_chat[session_key] = {
            "sender_id": sender_id,
            "page_id": page_id,
            "nombre_pagina": nombre_pagina,
            "page_token": page_token,
            "historial": [],
            "clasificado": False,
            "venta_registrada": False,
            "no_interesado": False,
            "derivado": False,
            "pendiente_recuperacion": False
        }
    else:
        sesiones_chat[session_key]["nombre_pagina"] = nombre_pagina
        sesiones_chat[session_key]["page_token"] = page_token
    return session_key, sesiones_chat[session_key]

def activar_atencion_especializada(sender_id, page_id, motivo="Botón Venta Especializada"):
    """Notifica al cliente que pasa a chat especializado, alerta a Telegram y entrega WhatsApp SIN dejar el chat en standby."""
    session_key, sesion = obtener_o_crear_sesion(sender_id, page_id)
    cancelar_remarketing(session_key)
    cancelar_recuperacion(session_key)
    if session_key in temporizadores_embudo:
        temporizadores_embudo[session_key].cancel()

    sesion["derivado"] = True
    nombre_pagina = sesion["nombre_pagina"]
    page_token = sesion["page_token"]

    mensaje_cliente = (
        f"¡Con mucho gusto! ✨ Tu solicitud pasará ahora a nuestro *chat especializado* de {nombre_pagina} para brindarte atención personalizada. "
        "Un asesor revisará tu caso de inmediato.\n\n"
        f"Si deseas escribirnos directamente por WhatsApp, haz clic aquí: {WHATSAPP_ATENCION} (o cuéntame por aquí qué referencia necesitas mientras tanto)."
    )
    enviar_a_messenger(sender_id, {"text": mensaje_cliente}, page_token)
    sesion["historial"].append(f"Bot: {mensaje_cliente}")

    resumen_msg = (
        f"🏪 *Página:* {nombre_pagina}\n"
        f"📌 *Motivo:* {motivo}\n"
        f"🆔 *ID Messenger:* {sender_id}\n"
        f"💡 *Nota:* El bot sigue activo para no dejar al cliente sin respuesta si vuelve a escribir."
    )
    enviar_alertas(f"CHAT ESPECIALIZADO [{nombre_pagina}]", resumen_msg)
    registrar_en_sheets("Interacciones_y_Mayoristas", [
        hora_colombia(), "VENTA ESPECIALIZADA", sender_id, "Por confirmar",
        "Por confirmar", "Bogotá/Soacha", motivo, nombre_pagina
    ])

def procesar_salida_ia(session_key, sesion, texto_ia):
    """Procesa etiquetas, adjuntos, alertas, Sheets y temporizadores de cualquier respuesta de IA."""
    sender_id = sesion["sender_id"]
    page_token = sesion["page_token"]
    nombre_pagina = sesion["nombre_pagina"]

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
    elif "[ATENCION_ESPECIALIZADA]" in texto_ia:
        evento_detectado = "ATENCION_ESPECIALIZADA"
        texto_ia = texto_ia.replace("[ATENCION_ESPECIALIZADA]", "")
    elif "[NO_INTERESADO]" in texto_ia:
        evento_detectado = "NO_INTERESADO"
        texto_ia = texto_ia.replace("[NO_INTERESADO]", "")

    fotos = re.findall(r"\[FOTO:\s*(https?://[^\]]+)\]", texto_ia)
    videos = re.findall(r"\[VIDEO:\s*(https?://[^\]]+)\]", texto_ia)

    texto_limpio = re.sub(r"\[FOTO:\s*https?://[^\]]+\]", "", texto_ia)
    texto_limpio = re.sub(r"\[VIDEO:\s*https?://[^\]]+\]", "", texto_limpio).strip()

    if texto_limpio:
        enviar_a_messenger(sender_id, {"text": texto_limpio}, page_token)
        actualizar_metrica_diaria(session_key)

    for url_foto in fotos:
        enviar_a_messenger(sender_id, {
            "attachment": {"type": "image", "payload": {"url": url_foto.strip(), "is_reusable": True}}
        }, page_token)

    for url_video in videos:
        enviar_a_messenger(sender_id, {
            "attachment": {"type": "video", "payload": {"url": url_video.strip(), "is_reusable": True}}
        }, page_token)

    if evento_detectado == "NO_INTERESADO":
        sesion["no_interesado"] = True
        cancelar_remarketing(session_key)
        cancelar_recuperacion(session_key)
        reiniciar_temporizador_embudo(session_key)
        return

    if evento_detectado:
        cancelar_remarketing(session_key)
        cancelar_recuperacion(session_key)
        if session_key in temporizadores_embudo:
            temporizadores_embudo[session_key].cancel()

        if evento_detectado in ("MAYORISTA", "ENVIO_NACIONAL", "ATENCION_ESPECIALIZADA"):
            sesion["derivado"] = True

        clasificar_y_guardar_embudo(session_key, evento_forzado=evento_detectado)

        historial_completo = "\n".join(sesion["historial"])
        datos = extraer_resumen_datos(historial_completo, evento_detectado)

        detalle_texto = datos.get("detalle_pedido", "")
        if isinstance(detalle_texto, (dict, list)):
            detalle_texto = "\n".join(f"• {k}: {v}" for k, v in detalle_texto.items()) if isinstance(detalle_texto, dict) else "\n".join(str(x) for x in detalle_texto)

        resumen_msg = (
            f"🏪 *Página:* {nombre_pagina}\n"
            f"👤 *Cliente:* {datos.get('nombre')}\n"
            f"📱 *WhatsApp:* {datos.get('telefono')}\n"
            f"📍 *Ubicación:* {datos.get('ciudad_direccion')}\n"
            f"📦 *Detalle:*\n{detalle_texto}\n"
            f"🆔 *ID Messenger:* {sender_id}"
        )

        if evento_detectado == "VENTA_CONFIRMADA":
            titulo_alerta = f"MODIFICACIÓN DE PEDIDO [{nombre_pagina}]" if sesion.get("venta_registrada") else f"NUEVO PEDIDO CONFIRMADO [{nombre_pagina}]"
            sesion["venta_registrada"] = True
            enviar_alertas(titulo_alerta, resumen_msg)
            registrar_en_sheets("Pedidos_Confirmados", [
                hora_colombia(), sender_id, datos.get("nombre"), datos.get("telefono"),
                datos.get("ciudad_direccion"), detalle_texto, "Por Despachar", nombre_pagina
            ])
        elif evento_detectado == "MAYORISTA":
            enviar_alertas(f"CLIENTE MAYORISTA [{nombre_pagina}]", resumen_msg)
            registrar_en_sheets("Interacciones_y_Mayoristas", [
                hora_colombia(), "MAYORISTA", sender_id, datos.get("nombre"),
                datos.get("telefono"), datos.get("ciudad_direccion"), detalle_texto, nombre_pagina
            ])
        elif evento_detectado == "ENVIO_NACIONAL":
            enviar_alertas(f"ENVÍO NACIONAL [{nombre_pagina}]", resumen_msg)
            registrar_en_sheets("Interacciones_y_Mayoristas", [
                hora_colombia(), "ENVIO NACIONAL", sender_id, datos.get("nombre"),
                datos.get("telefono"), datos.get("ciudad_direccion"), detalle_texto, nombre_pagina
            ])
        elif evento_detectado == "ATENCION_ESPECIALIZADA":
            enviar_alertas(f"ATENCIÓN ESPECIALIZADA [{nombre_pagina}]", resumen_msg)
            registrar_en_sheets("Interacciones_y_Mayoristas", [
                hora_colombia(), "ATENCION ESPECIALIZADA", sender_id, datos.get("nombre"),
                datos.get("telefono"), datos.get("ciudad_direccion"), detalle_texto, nombre_pagina
            ])
    elif not sesion.get("venta_registrada"):
        reiniciar_temporizador_embudo(session_key)
        programar_remarketing(session_key)

def procesar_respuesta_bot(sender_id, mensaje_usuario, page_id=None):
    session_key, sesion = obtener_o_crear_sesion(sender_id, page_id)
    cancelar_recuperacion(session_key)

    sesion["historial"].append(f"Cliente: {mensaje_usuario}")
    nombre_pagina = sesion["nombre_pagina"]
    page_token = sesion["page_token"]
    instrucciones_pagina = INSTRUCCIONES_SISTEMA.replace("{NOMBRE_TIENDA}", nombre_pagina)

    historial_formateado = "\n".join(sesion["historial"])
    prompt_conversacion = (
        "Historial de la conversación hasta ahora:\n"
        f"{historial_formateado}\n\n"
        "Responde únicamente al último mensaje del Cliente manteniendo toda la memoria previa de la conversación:"
    )

    try:
        texto_ia = llamar_gemini_con_respaldo(
            contents=prompt_conversacion,
            system_instruction=instrucciones_pagina,
            temperature=0.1
        )
        sesion["historial"].append(f"Bot: {texto_ia}")
    except Exception as e:
        print(f"Error Gemini (Contingencia activa en {nombre_pagina}): {e}")
        mensaje_contingencia = (
            f"¡Hola! 👋 En *{nombre_pagina}* tenemos disponibilidad inmediata con *pago 100% contraentrega* en Bogotá ($10.000) y Soacha ($15.000):\n\n"
            "• Pampas Eternizadas: $12.900 c/u\n"
            "• Jarrones en Mimbre: 40cm ($40.000) | 50cm ($48.000) | 60cm ($55.000) | 80cm ($75.000)\n"
            "• Lámpara Boho Mimbre 40cm: $45.000\n"
            "• Cojines Decorativos: $27.000 c/u (3 o más a $25.000 c/u)\n\n"
            f"Para atención inmediata escríbenos por WhatsApp: {WHATSAPP_ATENCION} (o en unos minutos retomamos tu consulta aquí mismo)."
        )
        enviar_a_messenger(sender_id, {"text": mensaje_contingencia}, page_token)
        sesion["historial"].append(f"Bot (Contingencia): {mensaje_contingencia}")
        enviar_alertas(
            f"AVISO CONTINGENCIA IA [{nombre_pagina}]",
            f"🆔 *ID Messenger:* {sender_id}\n💬 *Mensaje cliente:* {mensaje_usuario}\n🔄 Se programó recuperación automática en 3 min."
        )
        programar_recuperacion_chat(session_key)
        reiniciar_temporizador_embudo(session_key)
        return

    procesar_salida_ia(session_key, sesion, texto_ia)

# --- RUTAS DEL SERVIDOR WEBHOOK ---

@app.route("/", methods=["GET"])
def home():
    paginas_activas = ", ".join(p["nombre"] for p in PAGINAS_CONFIG.values()) or "Modo página única"
    return f"Servidor Bot Activo ✅ | Páginas: {paginas_activas}", 200

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
            page_id = entry.get("id")
            for event in entry.get("messaging", []):
                if event.get("message") and event["message"].get("is_echo"):
                    continue

                sender_id = event.get("sender", {}).get("id")
                if not sender_id or sender_id == page_id:
                    continue

                if event.get("referral"):
                    ref_data = event["referral"]
                    if ref_data.get("type") == "OPEN_THREAD":
                        continue
                    ref_producto = ref_data.get("ref", "").strip()
                    if ref_producto:
                        producto_legible = ref_producto.replace("_", " ")
                        mensaje_armado = f"Hola, vengo del enlace directo por: {producto_legible} (Referencia de publicación: {ref_producto}). Muéstrame disponibilidad, precios y fotos/video."
                        threading.Thread(
                            target=procesar_respuesta_bot,
                            args=(sender_id, mensaje_armado, page_id),
                            daemon=True
                        ).start()
                    continue

                if event.get("postback"):
                    payload_btn = event["postback"].get("payload", "")
                    titulo_boton = event["postback"].get("title", "")
                    if payload_btn == "VENTA_ESPECIALIZADA_BTN" or "especializada" in titulo_boton.lower():
                        threading.Thread(
                            target=activar_atencion_especializada,
                            args=(sender_id, page_id, "Botón Venta Especializada"),
                            daemon=True
                        ).start()
                        continue

                    ref_producto = event["postback"].get("referral", {}).get("ref", "").strip()
                    if ref_producto:
                        producto_legible = ref_producto.replace("_", " ")
                        mensaje_armado = f"Hola, vengo del enlace directo por: {producto_legible} (Referencia de publicación: {ref_producto}). Muéstrame disponibilidad, precios y fotos/video."
                    elif payload_btn == "INICIAR_CHAT":
                        mensaje_armado = "Hola, me interesa conocer los productos disponibles."
                    else:
                        mensaje_armado = titulo_boton

                    if mensaje_armado:
                        threading.Thread(
                            target=procesar_respuesta_bot,
                            args=(sender_id, mensaje_armado, page_id),
                            daemon=True
                        ).start()
                    continue

                if event.get("message") and not event["message"].get("is_echo"):
                    texto = event["message"].get("text", "")
                    if texto.strip().lower() in ("venta especializada", "atencion especializada", "atención especializada"):
                        threading.Thread(
                            target=activar_atencion_especializada,
                            args=(sender_id, page_id, f"Solicitud directa: {texto.strip()}"),
                            daemon=True
                        ).start()
                        continue

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
                            args=(sender_id, mensaje_final, page_id),
                            daemon=True
                        ).start()
                    elif adjuntos:
                        _, page_token = obtener_info_pagina(page_id)
                        threading.Thread(
                            target=enviar_a_messenger,
                            args=(sender_id, {"text": "Recibí tu archivo. ¿Me cuentas por texto qué referencia te gustaría cotizar o pedir?"}, page_token),
                            daemon=True
                        ).start()

        return "EVENT_RECEIVED", 200
    return "Not Found", 404

configurar_menu_messenger()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
