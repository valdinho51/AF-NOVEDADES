"""
Servidor Flask para generar Novedades PDF
Deploy en Railway: railway up
"""

import asyncio, base64, io, os, re, json, requests
from flask import Flask, request, jsonify
from PIL import Image
from pyairtable import Api
from playwright.async_api import async_playwright

app = Flask(__name__)

# ── CONFIG ────────────────────────────────────────────────────────
BASE_ID         = 'appLgrz2T5Tm2087a'
TABLE_PRODUCTOS = 'tblFl79JkbVqflLyz'
TABLE_CATALOGOS = 'tblSbtgCTibjDwEux'

FIELD_SKU       = 'fldc7oxT7Pt0MMQxG'
FIELD_NOMBRE    = 'fldeMwAMWeCORbqBW'
FIELD_CATEGORIA = 'fld2whlleDbMQKUb7'
FIELD_SUBCAT    = 'fld63Wn4xTSAQ38B6'
FIELD_PRECIO    = 'fldwqRHCO8g8f9uoR'
FIELD_IMAGEN    = 'fldQDRq9v55HqcIxJ'

FIELD_ESTADO    = 'fld5Qy6pNTv1Vcw85'
FIELD_PDF_OUT   = 'fldN1cUzDNArj0aQ5'
FIELD_FECHA     = 'fldrqHqbfd48QcVpq'

# Logo AF en base64 (reemplazar con el tuyo real)
LOGO_B64 = os.getenv('LOGO_B64', '')

# ── UTILIDADES ────────────────────────────────────────────────────

def get_field(record, field_id):
    """Extrae valor de un campo de Airtable."""
    v = record['fields'].get(field_id)
    if v is None:
        return None
    if isinstance(v, dict) and 'name' in v:
        return v['name']
    if isinstance(v, list) and v and isinstance(v[0], dict) and 'name' in v[0]:
        return v[0]['name']
    return v

def get_img_url(record):
    """Obtiene la URL de la imagen del producto."""
    imgs = record['fields'].get(FIELD_IMAGEN, [])
    if imgs and isinstance(imgs, list):
        return imgs[0].get('url', '')
    return ''

