# ai_helpers.py (Yakuniy, To'liq Versiya)

import asyncio
import json
import os
import re
import datetime
import logging
from typing import Dict, Any

# AI va Ovoz uchun kerakli kutubxonalar
import google.generativeai as genai
import azure.cognitiveservices.speech as speechsdk
from pydub import AudioSegment
from pydub.utils import which

# Konfiguratsiyadan kerakli kalitlarni olamiz
from config import GEMINI_API_KEYS, AZURE_SPEECH_KEY, AZURE_SPEECH_REGION

# Biz yaratgan "asboblar" va ma'lumotlar bazasi faylini import qilamiz
from handlers import admin_tools
import database as db


LOCAL_FFMPEG = os.path.join(os.path.dirname(__file__), "ffmpeg.exe")
LOCAL_FFPROBE = os.path.join(os.path.dirname(__file__), "ffprobe.exe")
if os.path.exists(LOCAL_FFMPEG) and not which("ffmpeg"):
    AudioSegment.converter = LOCAL_FFMPEG
if os.path.exists(LOCAL_FFPROBE) and not which("ffprobe"):
    AudioSegment.ffprobe = LOCAL_FFPROBE


# ==============================================================================
# 1. YORDAMCHI FUNKSIYALAR
# ==============================================================================

def to_latin(text: str) -> str:
    """Kirill harflarini lotin harflariga o'giradigan ishonchli funksiya."""
    if not isinstance(text, str):
        return text
    letters = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z', 'и': 'i', 'й': 'y',
        'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
        'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': "'", 'ы': 'i', 'ь': "'", 'э': 'e', 'ю': 'yu',
        'я': 'ya',
        'ў': "o'", 'қ': 'q', 'ғ': 'g\'', 'ҳ': 'h',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'Yo', 'Ж': 'J', 'З': 'Z', 'И': 'I', 'Й': 'Y',
        'К': 'K', 'Л': 'L', 'М': 'M', 'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U', 'Ф': 'F',
        'Х': 'X', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Sh', 'Ъ': "'", 'Ы': 'I', 'Ь': "'", 'Э': 'E', 'Ю': 'Yu',
        'Я': 'Ya',
        'Ў': "O'", 'Қ': 'Q', 'Ғ': 'G\'', 'Ҳ': 'H'
    }
    return "".join([letters.get(char, char) for char in text])


def _prepare_text_for_ai(text: str) -> str:
    """AI'ga yuborishdan oldin matnni tahlil qiladi va pul bilan bog'liq xatolarni tuzatadi."""
    text = text.strip()
    misheard_words = ['son', 'som', 'сўн', 'сом']
    for word in misheard_words:
        text = re.sub(fr'(\d+\s*({word}))', lambda m: m.group(1).replace(word, 'so‘m'), text, flags=re.IGNORECASE)
        text = re.sub(fr'((ming|million|минг|миллион)\s*({word}))', lambda m: m.group(1).replace(word, 'so‘m'), text,
                      flags=re.IGNORECASE)
    money_keywords = ['ming', 'million', 'минг', 'миллион']
    if 'so‘m' not in text.lower() and 'сўм' not in text.lower():
        if any(keyword in text.lower() for keyword in money_keywords) and any(char.isdigit() for char in text):
            for keyword in money_keywords:
                if keyword in text.lower():
                    text = re.sub(f'({keyword})', r'\1 so‘m', text, count=1, flags=re.IGNORECASE)
                    break
    return text


def _parse_month(month: str) -> str:
    """"shu oy", "otgan oy" kabi matnlarni "YYYY-MM" formatiga o'giradi."""
    today = datetime.date.today()
    month = month.lower()
    if "shu oy" in month or "joriy" in month:
        return today.strftime("%Y-%m")
    if "otgan oy" in month:
        first_day_of_current_month = today.replace(day=1)
        last_day_of_last_month = first_day_of_current_month - datetime.timedelta(days=1)
        return last_day_of_last_month.strftime("%Y-%m")
    return month  # Agar "2024-08" kabi kelsa


