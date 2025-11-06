# tools/nutrition_lookup.py
import os
import requests
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

# --- Open Food Facts ---
SEARCH_URL_OFF = "https://world.openfoodfacts.org/cgi/search.pl"

# --- Edamam ---
SEARCH_URL_EDAMAM = "https://api.edamam.com/api/food-database/v2/parser"
EDAMAM_APP_ID = os.getenv("EDAMAM_APP_ID")
EDAMAM_APP_KEY = os.getenv("EDAMAM_APP_KEY")


def _pick_prod_off(data: dict) -> Optional[dict]:
    """Выбирает наиболее подходящий продукт из ответа Open Food Facts."""
    prods = data.get("products") or []
    if not prods:
        return None
    # Берём запись с наибольшим числом нутриентов
    prods = sorted(prods, key=lambda p: len((p or {}).get("nutriments", {})), reverse=True)
    return prods[0]

def _extract_nutrients_off(p: dict) -> Dict[str, Any]:
    """Извлекает нутриенты из продукта Open Food Facts."""
    n = p.get("nutriments", {}) or {}

    def num(key):
        v = n.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            try:
                return float(str(v).replace(",", "."))
            except (ValueError, TypeError):
                return None

    kcal = num("energy-kcal_100g")
    if kcal is None:
        kj = num("energy_100g")
        if kj is not None:
            kcal = round(kj / 4.184, 1)

    return {
        "name": p.get("product_name") or p.get("generic_name") or p.get("brands"),
        "per": "100g",
        "kcal": kcal,
        "protein_g": num("proteins_100g"),
        "fat_g": num("fat_100g"),
        "carbs_g": num("carbohydrates_100g"),
        "fiber_g": num("fiber_100g"),
        "sugars_g": num("sugars_100g"),
        "salt_g": num("salt_100g"),
        "source": "openfoodfacts",
        "barcode": p.get("code"),
        "url": p.get("url"),
    }

def lookup_product_nutrition_edamam(product: str) -> Dict[str, Any]:
    """
    Ищет нутриенты продукта по названию через Edamam API.
    !!! ВНИМАНИЕ: Требует EDAMAM_APP_ID и EDAMAM_APP_KEY в .env файле.
    """
    if not EDAMAM_APP_ID or not EDAMAM_APP_KEY:
        return {"status": "error", "message": "Edamam API credentials not configured."}

    params = {
        "ingr": product,
        "app_id": EDAMAM_APP_ID,
        "app_key": EDAMAM_APP_KEY,
    }
    try:
        r = requests.get(SEARCH_URL_EDAMAM, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        return {"status": "error", "message": f"Edamam API request failed: {e}"}

    # Ищем лучший результат в 'parsed' или 'hints'
    food_data = None
    if data.get("parsed"):
        food_data = data["parsed"][0].get("food")
    if not food_data and data.get("hints"):
        food_data = data["hints"][0].get("food")

    if not food_data:
        return {"status": "not_found", "query": product, "message": "Продукт не найден в Edamam."}

    nutrients = food_data.get("nutrients", {})
    info = {
        "name": food_data.get("label"),
        "per": "100g",
        "kcal": nutrients.get("ENERC_KCAL"),
        "protein_g": nutrients.get("PROCNT"),
        "fat_g": nutrients.get("FAT"),
        "carbs_g": nutrients.get("CHOCDF"),
        "fiber_g": nutrients.get("FIBTG"),
        "sugars_g": None,  # Недоступно в базовом ответе
        "salt_g": None,    # Недоступно в базовом ответе
        "source": "edamam",
        "barcode": None,
        "url": food_data.get("uri"),
    }

    if info["kcal"] is None and all(info.get(k) is None for k in ("protein_g", "fat_g", "carbs_g")):
        return {"status": "incomplete", "query": product, "message": "В Edamam найден продукт без нутриентов.", "result": info}

    info["status"] = "ok"
    return info

def lookup_product_nutrition(product: str, per: str = "100g") -> Dict[str, Any]:
    """
    Ищет нутриенты продукта (сначала Open Food Facts, потом Edamam).
    """
    if per != "100g":
        return {"status": "unsupported_per", "message": "Пока поддерживается только per=100g"}

    # 1. Пробуем Open Food Facts
    off_result = None
    try:
        params = {
            "search_terms": product, "search_simple": 1, "action": "process",
            "json": 1, "page_size": 5,
        }
        r = requests.get(SEARCH_URL_OFF, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        p = _pick_prod_off(data)
        if p:
            off_result = _extract_nutrients_off(p)
            is_complete = off_result["kcal"] is not None or off_result["protein_g"] is not None
            if is_complete:
                off_result["status"] = "ok"
                return off_result
    except requests.RequestException:
        pass  # Ошибка сети, переходим к Edamam

    # 2. Если OFF не дал полного результата, пробуем Edamam
    edamam_result = lookup_product_nutrition_edamam(product)
    if edamam_result.get("status") == "ok":
        return edamam_result

    # 3. Если ничего не помогло, возвращаем неполный результат или ошибку
    if off_result:
        return {"status": "incomplete", "query": product, "message": "Нашёлся продукт без полноценной нутрициологии.", "result": off_result}
    else:
        return {"status": "not_found", "query": product, "message": "Продукт не найден ни в одной из баз."}
