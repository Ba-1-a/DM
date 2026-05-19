import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Silakan lengkapi data ini dari Discord Developer Portal milikmu
TOKEN = os.getenv('DISCORD_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID') # ID Bot kamu (bisa dilihat di tab OAuth2 Developer Portal)

URL = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"

headers = {
    "Authorization": f"Bot {TOKEN}",
    "Content-Type": "application/json"
}

# Definisi struktur Slash Commands yang akan dipasang di sistem Discord
commands_to_register = [
    {
        "name": "aksi",
        "description": "Kirimkan tindakan petualanganmu ke Dungeon Master",
        "options": [
            {
                "name": "tindakan",
                "description": "Apa yang ingin dilakukan karaktermu?",
                "type": 3, # Tipe data STRING
                "required": True
            }
        ]
    },
    {
        "name": "status",
        "description": "Melihat status karakter terbaru langsung dari awan GitHub"
    },
    {
        "name": "luka",
        "description": "Mengurangi HP karakter Bee dan auto-save ke GitHub",
        "options": [
            {
                "name": "damage",
                "description": "Berapa damage yang diterima Bee?",
                "type": 4, # Tipe data INTEGER
                "required": True
            }
        ]
    }
]

def register():
    print("Memulai pendaftaran Slash Commands...")
    for cmd in commands_to_register:
        response = requests.post(URL, headers=headers, json=cmd)
        if response.status_code in [200, 201]:
            print(f"✅ Berhasil mendaftarkan perintah: /{cmd['name']}")
        else:
            print(f"❌ Gagal mendaftarkan /{cmd['name']}: {response.text}")

if __name__ == "__main__":
    if not TOKEN or not APPLICATION_ID:
        print("❌ Error: Harap lengkapi DISCORD_TOKEN dan APPLICATION_ID di file .env terlebih dahulu!")
    else:
        register()