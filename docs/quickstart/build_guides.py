"""Build the two illustrated guides with ReportLab and DejaVu fonts."""
import json
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parent
W, H = A4
M = 44
WIDTH = W - 2 * M
NAVY = '#102136'
BLUE = '#165BAC'
MUTED = '#4C6077'
PALE = '#EDF4FC'
FONT_DIR = Path('/usr/share/fonts/truetype/dejavu')
for alias, file in [('Guide', 'DejaVuSans'), ('GuideBold', 'DejaVuSans-Bold'), ('GuideMono', 'DejaVuSansMono')]:
    pdfmetrics.registerFont(TTFont(alias, FONT_DIR / (file + '.ttf')))
pdfmetrics.registerFontFamily('Guide', normal='Guide', bold='GuideBold', italic='Guide', boldItalic='GuideBold')


def draw_paragraph(pdf, text, x, y, width=WIDTH, size=10.6, leading=15.8, color=NAVY, font='Guide'):
    p = Paragraph(text, ParagraphStyle('guide', fontName=font, fontSize=size, leading=leading, textColor=colors.HexColor(color)))
    _, height = p.wrap(width, H)
    p.drawOn(pdf, x, y - height)
    return y - height


def draw_image(pdf, image, y, max_height, width=WIDTH, x=M):
    src = ROOT / 'images' / image
    with Image.open(src) as im:
        iw, ih = im.size
    scale = min(width / iw, max_height / ih)
    a, b = iw * scale, ih * scale
    pdf.drawImage(str(src), x + (width - a) / 2, y - b, width=a, height=b)
    return y - b


