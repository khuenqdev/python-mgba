# local.py
import json
import re
import urllib.request
import urllib.parse

from .engines import LANG_NAMES, get_llama_instance


class LocalQwenTranslator:
    @classmethod
    def translate_block(cls, model_path: str, text: str, category: str, speaker: str,
                        src_lang: str, tgt_lang: str, criteria: str, api_key: str,
                        glossary_text: str = "") -> tuple:
        """Translates a categorized block ('dialogue', 'menu', or 'label') and optional speaker."""
        src_name = LANG_NAMES.get(src_lang, src_lang)
        tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)

        if category == "menu":
            role_hint = "This is a selectable game MENU command/option. Translate concisely as a crisp UI action/option (1-6 words)."
        elif category == "label":
            role_hint = "This is a standalone GRAPHIC LABEL, title banner, location name, or HUD tag. Translate concisely as a proper noun or short UI label."
        else:
            sp_ctx = f" spoken by '{speaker}'" if speaker else ""
            role_hint = (
                f"This is a complete unwrapped DIALOGUE/NARRATION sentence{sp_ctx}. "
                "Translate naturally with fluent grammar and appropriate character pronouns/register."
            )

        glossary_section = f"\n{glossary_text}" if glossary_text else ""
        user_prompt = f"Speaker: {speaker}\nText: {text}" if speaker else text
        sys_prompt = (
            f"You are an expert retro video game localizer translating {src_name} GBA text to {tgt_name}.\n"
            f"TASK: {role_hint}\n"
            f"STYLE CRITERIA:\n{criteria}{glossary_section}\n"
            + (
                "Output strictly in format: TranslatedSpeaker | TranslatedText"
                if speaker else
                "Output ONLY the translated text. No notes, no explanations, no romaji."
            )
        )

        raw_out = ""
        llm = get_llama_instance(model_path)
        if llm is not None:
            try:
                resp = llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                raw_out = resp["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"[Local LLM Error] {e}")

        if not raw_out and api_key:
            for model_id in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
                try:
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0"
                    }
                    payload = {
                        "model": model_id,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.1,
                        "max_completion_tokens": 200
                    }
                    req = urllib.request.Request(
                        "https://api.groq.com/openai/v1/chat/completions",
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers
                    )
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        raw_out = data["choices"][0]["message"]["content"].strip()
                        if raw_out:
                            break
                except Exception:
                    continue

        if not raw_out:
            raw_out = _google_translate_fallback(text, src_lang, tgt_lang)
            sp_tr = _google_translate_fallback(speaker, src_lang, tgt_lang) if speaker else ""
            return sp_tr, raw_out

        raw_out = re.sub(r"<think>.*?</think>", "", raw_out, flags=re.DOTALL).strip()
        if speaker and "|" in raw_out:
            sp_tr, tr_text = raw_out.split("|", 1)
            return sp_tr.strip(), tr_text.strip()

        return speaker, raw_out


def _google_translate_fallback(text: str, src: str, tgt: str) -> str:
    if not text.strip():
        return ""
    try:
        url = (
            f"https://translate.googleapis.com/translate_a/single?client=gtx"
            f"&sl={src}&tl={tgt}&dt=t&q={urllib.parse.quote(text)}"
        )
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return "".join(part[0] for part in data[0] if part and part[0]).strip()
    except Exception:
        return text