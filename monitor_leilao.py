import hashlib
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

URL = "https://www.leiloeirosdebrasilia.com.br/item/4134/detalhes?page=15"
STATE_FILE = "state.json"


def fetch_page() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0 Safari/537.36"
        )
    }
    response = requests.get(URL, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def extract_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text("\n", strip=True)

    title = ""
    h1 = soup.find(["h1"])
    if h1:
        title = normalize_text(h1.get_text(" ", strip=True))

    status = ""
    if "Aberto para Lances" in page_text:
        status = "Aberto para Lances"

    # Extrai um trecho entre "Últimos Lances" e a próxima seção conhecida
    ultimos_lances = ""
    match = re.search(
        r"Últimos Lances(.*?)(Documentos|Detalhes do Lote|Observações do Lote|Localização do Imóvel|CONTATOS)",
        page_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        ultimos_lances = normalize_text(match.group(1))
    else:
        ultimos_lances = "Seção não localizada"

    # Heurística para identificar possível lance
    # Você pode ajustar depois se o site mudar.
    found_bid_indicators = []
    bid_patterns = [
        r"R\$\s?[\d\.\,]+",
        r"lance automático",
        r"lance superado",
        r"usuário",
        r"apelido",
        r"ofertado",
    ]
    lower_text = ultimos_lances.lower()
    for pattern in bid_patterns:
        if re.search(pattern, lower_text, flags=re.IGNORECASE):
            found_bid_indicators.append(pattern)

    snapshot_text = f"{title}\nSTATUS:{status}\nULTIMOS_LANCES:{ultimos_lances}"
    digest = hashlib.sha256(snapshot_text.encode("utf-8")).hexdigest()

    return {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": URL,
        "title": title,
        "status": status,
        "ultimos_lances": ultimos_lances,
        "found_bid_indicators": found_bid_indicators,
        "digest": digest,
    }


def load_previous_state() -> dict | None:
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_send_email(previous: dict | None, current: dict) -> tuple[bool, str]:
    if previous is None:
        return True, "Primeira execução do monitor."

    reasons = []

    if previous.get("digest") != current.get("digest"):
        reasons.append("Mudança detectada no conteúdo monitorado.")

    prev_bids = normalize_text(previous.get("ultimos_lances", ""))
    curr_bids = normalize_text(current.get("ultimos_lances", ""))

    if prev_bids != curr_bids:
        reasons.append("Seção 'Últimos Lances' foi alterada.")

    if current.get("found_bid_indicators") and not previous.get("found_bid_indicators"):
        reasons.append("Possível lance identificado.")

    return (len(reasons) > 0, " ".join(reasons))


def build_email(previous: dict | None, current: dict, reason: str) -> MIMEMultipart:
    subject = "Alerta de leilão: mudança detectada no lote 441"

    prev_text = previous.get("ultimos_lances", "(sem estado anterior)") if previous else "(primeira execução)"
    curr_text = current.get("ultimos_lances", "")

    body = f"""
Mudança detectada no site monitorado.

Motivo:
{reason}

URL:
{current['url']}

Título:
{current.get('title', '')}

Status:
{current.get('status', '')}

Últimos Lances (anterior):
{prev_text}

Últimos Lances (atual):
{curr_text}

Indicadores de lance encontrados:
{", ".join(current.get("found_bid_indicators", [])) or "nenhum"}

Verificado em:
{current.get("checked_at_utc")}
""".strip()

    msg = MIMEMultipart()
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    return msg


def send_email(message: MIMEMultipart) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(message)


def main() -> None:
    html = fetch_page()
    current = extract_snapshot(html)
    previous = load_previous_state()

    send, reason = should_send_email(previous, current)

    print("Resumo atual:")
    print(json.dumps(current, ensure_ascii=False, indent=2))

    if send:
        print(f"Enviando email: {reason}")
        message = build_email(previous, current, reason)
        send_email(message)
    else:
        print("Nenhuma mudança relevante detectada.")

    save_state(current)


if __name__ == "__main__":
    main()