# ==============================================================================
# 2. YANGI "ASBOB" (Oylik hisoblash funksiyasi)
# ==============================================================================

async def calculate_employee_final_salary(employee_name: str, month: str, admin_tg_id: int | None = None) -> Dict[str, Any]:
    """Xodimning yakuniy maoshini barcha chegirma va to'lovlarni hisobga olgan holda hisoblaydi. 'month' parametri 'shu oy' yoki 'otgan oy' kabi matn bo'lishi mumkin."""
    month_str = _parse_month(month)
    results = {"error": None}
    if not month_str:
        results["error"] = "Oy formati noto'g'ri. 'shu oy', 'otgan oy' yoki 'YYYY-MM' ko'rinishida yozing."
        return results
    try:
        worker_info, error = await admin_tools._resolve_worker_for_admin(employee_name, admin_tg_id)
        if error:
            if error.get("status") == "ambiguous":
                return {"error": error["message"], "nomzodlar": error.get("nomzodlar", [])}
            results["error"] = error["message"]
            return results

        async with db.pool.acquire() as conn:
            worker_id = worker_info['id']
            results["full_name"] = admin_tools._candidate_label(worker_info)
            base_salary = float(worker_info['monthly_salary'] or 0.0)
            results["base_salary"] = base_salary

            total_payments = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM salary_payments WHERE worker_id = $1 AND to_char(payment_date, 'YYYY-MM') = $2",
                worker_id, month_str)
            results["total_payments"] = float(total_payments)

            # TODO: Kelajakda bu yerga kechikishlar va boshqa chegirmalarni hisoblash logikasini qo'shish mumkin
            total_deductions = 0.0
            results["total_deductions"] = total_deductions

            results['final_salary'] = base_salary - total_deductions - float(total_payments)

    except Exception as e:
        logging.error(f"Yakuniy maoshni hisoblashda xato: {e}")
        results["error"] = str(e)
    return results


# ==============================================================================
# 3. GEMINI AI KLIENTINI SOZLASH (MULTI-KEY SUPPORT)
# ==============================================================================

class GeminiKeyManager:
    def __init__(self, keys: list):
        self.keys = keys
        self.current_key_index = 0
        self.model = None
        self._initialize_model()

    def _initialize_model(self):
        if not self.keys:
            logging.warning("Gemini API kalitlari topilmadi!")
            return

        current_key = self.keys[self.current_key_index]
        genai.configure(api_key=current_key)
        
        safety_settings = [{"category": c, "threshold": "BLOCK_NONE"} for c in
                           ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                            "HARM_CATEGORY_DANGEROUS_CONTENT"]]
        
        self.model = genai.GenerativeModel(
            model_name='models/gemini-2.5-flash',
            safety_settings=safety_settings
        )
        print(f"INFO: Gemini ishga tushdi (Kalit indeksi: {self.current_key_index})")

    def rotate_key(self):
        """Navbatdagi kalitga o'tadi."""
        if not self.keys or len(self.keys) <= 1:
            print("INFO: Boshqa zaxira kalitlar yo'q.")
            return False
        
        self.current_key_index = (self.current_key_index + 1) % len(self.keys)
        print(f"WARNING: API kalit almashtirilmoqda... Yangi indeks: {self.current_key_index}")
        self._initialize_model()
        return True

    async def generate_content_with_retry(self, *args, **kwargs):
        """So'rovni bajarishga urinib ko'radi. Agar xato bo'lsa, kalitni almashtirib qayta urinadi."""
        if not self.model:
            return None

        # Maksimum urinishlar soni = kalitlar soni
        max_retries = len(self.keys) if self.keys else 1
        
        for attempt in range(max_retries):
            try:
                return await self.model.generate_content_async(*args, **kwargs)
            except Exception as e:
                error_msg = str(e).lower()
                # 429: Resource Exhausted (Limit tugadi)
                # 403: Permission Denied (Kalit noto'g'ri)
                # 500: Server Error (Ba'zan server xatosida ham boshqa serverga (kalitga) o'tish yordam berishi mumkin)
                if "429" in error_msg or "resource_exhausted" in error_msg or "quota" in error_msg or "403" in error_msg:
                    logging.warning(f"Gemini xatosi (Urinish {attempt+1}/{max_retries}): {e}")
                    if self.rotate_key():
                        await asyncio.sleep(1) # O'tishda ozgina kutamiz
                        continue
                    else:
                        raise e # Boshqa kalit yo'q, xatoni qaytaramiz
                else:
                    raise e # Boshqa turdagi xato bo'lsa, shunchaki to'xtatamiz
        return None

