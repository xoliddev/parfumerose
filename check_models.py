"""
Mavjud Gemini modellarini ro'yxatini olish uchun yordamchi skript.
Buni ishga tushiring: python check_models.py
"""
import google.generativeai as genai
from config import GEMINI_API_KEYS

if not GEMINI_API_KEYS:
    print("❌ GEMINI_API_KEYS yoki GEMINI_API_KEY topilmadi!")
else:
    key = GEMINI_API_KEYS[0]
    print(f"🔑 Kalit ishlatilmoqda: {key[:10]}...{key[-5:]}")
    
    genai.configure(api_key=key)
    
    print("\n📋 Mavjud modellar ro'yxati:")
    print("-" * 50)
    for model in genai.list_models():
        if 'generateContent' in model.supported_generation_methods:
            print(f"  ✅ {model.name}")