def extraer_titulo_base(nombre):
    """Elimina variantes del nombre para comparar."""
    base = nombre.upper().strip()
    patrones = [
        r'\b\d+\s*(mt|mts|metro|metros|m)\b',
        r'\b(negro|rojo|azul|blanco|gris|verde|amarillo|plata|dorado)\b',
        r'\b\d+\s*(awg|amp|watts?|w)\b',
    ]
    for p in patrones:
        base = re.sub(p, '', base, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', base).strip()

def similitud(a, b):
    """Jaccard sobre palabras."""
    wa = set(extraer_titulo_base(a).split())
    wb = set(extraer_titulo_base(b).split())
    if not wa or not wb:
        return 0
    return len(wa & wb) / len(wa | wb)

def agrupar(productos):
    """Agrupa productos con misma descripción base."""
    n = len(productos)
    usado = [False] * n
    grupos = []
    for i in range(n):
        if usado[i]:
            continue
        grupo = [productos[i]]
        usado[i] = True
        for j in range(i+1, n):
            if usado[j]:
                continue
            a, b = productos[i], productos[j]
            if a['categoria'] != b['categoria']:
                continue
            if similitud(a['nombre'], b['nombre']) < 0.65:
                continue
            precios = [p['precio'] for p in grupo + [b] if p.get('precio')]
            if precios and max(precios) / min(precios) > 1.8:
                continue
            grupo.append(b)
            usado[j] = True
        grupos.append(grupo)
    return grupos

def sku_case(grupo):
    """Determina el caso de SKU según número de variantes y tipo."""
    if len(grupo) == 1:
        return 'single'
    precios = [p['precio'] for p in grupo]
    if len(set(precios)) == 1:
        return 'color'
    return 'variante'

# ── HTML DEL FLYER ────────────────────────────────────────────────

def render_left(grupo):
    """Genera el bloque izquierdo del flyer según el caso."""
    case = sku_case(grupo)
    p0 = grupo[0]
    cat = p0['categoria'] or ''
    nombre = p0['nombre'].upper()
    title_sm = 'sm' if len(nombre) > 20 else ''

    html = f'<div class="eyebrow">{cat}</div>'
    html += f'<div class="title {title_sm}">{nombre}</div>'

    if case == 'single':
        html += f'<div class="sku-row"><span class="sku-label">SKU</span><span class="sku-val">{p0["sku"]}</span></div>'
    elif case == 'color':
        html += '<div class="sec-label">Color · SKU</div><div class="pill-list">'
        for p in grupo:
            html += f'<div class="pill"><div class="pill-name">{p["variante"] or p["nombre"]}</div><div class="pill-sku">{p["sku"]}</div></div>'
        html += '</div>'
    else:  # variante con precio distinto
        html += '<div class="sec-label">Versión · SKU</div><div class="pill-list">'
        for p in grupo:
            html += f'<div class="pill"><div class="pill-name">{p["variante"] or p["nombre"]}</div><div class="pill-sku">{p["sku"]}</div></div>'
        html += '</div>'

    return html

def render_right(grupo):
    """Genera la columna de precios."""
    case = sku_case(grupo)
    html = ''
    if case in ('single', 'color'):
        p = grupo[0]
        precio = f"{p['precio']:,.0f}" if p['precio'] else '—'
        html = f'''<div class="pblock">
            <div class="ptype">Mayoreo</div>
            <div class="pamount"><div class="pcur">$</div><div class="pval">{precio}</div><div class="punit">MXN</div></div>
        </div>'''
    else:
        sm = 'sm' if len(grupo) >= 3 else ''
        for p in grupo:
            precio = f"{p['precio']:,.0f}" if p['precio'] else '—'
            variante = p['variante'] or p['nombre']
            html += f'''<div class="pblock sm">
                <div class="ptype">{variante}</div>
                <div class="pamount"><div class="pcur">$</div><div class="pval {sm}">{precio}</div><div class="punit">MXN</div></div>
            </div>'''
    return html

def make_page_html(logo_b64, grupo):
    """Genera el HTML completo de un flyer."""
    img_url = grupo[0]['img_url']
    left = render_left(grupo)
    right = render_right(grupo)

    return f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 390px; background: #fff; font-family: Arial, Helvetica, sans-serif; }}
.page {{ width: 390px; display: flex; flex-direction: column; background: #fff; }}
.bar-top {{ height: 6px; background: #3dd132; flex-shrink: 0; }}
.hdr {{ height: 64px; padding: 0 22px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #efefef; }}
.hdr img {{ height: 46px; }}
.hdr-tag {{ font-size: 14px; font-weight: bold; letter-spacing: 3px; color: #3dd132; text-transform: uppercase; }}
.img-sec {{ height: 300px; background: #fff; display: flex; align-items: center; justify-content: center; padding: 14px 30px; border-bottom: 1px solid #efefef; overflow: hidden; }}
.img-sec img {{ max-height: 272px; max-width: 310px; object-fit: contain; }}
.info {{ display: flex; min-height: 220px; }}
.info-left {{ flex: 1; padding: 18px 12px 16px 22px; display: flex; flex-direction: column; border-right: 1px solid #efefef; }}
.info-right {{ width: 145px; flex-shrink: 0; display: flex; flex-direction: column; justify-content: center; padding: 14px 14px; gap: 8px; }}
.eyebrow {{ font-size: 10px; font-weight: bold; letter-spacing: 2.5px; color: #3dd132; text-transform: uppercase; margin-bottom: 5px; }}
.title {{ font-size: 26px; font-weight: 900; color: #111; line-height: 1.05; margin-bottom: 8px; }}
.title.sm {{ font-size: 21px; }}
.sku-row {{ display: flex; align-items: center; gap: 6px; margin-bottom: 10px; }}
.sku-label {{ font-size: 10px; font-weight: bold; letter-spacing: 2px; color: #bbb; text-transform: uppercase; }}
.sku-val {{ font-size: 13px; font-weight: bold; letter-spacing: 1px; color: #333; text-transform: uppercase; }}
.sec-label {{ font-size: 10px; font-weight: bold; letter-spacing: 2px; color: #bbb; text-transform: uppercase; margin-bottom: 7px; }}
.pill-list {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 6px; }}
.pill {{ display: flex; align-items: center; gap: 7px; padding: 6px 9px 6px 7px; border: 1px solid #e5e5e5; width: fit-content; }}
.pill-name {{ font-size: 12px; font-weight: bold; color: #333; }}
.pill-sku {{ font-size: 12px; font-weight: bold; letter-spacing: 1px; color: #333; text-transform: uppercase; }}
.pblock {{ padding: 10px 12px; border: 1.5px solid #3dd132; border-radius: 4px; }}
.pblock.sm {{ padding: 7px 10px; }}
.ptype {{ font-size: 10px; font-weight: bold; letter-spacing: 2px; color: #888; text-transform: uppercase; margin-bottom: 3px; }}
.pamount {{ display: flex; align-items: baseline; gap: 1px; }}
.pcur {{ font-size: 12px; font-weight: 400; color: #777; }}
.pval {{ font-size: 36px; font-weight: 900; color: #111; line-height: 1; letter-spacing: -1px; }}
.pval.sm {{ font-size: 27px; }}
.punit {{ font-size: 9px; font-weight: bold; color: #aaa; align-self: flex-end; padding-bottom: 3px; margin-left: 2px; }}
</style></head><body>
<div class="page">
  <div class="bar-top"></div>
  <div class="hdr">
    <img src="data:image/png;base64,{logo_b64}" alt="AF Autopartes">
    <div class="hdr-tag">Novedades</div>
  </div>
  <div class="img-sec"><img src="{img_url}" alt="producto"></div>
  <div class="info">
    <div class="info-left">{left}</div>
    <div class="info-right">{right}</div>
  </div>
</div>
</body></html>'''

# ── RENDER Y PDF ──────────────────────────────────────────────────

async def render_grupos(logo_b64, grupos):
    """Renderiza cada grupo como PNG y retorna lista de imágenes PIL."""
    imgs = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        for grupo in grupos:
            html = make_page_html(logo_b64, grupo)
            page = await browser.new_page(
                viewport={"width": 390, "height": 900},
                device_scale_factor=5  # máxima nitidez
            )
            await page.set_content(html, wait_until='networkidle')
            await page.wait_for_timeout(2500)
            h = await page.evaluate('document.querySelector(".page").offsetHeight')
            await page.set_viewport_size({"width": 390, "height": h})
            el = await page.query_selector('.page')
            png_bytes = await el.screenshot()
            img = Image.open(io.BytesIO(png_bytes)).convert('RGB')
            imgs.append(img)
        await browser.close()
    return imgs

def imgs_to_pdf(imgs):
    """Convierte lista de imágenes PIL a bytes de PDF."""
    buf = io.BytesIO()
    imgs[0].save(buf, format='PDF', save_all=True, append_images=imgs[1:], resolution=300)
    return buf.getvalue()

def subir_pdf_airtable(token, base_id, record_id, field_id, pdf_bytes):
    """Sube el PDF al campo multipleAttachments de Airtable."""
    from datetime import datetime
    filename = f'novedades_{datetime.now().strftime("%Y-%m-%d_%H-%M")}.pdf'
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    url = f'https://content.airtable.com/v0/{base_id}/{record_id}/{field_id}/uploadAttachment'
    resp = requests.post(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }, json={
        'contentType': 'application/pdf',
        'filename': filename,
        'file': pdf_b64,
    })
    resp.raise_for_status()
    return filename

def actualizar_estado(token, base_id, table_id, record_id, estado):
    url = f'https://api.airtable.com/v0/{base_id}/{table_id}/{record_id}'
    requests.patch(url, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }, json={'fields': {'Estado': {'name': estado}}})

# ── ENDPOINT ──────────────────────────────────────────────────────

@app.route('/generar', methods=['POST'])
def generar():
    data    = request.json
    skus    = data.get('skus', [])
    base_id = data.get('base_id', BASE_ID)
    rec_id  = data.get('record_id')
    token   = data.get('token')

    if not skus or not token:
        return jsonify({'error': 'Faltan parámetros'}), 400

    # Correr en background para no bloquear el webhook
    import threading
    threading.Thread(target=procesar, args=(skus, base_id, rec_id, token)).start()
    return jsonify({'ok': True, 'message': f'{len(skus)} SKUs en proceso'}), 200

def procesar(skus, base_id, rec_id, token):
    try:
        print(f'[procesar] SKUs: {skus}')

        # 1. Fetch productos de Airtable
        api = Api(token)
        table = api.table(base_id, TABLE_PRODUCTOS)
        formula = 'OR(' + ','.join([f'FIND("{s}", {{SKU}})' for s in skus]) + ')'
        records = table.all(formula=formula)

        productos = []
        for r in records:
            f = r['fields']
            cat = f.get(FIELD_CATEGORIA, {})
            sub = f.get(FIELD_SUBCAT, {})
            imgs = f.get(FIELD_IMAGEN, [])
            productos.append({
                'sku':       (f.get(FIELD_SKU) or '').strip(),
                'nombre':    (f.get(FIELD_NOMBRE) or '').strip(),
                'categoria': cat.get('name', '') if isinstance(cat, dict) else '',
                'subcat':    sub.get('name', '') if isinstance(sub, dict) else '',
                'precio':    f.get(FIELD_PRECIO, 0),
                'img_url':   imgs[0]['url'] if imgs else '',
                'variante':  None,
            })

        print(f'[procesar] {len(productos)} productos encontrados')

        # 2. Agrupar
        grupos = agrupar(productos)
        print(f'[procesar] {len(grupos)} grupos')

        # 3. Logo
        logo_b64 = LOGO_B64 or os.getenv('LOGO_B64', '')

        # 4. Render
        imgs = asyncio.run(render_grupos(logo_b64, grupos))

        # 5. PDF
        pdf_bytes = imgs_to_pdf(imgs)

        # 6. Subir a Airtable
        filename = subir_pdf_airtable(token, base_id, rec_id, FIELD_PDF_OUT, pdf_bytes)
        print(f'[procesar] PDF subido: {filename}')

        # 7. Actualizar estado
        actualizar_estado(token, base_id, TABLE_CATALOGOS, rec_id, 'Listo')

    except Exception as e:
        print(f'[procesar] ERROR: {e}')
        actualizar_estado(token, base_id, TABLE_CATALOGOS, rec_id, 'Error')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
