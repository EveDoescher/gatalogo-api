"""Public, revocable landing pages for deliberately shared search posters."""
from html import escape
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_database_session
from app.persistence_models import MissingReport, RemoteCat
from app.services.missing_posters import public_poster_details

router = APIRouter(prefix="/public/busca", tags=["public-search"])
HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"}


def render_page(report, cat):
    active = report is not None and report.status == "active"
    name = escape(str((cat.payload or {}).get("name") or "Gato desaparecido")) if cat else "Busca por um gato"
    fields = ""
    if active:
        details = public_poster_details(report, cat)
        pairs = [("Último local visto", details.get("last_seen_address")),
                 ("Coleira", details.get("collar")), ("Marcas características", details.get("distinguishing_marks")),
                 ("Microchip", details.get("microchip"))]
        fields = "".join(f"<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>" for label, value in pairs if value)
        contacts = "".join(f'<p class="phone"><span>{"Telefone / WhatsApp" if c["whatsapp"] else "Telefone"}</span><strong>{escape(c["phone"])}</strong></p>' for c in details["contacts"])
        contact_block = f'<section class="contact"><h2>Viu este gatinho?</h2>{contacts or "<p>Registre a foto e o local pelo Gatálogo para ajudar o tutor.</p>"}</section>'
        date = f'<p class="status">Alerta aberto em {report.created_at:%d/%m/%Y}</p>' if details.get("created_at") else ""
        content = f'<header>PROCURA-SE</header><img src="/public/busca/{report.id}/foto" alt="Foto de {name}"><h1>{name}</h1>{date}<dl>{fields}</dl>{contact_block}<p>Uma foto e uma localização podem ajudar na busca. No Gatálogo, use Gatalogar para registrar um avistamento.</p>'
    else:
        content = '<header class="closed">Busca encerrada</header><h1>Obrigado por ajudar</h1><p>Este cartaz não recebe novas pistas. Os contatos e a foto foram retirados desta página.</p>'
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Busca por um gato · Gatálogo</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#fbf6e8;color:#4b281b;font:17px/1.5 system-ui,sans-serif;padding:24px 16px}}main{{max-width:580px;margin:auto;background:#fff9ec;border:2px solid #d4543c;border-radius:18px;padding:20px;box-shadow:0 6px 20px #4b281b15}}header{{background:#d4543c;color:white;text-align:center;font-size:30px;font-weight:900;padding:12px;border-radius:10px}}header.closed{{background:#52783c}}h1{{text-align:center;font-size:30px;margin:16px 0 8px;overflow-wrap:anywhere}}h2{{font-size:21px;margin:0 0 12px}}img{{display:block;width:100%;max-height:420px;object-fit:contain;border-radius:12px;margin-top:16px}}.status{{text-align:center;color:#9d3425;font-weight:700}}dt{{font-size:14px;color:#80654e;margin-top:14px}}dd{{margin:0;font-weight:700;overflow-wrap:anywhere}}.contact{{background:#fcebe8;border-radius:12px;padding:16px;margin:20px 0}}.phone span,.phone strong{{display:block}}.phone strong{{font-size:22px;overflow-wrap:anywhere}}footer{{text-align:center;color:#426538;margin:20px auto;font-weight:700}}</style><main>{content}</main><footer>Gatálogo · Uma comunidade que ajuda</footer></html>"""


async def _public_report(report_id, session):
    report = await session.get(MissingReport, report_id)
    cat = await session.get(RemoteCat, report.cat_id) if report else None
    if cat is None or cat.deleted_at is not None:
        raise HTTPException(404, "Busca indisponível.")
    if report.status == "active" and not public_poster_details(report, cat)["poster_options"]["qr_code"]:
        raise HTTPException(404, "Cartaz indisponível.")
    return report, cat


@router.get("/{report_id}", response_class=HTMLResponse)
async def public_search(report_id: UUID, session: AsyncSession = Depends(get_database_session)):
    report, cat = await _public_report(report_id, session)
    return HTMLResponse(render_page(report, cat), headers=HEADERS)


@router.get("/{report_id}/foto")
async def public_photo(report_id: UUID, session: AsyncSession = Depends(get_database_session)):
    report, _ = await _public_report(report_id, session)
    if report.status != "active":
        raise HTTPException(404, "Busca encerrada.")
    from app.routers.product import report_photo
    response = await report_photo(report_id, user=None, session=session)
    response.headers.update(HEADERS)
    return response
