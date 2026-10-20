"""QR-код ссылки входа в Telegram в виде SVG.

Телефон сканирует его с экрана компьютера в Telegram: Настройки → Устройства.
Кодировщик qrcodegen лежит рядом без изменений (MIT, Project Nayuki), так что
новой зависимости в образе нет. Цвета задаются классами из index.html: код
остаётся тёмным на светлой бумаге в обеих темах, иначе сканер его не берёт.
"""
import qrcodegen

BORDER = 2  # тихая зона вокруг кода: без неё сканер часто не находит символ


def svg(text):
    code = qrcodegen.QrCode.encode_text(text, qrcodegen.QrCode.Ecc.MEDIUM)
    n = code.get_size()
    cells = "".join(f"M{x},{y}h1v1h-1z" for y in range(n) for x in range(n) if code.get_module(x, y))
    side = n + 2 * BORDER
    return (f'<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
            f'shape-rendering="crispEdges" role="img" aria-label="QR-код входа в Telegram">'
            f'<rect class="qr-paper" width="{side}" height="{side}"/>'
            f'<path class="qr-ink" transform="translate({BORDER},{BORDER})" d="{cells}"/></svg>')
