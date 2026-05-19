import json
import os
import re
import random
import threading
import requests
from flask import Flask, request, jsonify
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from github import Github, Auth
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- KONFIGURASI ENV ---
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
OPENROUTER_KEY = os.getenv('OPENROUTER_API_KEY')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_REPO = os.getenv('GITHUB_REPO')

ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

auth_gh = Auth.Token(GITHUB_TOKEN)
gh = Github(auth=auth_gh)
repo = gh.get_repo(GITHUB_REPO)

DAFTAR_MODEL = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "deepseek/deepseek-v4-flash:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
]

def ambil_data_dari_github(nama_file):
    try:
        file_content = repo.get_contents(nama_file)
        return json.loads(file_content.decoded_content.decode('utf-8')), file_content.sha
    except Exception:
        return {}, None

def simpan_data_ke_github(nama_file, data, sha, pesan_commit="Update data"):
    try:
        konten_baru = json.dumps(data, indent=2)
        if sha:
            repo.update_file(nama_file, pesan_commit, konten_baru, sha)
        else:
            repo.create_file(nama_file, pesan_commit, konten_baru)
        return True
    except Exception as e:
        print(f"Gagal menulis ke GitHub: {e}")
        return False

def panggil_ai_dengan_fallback(messages):
    for model in DAFTAR_MODEL:
        try:
            response = ai_client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=5 
            )
            konten = response.choices[0].message.content
            if konten:
                return konten, model
        except Exception:
            continue
    return "*(DM AI terdiam karena OpenRouter sibuk. Silakan coba lagi.)*", None

def ambil_system_prompt(char_context):
    return f"""Kamu adalah Dungeon Master D&D 5e yang seru, gelap, sastrawi, dan deskriptif.
Pemain saat ini menggunakan karakter berikut:
{char_context}

TUGASMU:
1. Narasikan efek dari tindakan pemain dengan mendalam (maksimal 2 paragraf).
2. JANGAN PERNAH mengocok dadu sendiri. 
3. Jika aksi berisiko, picu tombol mekanik di AKHIR jawabanmu: [ROLL: Nama_Aksi, Rumus_Dadu+Modifier]
   Contoh: [ROLL: Stealth Check, 1d20+10]
4. Gunakan bahasa Indonesia."""

# =========================================================================
# FUNGSI PEKERJA BAYANGAN (BACKGROUND THREAD) UNTUK MENGHINDARI 3 DETIK
# =========================================================================

def background_aksi(app_id, token, channel_id, tindakan):
    char_data, char_sha = ambil_data_dari_github("B.json")
    history_data, hist_sha = ambil_data_dari_github("history.json")

    channel_history = history_data.get(channel_id, [])
    channel_history.append({"role": "user", "content": tindakan})
    if len(channel_history) > 8: channel_history = channel_history[-8:]

    sys_prompt = ambil_system_prompt(json.dumps(char_data))
    messages = [{"role": "system", "content": sys_prompt}] + channel_history

    jawaban_ai, model_terpilih = panggil_ai_dengan_fallback(messages)
    channel_history.append({"role": "assistant", "content": jawaban_ai})
    
    history_data[channel_id] = channel_history
    simpan_data_ke_github("history.json", history_data, hist_sha, "Update history chat")

    match = re.search(r'\[ROLL:\s*(.+?),\s*(.+?)\]', jawaban_ai, re.IGNORECASE)
    if model_terpilih: jawaban_ai += f"\n*(AI: {model_terpilih})*"

    payload_patch = {"content": jawaban_ai}

    if match:
        check_name = match.group(1).strip()
        dice_formula = match.group(2).strip()
        clean_content = re.sub(r'\[ROLL:\s*(.+?),\s*(.+?)\]', '', jawaban_ai, flags=re.IGNORECASE).strip()
        
        payload_patch["content"] = clean_content
        payload_patch["components"] = [{
            "type": 1,
            "components": [{
                "type": 2, "style": 3, "label": f"Kocok Dadu {check_name} 🎲", "custom_id": f"roll|{check_name}|{dice_formula}"
            }]
        }]

    # Edit pesan "Sedang berpikir..." menjadi narasi utuh
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
    requests.patch(url, json=payload_patch)