# Global Manager obyektini yaratamiz
gemini_manager = GeminiKeyManager(GEMINI_API_KEYS)

# ==============================================================================
# 4. "ASBOBLAR" RO'YXATI
# ==============================================================================
available_tools = {
    # admin_tools.py dan import qilinganlar:
    "add_salary_payment": admin_tools.add_salary_payment,
    "get_daily_attendance": admin_tools.get_daily_attendance,
    "get_salary_report_for_month": admin_tools.get_salary_report_for_month,
    "list_employees_at_work_now": admin_tools.list_employees_at_work_now,
    "list_absent_employees": admin_tools.list_absent_employees,
    "get_late_arrivals_report": admin_tools.get_late_arrivals_report,
    "get_attendees_for_date": admin_tools.get_attendees_for_date,
    # Shu faylning o'zidan olingan yangi asbob:
    "calculate_employee_final_salary": calculate_employee_final_salary,
    "get_last_session_date": admin_tools.get_last_session_date,
    "get_attendees_for_last_session": admin_tools.get_attendees_for_last_session,
}


# ==============================================================================
# 5. ASOSIY AI MANTIQI (ASBOBLAR BILAN ISHLASH)
# ==============================================================================

async def process_admin_request_with_tools(user_query: str, admin_tg_id: int | None = None) -> str:
    """Foydalanuvchi so'rovini tahlil qiladi, kerakli asbobni chaqiradi va yakuniy javobni qaytaradi."""
    if not gemini_manager.model:
        return "Xatolik: Gemini API kaliti topilmadi yoki noto'g'ri."
    try:
        # Matnni AIga yuborishdan oldin lotinlashtiramiz va "so'm" xatolarini to'g'rilaymiz
        prepared_query = _prepare_text_for_ai(to_latin(user_query))

        response = await gemini_manager.generate_content_with_retry(prepared_query, tools=list(available_tools.values()))
        if not response or not response.candidates:
             return "Xatolik: AI javob bermadi."
             
        response_data = response.candidates[0].content.parts[0]

        if not hasattr(response_data, 'function_call'):
            return "Kechirasiz, bu so'rovni tushunmadim. Aniqroq buyruq bering, masalan: 'Rustam bugun nechida keldi?'"

        function_call = response_data.function_call
        tool_name = (function_call.name or "").strip()
        tool_args = {key: value for key, value in function_call.args.items()} if function_call.args else {}
        print(f"INFO: AI '{tool_name}' asbobini chaqirmoqchi: {tool_args}")

        if tool_name in available_tools:
            tool_function = available_tools[tool_name]
            tool_result = await tool_function(admin_tg_id=admin_tg_id, **tool_args)

            final_response_prompt = (
                "Sen ma'lumotlarni tahlil qilib, foydalanuvchiga chiroyli va tushunarli qilib yetkazib beradigan yordamchisan. "
                "Quyidagi ma'lumotlar asosida o'zbek tilida qisqa va aniq javob qaytar. Faqat javob matnini yoz, ortiqcha izohlarsiz.\n\n"
                f"Foydalanuvchining asl so'rovi: '{user_query}'\n"
                f"Funksiya natijasi (JSON formatida): {json.dumps(tool_result, ensure_ascii=False, indent=2)}"
            )
            final_response = await gemini_manager.generate_content_with_retry(final_response_prompt)
            return final_response.text
        else:
            return (
                "Kechirasiz, bu so'rovni hozircha to'g'ridan-to'g'ri tushunmadim. "
                "Xodimning ismini va amalni aniqroq yozing. Masalan: 'Zulayho o'qishga ketdi' yoki 'Zulayho dam oldi'."
            )
    except Exception as e:
        logging.error(f"AI bilan ishlashda kutilmagan xatolik: {e}")
        return "Sun'iy intellekt bilan bog'lanishda xatolik yuz berdi."


