import json
import os
import re
import random
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

# Inisialisasi API OpenRouter & GitHub
ai_client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_KEY,
)

auth_gh = Auth.Token(GITHUB_TOKEN)
gh = Github(auth=auth_gh)
repo = gh.get_repo(GITHUB_REPO)

# Daftar model gratis OpenRouter (Sesuai request, Gemini dihapus)
# Diurutkan dari yang kemampuannya paling stabil untuk Roleplay
DAFTAR_MODEL = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "deepseek/deepseek-v4-flash:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "openrouter/owl-alpha",
    "baidu/cobuddy:free"
]

# --- 1. VERIFIKASI KEAMANAN DISCORD (MANDATORI) ---
def verifikasi_request_discord(req):
    signature = req.headers.get('X-Signature-Ed25519')
    timestamp = req.headers.get('X-Signature-Timestamp')
    body = req.data.decode('utf-8')

    if not signature or not timestamp:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
        return True
    except BadSignatureError:
        return False

# --- 2. SISTEM MEMORI DAN DATA VIA GITHUB ---
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
        print(f"Gagal menulis ke GitHub ({nama_file}): {e}")
        return False

# --- 3. KECERDASAN MULTI-MODEL DENGAN AUTOFALLBACK ---
def panggil_ai_dengan_fallback(messages):
    for model in DAFTAR_MODEL:
        try:
            # Timeout 4 detik per model agar tidak melanggar batas 3 detik Discord
            # (Discord mentoleransi sedikit delay jaringan, tapi kita set ketat)
            response = ai_client.chat.completions.create(
                model=model,
                messages=messages,
                timeout=4 
            )
            konten = response.choices[0].message.content
            if konten:
                return konten, model
        except Exception as e:
            print(f"Model {model} gagal: {e}. Lanjut ke model berikutnya...")
            continue
    return "*(DM AI terdiam. Server OpenRouter sedang sibuk, harap coba beberapa saat lagi.)*", None

def ambil_system_prompt(char_context):
    return f"""Kamu adalah Dungeon Master D&D 5e yang seru, gelap, sastrawi, dan deskriptif.
Pemain saat ini menggunakan karakter berikut:
{char_context}

TUGASMU:
1. Narasikan efek dari tindakan pemain dengan mendalam (maksimal 2 paragraf).
2. JANGAN PERNAH mengocok dadu sendiri di teks ceritamu. 
3. Jika aksi pemain berisiko dan butuh check, kamu WAJIB memicu tombol mekanik dengan menuliskan kode rahasia ini di PALING AKHIR jawabanmu:
   [ROLL: Nama_Aksi, Rumus_Dadu+Modifier_Karakter]
   Contoh: [ROLL: Stealth Check, 1d20+10]
4. Gunakan bahasa Indonesia."""