def background_roll(app_id, token, channel_id, user_name, check_name, dice_formula):
    clean_formula = dice_formula.replace(" ", "")
    match = re.match(r'(\d*)d(\d+)([\+\-]\d+)?', clean_formula, re.IGNORECASE)
    
    final_total = 0
    narasi_dadu = "Gagal membaca format dadu."
    
    if match:
        num_dice = int(match.group(1)) if match.group(1) else 1
        sides = int(match.group(2))
        modifier = int(match.group(3)) if match.group(3) else 0
        
        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        final_total = sum(rolls) + modifier
        detail = f"({' + '.join(map(str, rolls))}) {'+' if modifier >= 0 else '-'} {abs(modifier)}"
        
        narasi_dadu = f"🎲 **{user_name} mengocok {check_name}!**\n📊 `{dice_formula}` | Detail: {detail}\n🎯 **Total Akhir: {final_total}**"

    char_data, char_sha = ambil_data_dari_github("B.json")
    history_data, hist_sha = ambil_data_dari_github("history.json")

    prompt = f"[SISTEM: Hasil dadu {check_name} = {final_total}. Langsung lanjutkan narasi konsekuensi aksi ini secara dramatis!]"
    
    channel_history = history_data.get(channel_id, [])
    channel_history.append({"role": "user", "content": prompt})

    sys_prompt = ambil_system_prompt(json.dumps(char_data))
    messages = [{"role": "system", "content": sys_prompt}] + channel_history[-8:]

    jawaban_ai, model_terpilih = panggil_ai_dengan_fallback(messages)
    channel_history.append({"role": "assistant", "content": jawaban_ai})
    
    history_data[channel_id] = channel_history
    simpan_data_ke_github("history.json", history_data, hist_sha, "Update history pasca roll")

    jawaban_akhir = f"{narasi_dadu}\n\n{jawaban_ai}"
    if model_terpilih: jawaban_akhir += f"\n*(AI: {model_terpilih})*"
    
    payload_patch = {"content": jawaban_akhir}
    match_next = re.search(r'\[ROLL:\s*(.+?),\s*(.+?)\]', jawaban_ai, re.IGNORECASE)
    
    if match_next:
        next_check = match_next.group(1).strip()
        next_formula = match_next.group(2).strip()
        clean_content = re.sub(r'\[ROLL:\s*(.+?),\s*(.+?)\]', '', jawaban_akhir, flags=re.IGNORECASE).strip()
        
        payload_patch["content"] = clean_content
        payload_patch["components"] = [{
            "type": 1,
            "components": [{
                "type": 2, "style": 3, "label": f"Kocok Dadu {next_check} 🎲", "custom_id": f"roll|{next_check}|{next_formula}"
            }]
        }]

    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
    requests.patch(url, json=payload_patch)


# =========================================================================
# ENDPOINT WEBHOOK UTAMA (SAPU JAGAT)
# =========================================================================
@app.route('/', defaults={'path': ''}, methods=['POST', 'GET'])
@app.route('/<path:path>', methods=['POST', 'GET'])
def interactions(path):
    if request.method == 'GET':
        return "🤖 Bot Vercel Aktif dan Siap Menerima Perintah!", 200

    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    body = request.get_data(as_text=True)

    if not signature or not timestamp: return jsonify({"error": "Missing sig"}), 401
    try:
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
    except BadSignatureError:
        return jsonify({"error": "Bad sig"}), 401

    payload = request.json
    interaction_type = payload.get('type')

    if interaction_type == 1:
        return jsonify({"type": 1})

    # --- JALUR A: SLASH COMMANDS ---
    if interaction_type == 2:
        command_name = payload['data']['name']
        channel_id = str(payload['channel_id'])
        app_id = payload['application_id']
        token = payload['token']

        if command_name == "status":
            char_data, _ = ambil_data_dari_github("B.json")
            pesan = (
                f"**{char_data['character']['name']}** si {char_data['character']['class']} (Lv.{char_data['character']['level']})\n"
                f"❤️ HP: {char_data['character']['hp_current']}/{char_data['character']['hp_max']}\n"
                f"🥷 Stealth: +{char_data['skills_expertise']['Stealth']}\n"
                f"🗡️ Senjata: {char_data['combat']['main_weapon']}"
            )
            return jsonify({"type": 4, "data": {"content": pesan}})

        elif command_name == "luka":
            # BUG FIX: Gunakan absolut agar angka negatif tetap jadi pengurangan
            damage = abs(payload['data']['options'][0]['value'])
            char_data, char_sha = ambil_data_dari_github("B.json")
            
            hp_lama = char_data['character']['hp_current']
            char_data['character']['hp_current'] -= damage
            hp_baru = char_data['character']['hp_current']

            simpan_data_ke_github("B.json", char_data, char_sha, f"Bee terkena {damage} damage")
            return jsonify({
                "type": 4,
                "data": {"content": f"🗡️ **Bee terkena {damage} damage!** (HP: {hp_lama} ➔ {hp_baru})\n☁️ *Status auto-saved ke GitHub!*"}
            })

        elif command_name == "aksi":
            tindakan = payload['data']['options'][0]['value']
            # JALANKAN PROSES LAMA DI BACKGROUND!
            threading.Thread(target=background_aksi, args=(app_id, token, channel_id, tindakan)).start()
            # KEMBALIKAN TYPE 5 (DEFERRED) SECARA INSTAN KE DISCORD
            return jsonify({"type": 5})

    # --- JALUR B: INTERAKSI TOMBOL ---
    if interaction_type == 3:
        custom_id = payload['data']['custom_id']
        channel_id = str(payload['channel_id'])
        user_name = payload.get('member', {}).get('user', {}).get('global_name') or payload.get('member', {}).get('user', {}).get('username', 'Pemain')
        app_id = payload['application_id']
        token = payload['token']

        if custom_id.startswith("roll|"):
            _, check_name, dice_formula = custom_id.split("|")
            # JALANKAN PROSES LAMA DI BACKGROUND!
            threading.Thread(target=background_roll, args=(app_id, token, channel_id, user_name, check_name, dice_formula)).start()
            # KEMBALIKAN TYPE 5 SECARA INSTAN
            return jsonify({"type": 5})

    return jsonify({"error": "Unknown type"}), 400