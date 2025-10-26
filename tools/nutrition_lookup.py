# tools/nutrition_lookup.py
import requests
from typing import Optional, Dict, Any

SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"

def _pick_prod(data: dict) -> Optional[dict]:
    prods = data.get("products") or []
    if not prods:
        return None
    # Берём запись с наибольшим числом нутриентов
    prods = sorted(prods, key=lambda p: len((p or {}).get("nutriments", {})), reverse=True)
    return prods[0]

def _extract_nutrients(p: dict) -> Dict[str, Any]:
    n = p.get("nutriments", {}) or {}

    def num(key):
        v = n.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            try:
                return float(str(v).replace(",", "."))
            except Exception:
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

def lookup_product_nutrition(product: str, per: str = "100g") -> Dict[str, Any]:
    """
    Ищет нутриенты продукта по названию (Open Food Facts) и возвращает значения на 100 г.
    """
    params = {
        "search_terms": product,
        "search_simple": 1,
        "action": "process",
        "json": 1,
        "page_size": 5,
    }
    r = requests.get(SEARCH_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    p = _pick_prod(data)
    if not p:
        return {"status": "not_found", "query": product, "message": "Продукт не найден."}

    info = _extract_nutrients(p)

    if per != "100g":
        return {"status": "unsupported_per", "message": "Пока только per=100g", "result_100g": info}

    if info["kcal"] is None and all(info.get(k) is None for k in ("protein_g", "fat_g", "carbs_g")):
        return {"status": "incomplete", "query": product, "message": "Нашёлся продукт без полноценной нутрициологии.", "result": info}

    info["status"] = "ok"
    return info