# --- 4. ENDPOINT WEBHOOK UTAMA ---
@app.route('/api/interactions', methods=['POST'])
def interactions():
    if not verifikasi_request_discord(request):
        return jsonify({"error": "Tanda tangan tidak valid"}), 401

    payload = request.json
    interaction_type = payload.get('type')

    # Discord PING-PONG (Wajib untuk validasi Discord)
    if interaction_type == 1:
        return jsonify({"type": 1})

    # --- JALUR A: SLASH COMMANDS (/aksi, /status, /luka) ---
    if interaction_type == 2:
        command_name = payload['data']['name']
        channel_id = str(payload['channel_id'])

        char_data, char_sha = ambil_data_dari_github("B.json")
        history_data, hist_sha = ambil_data_dari_github("history.json")

        if command_name == "status":
            pesan = (
                f"**{char_data['character']['name']}** si {char_data['character']['class']} (Lv.{char_data['character']['level']})\n"
                f"❤️ HP: {char_data['character']['hp_current']}/{char_data['character']['hp_max']}\n"
                f"🥷 Stealth: +{char_data['skills_expertise']['Stealth']}\n"
                f"🗡️ Senjata: {char_data['combat']['main_weapon']}"
            )
            return jsonify({"type": 4, "data": {"content": pesan}})

        elif command_name == "luka":
            damage = payload['data']['options'][0]['value']
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
            
            channel_history = history_data.get(channel_id, [])
            channel_history.append({"role": "user", "content": tindakan})
            if len(channel_history) > 8:
                channel_history = channel_history[-8:]

            system_prompt = ambil_system_prompt(json.dumps(char_data))
            messages = [{"role": "system", "content": system_prompt}] + channel_history

            jawaban_ai, model_terpilih = panggil_ai_dengan_fallback(messages)
            channel_history.append({"role": "assistant", "content": jawaban_ai})
            
            history_data[channel_id] = channel_history
            simpan_data_ke_github("history.json", history_data, hist_sha, "Update history chat")

            match = re.search(r'\[ROLL:\s*(.+?),\s*(.+?)\]', jawaban_ai, re.IGNORECASE)
            
            # Tambahkan info model yang terpakai di bawah narasi (opsional, bisa dihapus jika mengganggu)
            if model_terpilih:
                jawaban_ai += f"\n*(Merespons menggunakan: {model_terpilih})*"

            response_data = {"type": 4, "data": {"content": jawaban_ai}}

            if match:
                check_name = match.group(1).strip()
                dice_formula = match.group(2).strip()
                clean_content = re.sub(r'\[ROLL:\s*(.+?),\s*(.+?)\]', '', jawaban_ai, flags=re.IGNORECASE).strip()
                
                response_data["data"]["content"] = clean_content
                response_data["data"]["components"] = [{
                    "type": 1,
                    "components": [{
                        "type": 2,
                        "style": 3,
                        "label": f"Kocok Dadu {check_name} 🎲",
                        "custom_id": f"roll|{check_name}|{dice_formula}"
                    }]
                }]
            return jsonify(response_data)

    # --- JALUR B: INTERAKSI TOMBOL NATIVE DISCORD ---
    if interaction_type == 3:
        custom_id = payload['data']['custom_id']
        channel_id = str(payload['channel_id'])
        user_name = payload.get('member', {}).get('user', {}).get('global_name') or payload.get('member', {}).get('user', {}).get('username', 'Pemain')

        if custom_id.startswith("roll|"):
            _, check_name, dice_formula = custom_id.split("|")
            
            clean_formula = dice_formula.replace(" ", "")
            match = re.match(r'(\d*)d(\d+)([\+\-]\d+)?', clean_formula, re.IGNORECASE)
            
            if match:
                num_dice = int(match.group(1)) if match.group(1) else 1
                sides = int(match.group(2))
                modifier = int(match.group(3)) if match.group(3) else 0
                
                rolls = [random.randint(1, sides) for _ in range(num_dice)]
                total_roll = sum(rolls)
                final_total = total_roll + modifier
                
                detail = f"({' + '.join(map(str, rolls))})"
                if modifier != 0:
                    detail += f" {'+' if modifier >= 0 else '-'} {abs(modifier)}"
                
                narasi_dadu = (
                    f"🎲 **{user_name} mengocok dadu untuk {check_name}!**\n"
                    f"📊 **Formula:** `{dice_formula}` | **Detail:** {detail}\n"
                    f"🎯 **Total Akhir:** **{final_total}**"
                )

                char_data, char_sha = ambil_data_dari_github("B.json")
                history_data, hist_sha = ambil_data_dari_github("history.json")

                prompt_otomatis = f"[SISTEM: Hasil dadu {check_name} = {final_total}. JANGAN mengulang cerita, langsung lanjutkan narasi konsekuensi aksi ini secara dramatis!]"
                
                channel_history = history_data.get(channel_id, [])
                channel_history.append({"role": "user", "content": prompt_otomatis})

                system_prompt = ambil_system_prompt(json.dumps(char_data))
                messages = [{"role": "system", "content": system_prompt}] + channel_history[-8:]

                jawaban_ai, model_terpilih = panggil_ai_dengan_fallback(messages)
                channel_history.append({"role": "assistant", "content": jawaban_ai})

                history_data[channel_id] = channel_history
                simpan_data_ke_github("history.json", history_data, hist_sha, "Update history pasca roll")

                jawaban_akhir = f"{narasi_dadu}\n\n{jawaban_ai}"
                if model_terpilih:
                    jawaban_akhir += f"\n*(Merespons menggunakan: {model_terpilih})*"
                
                match_next = re.search(r'\[ROLL:\s*(.+?),\s*(.+?)\]', jawaban_ai, re.IGNORECASE)
                response_data = {
                    "type": 4, 
                    "data": {"content": jawaban_akhir}
                }

                if match_next:
                    next_check = match_next.group(1).strip()
                    next_formula = match_next.group(2).strip()
                    clean_content = re.sub(r'\[ROLL:\s*(.+?),\s*(.+?)\]', '', jawaban_akhir, flags=re.IGNORECASE).strip()
                    
                    response_data["data"]["content"] = clean_content
                    response_data["data"]["components"] = [{
                        "type": 1,
                        "components": [{
                            "type": 2,
                            "style": 3,
                            "label": f"Kocok Dadu {next_check} 🎲",
                            "custom_id": f"roll|{next_check}|{next_formula}"
                        }]
                    }]

                return jsonify(response_data)

    return jsonify({"error": "Tipe interaksi tidak dikenal"}), 400

if __name__ == '__main__':
    app.run(port=3000)
