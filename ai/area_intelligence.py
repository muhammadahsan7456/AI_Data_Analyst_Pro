"""
AI Data Analyst Pro — Sub-Area & Neighborhood Intelligence Resolver
Parses full address fields (e.g. Consignee_Address) to extract sub-city area breakdowns
(e.g., Gulshan, Saddar, DHA/Defence, Clifton, Johar, Korangi, Nazimabad, Gulberg, Model Town, F-7, F-8).
"""

import re
import pandas as pd
from utils.helpers import expand_city_names_in_df


# Major Sub-Area Keywords Dictionary for Key Pakistani Logistics Hubs
MAJOR_SUB_AREAS = {
    "Karachi": [
        ("Gulshan-e-Iqbal", ["gulshan", "iqbal"]),
        ("DHA / Defence", ["dha", "defence", "defense"]),
        ("Clifton", ["clifton"]),
        ("Saddar / PECHS / Tariq Road", ["saddar", "tariq road", "pechs", "nursery"]),
        ("Gulistan-e-Johar", ["johar"]),
        ("Korangi / Landhi", ["korangi", "landhi"]),
        ("Nazimabad / FB Area", ["nazimabad", "federal b", "fb area"]),
        ("Malir / Airport", ["malir", "airport", "model colony"]),
        ("North Karachi / Surjani", ["north karachi", "surjani", "buffer zone"]),
        ("Bahria Town KHI", ["bahria town", "bahria karachi"]),
        ("SITE / Hawkesbay Industrial", ["site", "industrial area", "hawkesbay", "waria"])
    ],
    "Lahore": [
        ("Gulberg", ["gulberg"]),
        ("DHA Lahore", ["dha lahore", "dha", "defence"]),
        ("Johar Town", ["johar town"]),
        ("Model Town", ["model town"]),
        ("Cantt / Saddar LHE", ["cantt", "saddar lahore"]),
        ("Iqbal Town / Multan Road", ["iqbal town", "multan road"]),
        ("Bahria Town LHE", ["bahria town lahore", "bahria lahore"]),
        ("Ferozepur Road / Garden Town", ["ferozepur", "garden town"]),
        ("Wapda Town / Township", ["wapda town", "township"]),
        ("Baghbanpura / Mughalpura", ["baghbanpura", "mughalpura"])
    ],
    "Islamabad": [
        ("F Sectors (F-6/F-7/F-8/F-10/F-11)", ["f-6", "f-7", "f-8", "f-10", "f-11", "f6", "f7", "f8", "f10", "f11"]),
        ("G Sectors (G-6/G-9/G-11/G-13)", ["g-6", "g-9", "g-11", "g-13", "g6", "g9", "g11", "g13"]),
        ("I Sectors (I-8/I-9/I-10)", ["i-8", "i-9", "i-10", "i8", "i9", "i10"]),
        ("DHA / Bahria ISB", ["dha islamabad", "bahria islamabad", "bahria town"]),
        ("E-Sectors / Blue Area", ["e-7", "e-11", "blue area"])
    ],
    "Rawalpindi": [
        ("Saddar Rawalpindi", ["saddar"]),
        ("Satellite Town", ["satellite town"]),
        ("Commercial Market", ["commercial market"]),
        ("Bahria Town RWP", ["bahria town"]),
        ("Chaklala Scheme / Cantt", ["chaklala", "cantt"]),
        ("Adyala / Westridge", ["adyala", "westridge"])
    ],
    "Multan": [
        ("Bosna Road / Gulgasht", ["gulgasht", "bosan"]),
        ("Cantt Multan", ["cantt"]),
        ("Model Town Multan", ["model town"]),
        ("Shah Rukn-e-Alam", ["shah rukn"])
    ],
    "Peshawar": [
        ("Hayatabad", ["hayatabad"]),
        ("University Road", ["university road"]),
        ("Cantt Peshawar", ["cantt"]),
        ("Peshawar City Area", ["city"])
    ],
    "Faisalabad": [
        ("D Ground / People Colony", ["d ground", "people colony"]),
        ("Canal Road", ["canal road"]),
        ("Madina Town", ["madina town"]),
        ("Civil Lines", ["civil lines"])
    ],
    "Quetta": [
        ("Cantt Quetta", ["cantt"]),
        ("Jinnah Road", ["jinnah road"]),
        ("Satellite Town Quetta", ["satellite town"])
    ],
    "Hyderabad": [
        ("Latifabad", ["latifabad"]),
        ("Qasimabad", ["qasimabad"]),
        ("Auto Bhan Road", ["auto bhan"])
    ]
}


def build_sql_sub_area_case_statement(address_column: str, city_filter: str = None) -> str:
    """
    Generates T-SQL CASE statement for extracting sub-city area names from full address fields.
    """
    case_clauses = []
    
    city_key = None
    if city_filter:
        c_low = city_filter.lower()
        if "lahore" in c_low or "lhe" in c_low:
            city_key = "Lahore"
        elif "karachi" in c_low or "khi" in c_low:
            city_key = "Karachi"
        elif "islamabad" in c_low or "isb" in c_low:
            city_key = "Islamabad"
        elif "rawalpindi" in c_low or "rwp" in c_low:
            city_key = "Rawalpindi"
        elif "multan" in c_low or "mux" in c_low:
            city_key = "Multan"
        elif "peshawar" in c_low or "pew" in c_low:
            city_key = "Peshawar"
        elif "faisalabad" in c_low or "fsd" in c_low:
            city_key = "Faisalabad"
        elif "quetta" in c_low or "uet" in c_low:
            city_key = "Quetta"
        elif "hyderabad" in c_low or "hdd" in c_low:
            city_key = "Hyderabad"

    if not city_key or city_key not in MAJOR_SUB_AREAS:
        return f"[{address_column}]"

    areas_list = MAJOR_SUB_AREAS[city_key]

    for area_name, keywords in areas_list:
        sub_or_conditions = " OR ".join([f"[{address_column}] LIKE '%{kw}%'" for kw in keywords])
        case_clauses.append(f"WHEN ({sub_or_conditions}) THEN '{area_name}'")

    case_statement = f"CASE {' '.join(case_clauses)} ELSE 'Other Neighborhoods / Main City' END"
    return case_statement


def extract_sub_area_breakdown_df(df: pd.DataFrame, address_column: str) -> pd.DataFrame:
    """
    Python fallback to categorize addresses into sub-areas if full T-SQL query is executed.
    """
    if df is None or df.empty or address_column not in df.columns:
        return df

    def categorize_addr(addr):
        if not addr or pd.isna(addr):
            return "Other Neighborhoods"
        addr_low = str(addr).lower()
        
        # Check Karachi sub-areas
        for area_name, keywords in MAJOR_SUB_AREAS["Karachi"]:
            if any(kw in addr_low for kw in keywords):
                return area_name
        return "Main City Area"

    df_copy = df.copy()
    df_copy["Sub_Area"] = df_copy[address_column].apply(categorize_addr)
    return df_copy