# ==============================================================================
# 6. OVOZNI MATNGA O'GIRISH (MICROSOFT AZURE)
# ==============================================================================
async def transcribe_voice_to_text(file_path: str) -> str:
    """OGG formatidagi audio faylni WAV'ga o'giradi va Azure yordamida matnga o'giradi."""
    if not (AZURE_SPEECH_KEY and AZURE_SPEECH_REGION):
        print("Xatolik: Azure Speech kalitlari topilmadi.")
        return ""
    ogg_file_path = file_path
    wav_file_path = ogg_file_path.replace(".ogg", ".wav")
    try:
        # Fayl formatini avtomatik aniqlash (frontend webm yuborishi mumkin, lekin nomi .ogg bo'lishi mumkin)
        AudioSegment.from_file(ogg_file_path).export(wav_file_path, format="wav")
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        speech_config.speech_recognition_language = "uz-UZ"
        audio_config = speechsdk.audio.AudioConfig(filename=wav_file_path)
        speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
        result = await asyncio.get_running_loop().run_in_executor(None, speech_recognizer.recognize_once)
        return result.text if result.reason == speechsdk.ResultReason.RecognizedSpeech else ""
    except Exception as e:
        print(f"Azure Speech-to-Text yoki konvertatsiya xatoligi: {e}")
        return ""
    finally:
        # Fayllarni tozalash (xatolik bo'lsa ham)
        for path in [wav_file_path, ogg_file_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass # Fayl band bo'lsa, keyingi safar tozalanadi yoki OS o'zi tozalaydi


# Xodimlar uchun alohida, soddaroq "asboblar" ro'yxati
employee_tools = {
    "forward_to_admin": lambda message_to_admin: True
    # Bu yerda haqiqiy funksiya kerak emas,
    # bizga faqat AI shu nomni qaytarib bersa bo'ldi.
}


async def process_employee_request(user_query: str) -> Dict[str, Any]:
    """
    Xodimning matnli xabarini tahlil qiladi. Agar xabar muhim bo'lsa (iltimos, shikoyat, savol),
    uni adminga yuborish kerakligi haqida belgi qaytaradi. Aks holda, e'tibor bermaydi.
    """
    if not gemini_manager.model:
        return {"action": "error", "message": "Gemini AI sozlanmagan."}

    # Xodimlar uchun maxsus, soddalashtirilgan prompt
    employee_prompt = (
        "Sen xodimlarning matnli xabarlarini tahlil qiluvchi yordamchisan. "
        "Agar xabar biror bir muhim ma'noni anglatsa (iltimos, savol, shikoyat, tushuntirish), "
        "`forward_to_admin` asbobini `message_to_admin` parametri bilan chaqir. "
        "Agar xabar oddiy suhbat bo'lsa (salom, rahmat, ok), HECH QANDAY asbob chaqirma.\n\n"
        f"Xodim xabari: '{user_query}'"
    )

    try:
        response = await gemini_manager.generate_content_with_retry(
            employee_prompt,
            tools=list(employee_tools.keys())  # Faqat nomlarini beramiz
        )
        if not response or not response.candidates:
             return {"action": "ignore"}
             
        response_data = response.candidates[0].content.parts[0]

        if hasattr(response_data, 'function_call'):
            # Agar AI xabarni muhim deb topsa va asbob chaqirsa
            function_call = response_data.function_call
            tool_args = {key: value for key, value in function_call.args.items()}
            return {
                "action": "forward",
                "message": tool_args.get("message_to_admin", user_query)
            }
        else:
            # Agar AI xabarni ahamiyatsiz deb topsa
            return {"action": "ignore"}

    except Exception as e:
        logging.error(f"Xodim so'rovini tahlil qilishda xato: {e}")
        return {"action": "error", "message": "AI bilan bog'lanishda xatolik."}