def chrome(pdf, number, lang):
    pdf.setFillColor(colors.HexColor(NAVY))
    pdf.rect(0, H - 31, W, 31, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont('GuideBold', 9)
    pdf.drawString(M, H - 20, 'IRRADIUM')
    pdf.setFont('Guide', 8)
    pdf.drawRightString(W - M, H - 20, '0.3.0-alpha.1  /  CATEGORY IV' if lang == 'en' else '0.3.0-alpha.1  /  CATEGORÍA IV')
    pdf.setStrokeColor(colors.HexColor('#D8E3EF'))
    pdf.line(M, 42, W - M, 42)
    pdf.setFillColor(colors.HexColor(MUTED))
    pdf.drawString(M, 26, 'ILLUSTRATED GUIDE  /  TEACHING STUDY' if lang == 'en' else 'GUÍA ILUSTRADA  /  ESTUDIO DIDÁCTICO')
    pdf.drawRightString(W - M, 26, f'{number:02d}')


def build(lang):
    es = lang == 'es'
    pages = json.loads((ROOT / 'guide-content.json').read_text())[lang]
    metrics = json.loads((ROOT / 'results/verification.json').read_text())
    full = metrics['accumulated_statistics']
    air = metrics['air_comparison']
    substitutions = {'MAX': f"{100*full['maximum']:.2f}", 'P90': f"{100*full['p90']:.2f}", 'P95': f"{100*full['p95']:.2f}", 'P98': f"{100*full['p98']:.2f}", 'AIR': f"{air['sum_difference_percent']:.3f}"}
    file = 'IRRADIUM-guia-rapida-es.pdf' if es else 'IRRADIUM-quick-start-en.pdf'
    pdf = canvas.Canvas(str(ROOT / file), pagesize=A4, pageCompression=1, invariant=1, lang=lang)
    pdf.setTitle('IRRADIUM | ' + ('Guía ilustrada' if es else 'Illustrated guide'))
    pdf.setAuthor('IRRADIUM')
    pdf.bookmarkPage('cover')
    pdf.addOutlineEntry('IRRADIUM', 'cover')
    pdf.setFillColor(colors.HexColor(NAVY))
    pdf.rect(0, 0, W, H, fill=1, stroke=0)
    draw_paragraph(pdf, '0.3.0-alpha.1  /  ' + ('CATEGORÍA IV' if es else 'CATEGORY IV'), M, H - 66, size=11, color='#8ECAFF', font='GuideBold')
    draw_paragraph(pdf, 'IRRADIUM', M, H - 104, size=53, leading=60, color='#FFFFFF', font='GuideBold')
    draw_paragraph(pdf, 'Guía ilustrada de instalación y uso' if es else 'Illustrated installation and user guide', M, H - 173, size=19.5, leading=25, color='#FFFFFF')
    draw_paragraph(pdf, 'An Integrated Framework for Irradiator Modeling, Simulation and Optimization', M, H - 214, size=11, color='#B5CFEA')
    y = draw_image(pdf, 'showcase/cad-en.png', H - 259, 360)
    cards = [('54', 'cuerpos CAD' if es else 'CAD bodies'), ('5', 'materiales' if es else 'materials'), ('68', 'posiciones' if es else 'positions')]
    for i, (number, name) in enumerate(cards):
        x = M + i * (WIDTH + 9) / 3
        pdf.setFillColor(colors.HexColor('#1C3857'))
        pdf.roundRect(x, y - 81, (WIDTH - 18) / 3, 61, 8, fill=1, stroke=0)
        draw_paragraph(pdf, number, x + 12, y - 28, width=138, size=24, leading=28, color='#FFFFFF', font='GuideBold')
        draw_paragraph(pdf, name, x + 12, y - 57, width=138, size=9, leading=13, color='#B5CFEA')
    draw_paragraph(pdf, 'Caso didáctico inspirado en el irradiador SVST-Co-60/B.' if es else 'A teaching case inspired by the SVST-Co-60/B irradiator.', M, y - 106, size=10, color='#B5CFEA')
    pdf.showPage()
    chrome(pdf, 2, lang)
    pdf.bookmarkPage('contents')
    pdf.addOutlineEntry('Contenido' if es else 'Contents', 'contents')
    y = draw_paragraph(pdf, 'Encuentra tu siguiente paso' if es else 'Find your next step', M, H - 62, size=25, leading=34, font='GuideBold') - 24
    for idx, page in enumerate(pages, 1):
        label = page['title']
        pdf.setFillColor(colors.HexColor(PALE))
        pdf.roundRect(M, y - 30, WIDTH, 29, 5, fill=1, stroke=0)
        draw_paragraph(pdf, label, M + 13, y - 8, width=WIDTH - 50, size=10.5, leading=14)
        pdf.setFont('GuideBold', 9)
        pdf.setFillColor(colors.HexColor(BLUE))
        pdf.drawRightString(W - M - 13, y - 19, f'{idx+2:02d}')
        pdf.linkRect('', f'section{idx}', (M, y-30, W-M, y), relative=0, thickness=0)
        y -= 35
    assert y > 50
    pdf.showPage()

    for idx, page in enumerate(pages, 1):
        chrome(pdf, idx + 2, lang)
        pdf.bookmarkPage(f'section{idx}')
        pdf.addOutlineEntry(page['title'], f'section{idx}')
        y = draw_paragraph(pdf, page['path'], M, H - 57, size=8.5, leading=12, color=BLUE, font='GuideBold') - 9
        y = draw_paragraph(pdf, page['title'], M, y, size=25, leading=30, font='GuideBold') - 15
        for block in page['body']:
            for key, value in substitutions.items():
                block = block.replace('{' + key + '}', value.replace('.', ',') if es else value)
            if block.startswith('!'):
                p = Paragraph(block[1:], ParagraphStyle('tip', fontName='Guide', fontSize=10, leading=15, textColor=colors.HexColor(NAVY)))
                _, height = p.wrap(WIDTH - 26, H)
                pdf.setFillColor(colors.HexColor(PALE))
                pdf.roundRect(M, y - height - 18, WIDTH, height + 18, 7, fill=1, stroke=0)
                p.drawOn(pdf, M+13, y-height-9)
                y -= height + 28
            elif block.startswith('$'):
                y = draw_paragraph(pdf, block[1:], M + 10, y, width=WIDTH - 20, size=9, leading=15, font='GuideMono') - 10
            else:
                y = draw_paragraph(pdf, block, M, y) - 12
        pics = page.get('pics', [])
        if pics:
            if len(pics) == 1:
                y = draw_image(pdf, pics[0], y - 2, min(y - 83, 360)) - 9
            else:
                span = (WIDTH - 12) / 2
                height = min(250, y - 83)
                bottom = [draw_image(pdf, name, y-2, height, span, M + col * (span+12)) for col, name in enumerate(pics)]
                y = min(bottom) - 9
            y = draw_paragraph(pdf, page['caption'], M, y, size=8.2, leading=12.5, color=MUTED) - 6
        assert y >= 48, f'{lang} section {idx} overflows by {48-y:.1f} points'
        pdf.showPage()
    pdf.save()


if __name__ == '__main__':
    for language in ('es', 'en'):
        build(language)
