import hashlib
import json
import os
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from curl_cffi import requests

URL = "https://www.leiloeirosdebrasilia.com.br/item/4134/detalhes?page=15"
STATE_FILE = "state.json"


def fetch_page() -> str:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.leiloeirosdebrasilia.com.br/",
        "upgrade-insecure-requests": "1",
    }

    response = requests.get(
        URL,
        headers=headers,
        impersonate="chrome",   # importante
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response.text


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_snapshot(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text("\n", strip=True)

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = normalize_text(h1.get_text(" ", strip=True))

    status = ""
    if "Aberto para Lances" in page_text:
        status = "Aberto para Lances"

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


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_notify(previous, current):
    if previous is None:
        return False, "Primeira execução; estado salvo sem notificar."

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


def send_telegram(message_text: str) -> None:
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


def main() -> None:
    try:
        html = fetch_page()
    except Exception as e:
        # opcional: notifica falha de acesso
        send_telegram(f"⚠️ Falha ao acessar o leilão: {type(e).__name__}: {e}")
        raise

    current = extract_snapshot(html)
    previous = load_previous_state()

    notify, reason = should_notify(previous, current)

    print(json.dumps(current, ensure_ascii=False, indent=2))

    if notify:
        message = build_message(previous, current, reason)
        send_telegram(message)
        print(f"Notificação enviada: {reason}")
    else:
        print("Nenhuma mudança relevante detectada.")

    save_state(current)


if __name__ == "__main__":
    main()
