import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

URL = "https://www.leiloeirosdebrasilia.com.br/item/4134/detalhes?page=15"
STATE_FILE = "state.json"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_page() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.leiloeirosdebrasilia.com.br/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    response = requests.get(URL, headers=headers, timeout=40, allow_redirects=True)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def extract_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text("\n", strip=True)

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = normalize_text(h1.get_text(" ", strip=True))

    status = "Aberto para Lances" if "Aberto para Lances" in page_text else ""

    match = re.search(
        r"Últimos Lances(.*?)(Documentos|Detalhes do Lote|Observações do Lote|Localização do Imóvel|CONTATOS)",
        page_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    ultimos_lances = normalize_text(match.group(1)) if match else "Seção não localizada"

    found_bid_indicators = []
    bid_patterns = [
        r"R\$\s?[\d\.\,]+",
        r"lance",
        r"oferta",
        r"ofertado",
        r"usuário",
        r"apelido",
        r"superado",
    ]

    for pattern in bid_patterns:
        if re.search(pattern, ultimos_lances, flags=re.IGNORECASE):
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


def load_previous_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_notify(previous, current):
    if previous is None:
        return False, "Primeira execução; apenas salvando estado."

    reasons = []

    if previous.get("digest") != current.get("digest"):
        reasons.append("Mudança detectada no conteúdo monitorado.")

    if normalize_text(previous.get("ultimos_lances", "")) != normalize_text(current.get("ultimos_lances", "")):
        reasons.append("A seção 'Últimos Lances' foi alterada.")

    prev_has_bid = bool(previous.get("found_bid_indicators"))
    curr_has_bid = bool(current.get("found_bid_indicators"))
    if curr_has_bid and not prev_has_bid:
        reasons.append("Possível lance identificado.")

    return (len(reasons) > 0, " ".join(reasons))


def send_telegram(message_text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message_text,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()


def build_message(previous, current, reason: str) -> str:
    old_lances = previous.get("ultimos_lances", "(sem estado anterior)") if previous else "(sem estado anterior)"
    new_lances = current.get("ultimos_lances", "")

    return (
        "🚨 Alerta do leilão\n\n"
        f"Motivo: {reason}\n\n"
        f"Título: {current.get('title', '')}\n"
        f"Status: {current.get('status', '')}\n"
        f"URL: {current.get('url', '')}\n\n"
        f"Últimos Lances (anterior):\n{old_lances}\n\n"
        f"Últimos Lances (atual):\n{new_lances}\n\n"
        f"Verificado em: {current.get('checked_at_utc')}"
    )


def main():
    try:
        html = fetch_page()
    except Exception as e:
        send_telegram(f"⚠️ Falha ao acessar o leilão: {type(e).__name__}: {e}")
        raise

    current = extract_snapshot(html)
    previous = load_previous_state()

    print(json.dumps(current, ensure_ascii=False, indent=2))

    notify, reason = should_notify(previous, current)

    if notify:
        send_telegram(build_message(previous, current, reason))

    save_state(current)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise
