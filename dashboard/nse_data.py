"""
NSE stock universe with sector classifications.
Symbol keys match Kite's NSE trading symbols exactly.
Used by the screener to organise stocks sector-wise.
"""

# ── Sector → list of NSE trading symbols ─────────────────────────────────────
SECTORS: dict[str, list[str]] = {
    "NIFTY 50": [
        "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
        "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BHARTIARTL", "BPCL",
        "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
        "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
        "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK",
        "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
        "LTIM", "M&M", "MARUTI", "NESTLEIND", "NTPC",
        "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
        "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
        "TCS", "TECHM", "TITAN", "ULTRACEMCO", "WIPRO",
    ],
    "Banking": [
        "AUBANK", "AXISBANK", "BANDHANBNK", "BANKBARODA", "CUB",
        "DCBBANK", "FEDERALBNK", "HDFCBANK", "ICICIBANK", "IDFCFIRSTB",
        "INDUSINDBK", "J&KBANK", "KARURVYSYA", "KOTAKBANK", "MAHABANK",
        "PNB", "RBLBANK", "SBIN", "SOUTHBANK", "UCOBANK",
        "UNIONBANK", "YESBANK",
    ],
    "IT & Technology": [
        "COFORGE", "CYIENT", "HCLTECH", "INFY", "KPITTECH",
        "LTIM", "LTTS", "MASTEK", "MPHASIS", "OFSS",
        "PERSISTENT", "TATAELXSI", "TCS", "TECHM", "WIPRO",
        "ZENSARTECH",
    ],
    "Pharma & Healthcare": [
        "ABBOTINDIA", "AJANTPHARM", "ALKEM", "APOLLOHOSP", "AUROPHARMA",
        "BIOCON", "CIPLA", "DIVISLAB", "DRREDDY", "GLAND",
        "GRANULES", "IPCALAB", "LAURUSLABS", "LUPIN", "MAXHEALTH",
        "NATCOPHARM", "SUNPHARMA", "TORNTPHARM", "ZYDUSLIFE",
    ],
    "Auto & Auto Ancillaries": [
        "ASHOKLEY", "BAJAJ-AUTO", "BALKRISIND", "BHARATFORG", "BOSCHLTD",
        "EICHERMOT", "EXIDEIND", "HEROMOTOCO", "M&M", "MAHINDCIE",
        "MARUTI", "MOTHERSON", "MRF", "TATAMOTORS", "TVSMOTOR",
        "WABCOINDIA",
    ],
    "FMCG & Consumer": [
        "BRITANNIA", "COLPAL", "DABUR", "EMAMILTD", "GODREJCP",
        "HINDUNILVR", "ITC", "MARICO", "MCDOWELL-N", "NESTLEIND",
        "PGHH", "RADICO", "TATACONSUM", "UBL", "VBL",
    ],
    "Energy & Power": [
        "ADANIGREEN", "ADANIPOWER", "BPCL", "COALINDIA", "GAIL",
        "IOC", "NHPC", "NTPC", "ONGC", "POWERGRID",
        "RELIANCE", "TATAPOWER", "TORNTPOWER",
    ],
    "Metals & Mining": [
        "ADANIENT", "APLAPOLLO", "HINDALCO", "HINDCOPPER", "JSWSTEEL",
        "NATIONALUM", "NMDC", "SAIL", "TATASTEEL", "VEDL",
        "WELCORP",
    ],
    "Financial Services": [
        "ANGELONE", "BAJAJFINSV", "BAJFINANCE", "CANFINHOME", "CHOLAFIN",
        "HDFCAMC", "HDFCLIFE", "ICICIGI", "ICICIPRULI", "LICI",
        "M&MFIN", "MANAPPURAM", "MUTHOOTFIN", "POONAWALLA", "SBICARD",
        "SBILIFE", "SHRIRAMFIN",
    ],
    "Realty": [
        "BRIGADE", "DLF", "GODREJPROP", "MAHLIFE", "OBEROIRLTY",
        "PHOENIXLTD", "PRESTIGE", "SOBHA", "SUNTECK",
    ],
    "Infrastructure & Capital Goods": [
        "ABB", "BHEL", "BEL", "CUMMINSIND", "HAL",
        "IRFC", "L&TFH", "LT", "POLYCAB", "SIEMENS",
        "SUPREMEIND", "TIINDIA", "THERMAX",
    ],
    "Telecom": [
        "BHARTIARTL", "INDUSTOWER", "MTNL", "TATACOMM",
    ],
    "Chemicals": [
        "AAVAS", "ATUL", "DEEPAKNTR", "FLUOROCHEM", "GNFC",
        "NAVINFLUOR", "PIDILITIND", "PIIND", "SRF", "TATACHEM",
        "UPL", "VINDHYATEL",
    ],
    "Media & Entertainment": [
        "NAZARA", "NETWORK18", "PVR", "SAREGAMA", "SUNTV",
        "ZEEL",
    ],
    "Midcap Select": [
        "ABCAPITAL", "ASTRAL", "CROMPTON", "DELHIVERY", "DIXON",
        "GLENMARK", "HINDPETRO", "JIOFIN", "LUPIN", "MFSL",
        "PAGEIND", "PETRONET", "VOLTAS",
    ],
}

# ── Symbol → {name, sector} ───────────────────────────────────────────────────
STOCK_INFO: dict[str, dict] = {
    # NIFTY 50 / Large-cap
    "RELIANCE":    {"name": "Reliance Industries",         "sector": "Energy & Power"},
    "TCS":         {"name": "Tata Consultancy Services",   "sector": "IT & Technology"},
    "HDFCBANK":    {"name": "HDFC Bank",                   "sector": "Banking"},
    "ICICIBANK":   {"name": "ICICI Bank",                  "sector": "Banking"},
    "INFY":        {"name": "Infosys",                     "sector": "IT & Technology"},
    "SBIN":        {"name": "State Bank of India",         "sector": "Banking"},
    "BHARTIARTL":  {"name": "Bharti Airtel",               "sector": "Telecom"},
    "KOTAKBANK":   {"name": "Kotak Mahindra Bank",         "sector": "Banking"},
    "AXISBANK":    {"name": "Axis Bank",                   "sector": "Banking"},
    "LT":          {"name": "Larsen & Toubro",             "sector": "Infrastructure & Capital Goods"},
    "HINDUNILVR":  {"name": "Hindustan Unilever",          "sector": "FMCG & Consumer"},
    "ITC":         {"name": "ITC",                         "sector": "FMCG & Consumer"},
    "BAJFINANCE":  {"name": "Bajaj Finance",               "sector": "Financial Services"},
    "BAJAJFINSV":  {"name": "Bajaj Finserv",               "sector": "Financial Services"},
    "MARUTI":      {"name": "Maruti Suzuki",               "sector": "Auto & Auto Ancillaries"},
    "WIPRO":       {"name": "Wipro",                       "sector": "IT & Technology"},
    "HCLTECH":     {"name": "HCL Technologies",            "sector": "IT & Technology"},
    "ASIANPAINT":  {"name": "Asian Paints",                "sector": "Chemicals"},
    "M&M":         {"name": "Mahindra & Mahindra",         "sector": "Auto & Auto Ancillaries"},
    "ULTRACEMCO":  {"name": "UltraTech Cement",            "sector": "Infrastructure & Capital Goods"},
    "TITAN":       {"name": "Titan Company",               "sector": "FMCG & Consumer"},
    "NESTLEIND":   {"name": "Nestle India",                "sector": "FMCG & Consumer"},
    "TATASTEEL":   {"name": "Tata Steel",                  "sector": "Metals & Mining"},
    "TATAMOTORS":  {"name": "Tata Motors",                 "sector": "Auto & Auto Ancillaries"},
    "POWERGRID":   {"name": "Power Grid Corporation",      "sector": "Energy & Power"},
    "NTPC":        {"name": "NTPC",                        "sector": "Energy & Power"},
    "ONGC":        {"name": "ONGC",                        "sector": "Energy & Power"},
    "COALINDIA":   {"name": "Coal India",                  "sector": "Energy & Power"},
    "SUNPHARMA":   {"name": "Sun Pharmaceutical",          "sector": "Pharma & Healthcare"},
    "TECHM":       {"name": "Tech Mahindra",               "sector": "IT & Technology"},
    "LTIM":        {"name": "LTIMindtree",                 "sector": "IT & Technology"},
    "ADANIPORTS":  {"name": "Adani Ports & SEZ",           "sector": "Infrastructure & Capital Goods"},
    "ADANIENT":    {"name": "Adani Enterprises",           "sector": "Metals & Mining"},
    "INDUSINDBK":  {"name": "IndusInd Bank",               "sector": "Banking"},
    "HDFCLIFE":    {"name": "HDFC Life Insurance",         "sector": "Financial Services"},
    "SBILIFE":     {"name": "SBI Life Insurance",          "sector": "Financial Services"},
    "GRASIM":      {"name": "Grasim Industries",           "sector": "Infrastructure & Capital Goods"},
    "HINDALCO":    {"name": "Hindalco Industries",         "sector": "Metals & Mining"},
    "JSWSTEEL":    {"name": "JSW Steel",                   "sector": "Metals & Mining"},
    "BPCL":        {"name": "Bharat Petroleum",            "sector": "Energy & Power"},
    "EICHERMOT":   {"name": "Eicher Motors",               "sector": "Auto & Auto Ancillaries"},
    "BAJAJ-AUTO":  {"name": "Bajaj Auto",                  "sector": "Auto & Auto Ancillaries"},
    "HEROMOTOCO":  {"name": "Hero MotoCorp",               "sector": "Auto & Auto Ancillaries"},
    "DIVISLAB":    {"name": "Divi's Laboratories",         "sector": "Pharma & Healthcare"},
    "DRREDDY":     {"name": "Dr. Reddy's Laboratories",   "sector": "Pharma & Healthcare"},
    "CIPLA":       {"name": "Cipla",                       "sector": "Pharma & Healthcare"},
    "APOLLOHOSP":  {"name": "Apollo Hospitals",            "sector": "Pharma & Healthcare"},
    "SHRIRAMFIN":  {"name": "Shriram Finance",             "sector": "Financial Services"},
    "BRITANNIA":   {"name": "Britannia Industries",        "sector": "FMCG & Consumer"},
    "TATACONSUM":  {"name": "Tata Consumer Products",      "sector": "FMCG & Consumer"},

    # Banking
    "AUBANK":      {"name": "AU Small Finance Bank",       "sector": "Banking"},
    "BANDHANBNK":  {"name": "Bandhan Bank",                "sector": "Banking"},
    "BANKBARODA":  {"name": "Bank of Baroda",              "sector": "Banking"},
    "CUB":         {"name": "City Union Bank",             "sector": "Banking"},
    "DCBBANK":     {"name": "DCB Bank",                    "sector": "Banking"},
    "FEDERALBNK":  {"name": "Federal Bank",                "sector": "Banking"},
    "IDFCFIRSTB":  {"name": "IDFC First Bank",             "sector": "Banking"},
    "J&KBANK":     {"name": "Jammu & Kashmir Bank",        "sector": "Banking"},
    "KARURVYSYA":  {"name": "Karur Vysya Bank",            "sector": "Banking"},
    "MAHABANK":    {"name": "Bank of Maharashtra",         "sector": "Banking"},
    "PNB":         {"name": "Punjab National Bank",        "sector": "Banking"},
    "RBLBANK":     {"name": "RBL Bank",                    "sector": "Banking"},
    "SOUTHBANK":   {"name": "South Indian Bank",           "sector": "Banking"},
    "UCOBANK":     {"name": "UCO Bank",                    "sector": "Banking"},
    "UNIONBANK":   {"name": "Union Bank of India",         "sector": "Banking"},
    "YESBANK":     {"name": "Yes Bank",                    "sector": "Banking"},

    # IT & Technology
    "COFORGE":     {"name": "Coforge",                     "sector": "IT & Technology"},
    "CYIENT":      {"name": "Cyient",                      "sector": "IT & Technology"},
    "KPITTECH":    {"name": "KPIT Technologies",           "sector": "IT & Technology"},
    "LTTS":        {"name": "L&T Technology Services",     "sector": "IT & Technology"},
    "MASTEK":      {"name": "Mastek",                      "sector": "IT & Technology"},
    "MPHASIS":     {"name": "Mphasis",                     "sector": "IT & Technology"},
    "OFSS":        {"name": "Oracle Financial Services",   "sector": "IT & Technology"},
    "PERSISTENT":  {"name": "Persistent Systems",          "sector": "IT & Technology"},
    "TATAELXSI":   {"name": "Tata Elxsi",                  "sector": "IT & Technology"},
    "ZENSARTECH":  {"name": "Zensar Technologies",         "sector": "IT & Technology"},

    # Pharma & Healthcare
    "ABBOTINDIA":  {"name": "Abbott India",                "sector": "Pharma & Healthcare"},
    "AJANTPHARM":  {"name": "Ajanta Pharma",               "sector": "Pharma & Healthcare"},
    "ALKEM":       {"name": "Alkem Laboratories",          "sector": "Pharma & Healthcare"},
    "AUROPHARMA":  {"name": "Aurobindo Pharma",            "sector": "Pharma & Healthcare"},
    "BIOCON":      {"name": "Biocon",                      "sector": "Pharma & Healthcare"},
    "GLAND":       {"name": "Gland Pharma",                "sector": "Pharma & Healthcare"},
    "GRANULES":    {"name": "Granules India",              "sector": "Pharma & Healthcare"},
    "IPCALAB":     {"name": "IPCA Laboratories",           "sector": "Pharma & Healthcare"},
    "LAURUSLABS":  {"name": "Laurus Labs",                 "sector": "Pharma & Healthcare"},
    "LUPIN":       {"name": "Lupin",                       "sector": "Pharma & Healthcare"},
    "MAXHEALTH":   {"name": "Max Healthcare Institute",    "sector": "Pharma & Healthcare"},
    "NATCOPHARM":  {"name": "Natco Pharma",                "sector": "Pharma & Healthcare"},
    "TORNTPHARM":  {"name": "Torrent Pharmaceuticals",    "sector": "Pharma & Healthcare"},
    "ZYDUSLIFE":   {"name": "Zydus Lifesciences",          "sector": "Pharma & Healthcare"},

    # Auto
    "ASHOKLEY":    {"name": "Ashok Leyland",               "sector": "Auto & Auto Ancillaries"},
    "BALKRISIND":  {"name": "Balkrishna Industries",       "sector": "Auto & Auto Ancillaries"},
    "BHARATFORG":  {"name": "Bharat Forge",                "sector": "Auto & Auto Ancillaries"},
    "BOSCHLTD":    {"name": "Bosch",                       "sector": "Auto & Auto Ancillaries"},
    "EXIDEIND":    {"name": "Exide Industries",            "sector": "Auto & Auto Ancillaries"},
    "MAHINDCIE":   {"name": "Mahindra CIE Automotive",     "sector": "Auto & Auto Ancillaries"},
    "MOTHERSON":   {"name": "Samvardhana Motherson Intl.", "sector": "Auto & Auto Ancillaries"},
    "MRF":         {"name": "MRF",                         "sector": "Auto & Auto Ancillaries"},
    "TVSMOTOR":    {"name": "TVS Motor Company",           "sector": "Auto & Auto Ancillaries"},
    "WABCOINDIA":  {"name": "Wabco India",                 "sector": "Auto & Auto Ancillaries"},

    # FMCG
    "COLPAL":      {"name": "Colgate-Palmolive India",     "sector": "FMCG & Consumer"},
    "DABUR":       {"name": "Dabur India",                 "sector": "FMCG & Consumer"},
    "EMAMILTD":    {"name": "Emami",                       "sector": "FMCG & Consumer"},
    "GODREJCP":    {"name": "Godrej Consumer Products",    "sector": "FMCG & Consumer"},
    "MARICO":      {"name": "Marico",                      "sector": "FMCG & Consumer"},
    "MCDOWELL-N":  {"name": "United Spirits",              "sector": "FMCG & Consumer"},
    "PGHH":        {"name": "Procter & Gamble H&H",        "sector": "FMCG & Consumer"},
    "RADICO":      {"name": "Radico Khaitan",              "sector": "FMCG & Consumer"},
    "UBL":         {"name": "United Breweries",            "sector": "FMCG & Consumer"},
    "VBL":         {"name": "Varun Beverages",             "sector": "FMCG & Consumer"},

    # Energy & Power
    "ADANIGREEN":  {"name": "Adani Green Energy",          "sector": "Energy & Power"},
    "ADANIPOWER":  {"name": "Adani Power",                 "sector": "Energy & Power"},
    "GAIL":        {"name": "GAIL India",                  "sector": "Energy & Power"},
    "IOC":         {"name": "Indian Oil Corporation",      "sector": "Energy & Power"},
    "NHPC":        {"name": "NHPC",                        "sector": "Energy & Power"},
    "TATAPOWER":   {"name": "Tata Power Company",          "sector": "Energy & Power"},
    "TORNTPOWER":  {"name": "Torrent Power",               "sector": "Energy & Power"},

    # Metals
    "APLAPOLLO":   {"name": "APL Apollo Tubes",            "sector": "Metals & Mining"},
    "HINDCOPPER":  {"name": "Hindustan Copper",            "sector": "Metals & Mining"},
    "NATIONALUM":  {"name": "National Aluminium Co.",      "sector": "Metals & Mining"},
    "NMDC":        {"name": "NMDC",                        "sector": "Metals & Mining"},
    "SAIL":        {"name": "Steel Authority of India",    "sector": "Metals & Mining"},
    "VEDL":        {"name": "Vedanta",                     "sector": "Metals & Mining"},
    "WELCORP":     {"name": "Welspun Corp",                "sector": "Metals & Mining"},

    # Financial Services
    "ANGELONE":    {"name": "Angel One",                   "sector": "Financial Services"},
    "CANFINHOME":  {"name": "Can Fin Homes",               "sector": "Financial Services"},
    "CHOLAFIN":    {"name": "Cholamandalam Investment",    "sector": "Financial Services"},
    "HDFCAMC":     {"name": "HDFC Asset Management",       "sector": "Financial Services"},
    "ICICIGI":     {"name": "ICICI Lombard General Ins.",  "sector": "Financial Services"},
    "ICICIPRULI":  {"name": "ICICI Prudential Life Ins.",  "sector": "Financial Services"},
    "LICI":        {"name": "Life Insurance Corp. India",  "sector": "Financial Services"},
    "M&MFIN":      {"name": "Mahindra & Mahindra Fin.",    "sector": "Financial Services"},
    "MANAPPURAM":  {"name": "Manappuram Finance",          "sector": "Financial Services"},
    "MUTHOOTFIN":  {"name": "Muthoot Finance",             "sector": "Financial Services"},
    "POONAWALLA":  {"name": "Poonawalla Fincorp",          "sector": "Financial Services"},
    "SBICARD":     {"name": "SBI Cards & Payment Svcs.",   "sector": "Financial Services"},

    # Realty
    "BRIGADE":     {"name": "Brigade Enterprises",        "sector": "Realty"},
    "DLF":         {"name": "DLF",                         "sector": "Realty"},
    "GODREJPROP":  {"name": "Godrej Properties",           "sector": "Realty"},
    "MAHLIFE":     {"name": "Mahindra Lifespace Dev.",     "sector": "Realty"},
    "OBEROIRLTY":  {"name": "Oberoi Realty",               "sector": "Realty"},
    "PHOENIXLTD":  {"name": "The Phoenix Mills",           "sector": "Realty"},
    "PRESTIGE":    {"name": "Prestige Estates Projects",   "sector": "Realty"},
    "SOBHA":       {"name": "Sobha",                       "sector": "Realty"},
    "SUNTECK":     {"name": "Sunteck Realty",              "sector": "Realty"},

    # Infra & Capital Goods
    "ABB":         {"name": "ABB India",                   "sector": "Infrastructure & Capital Goods"},
    "BEL":         {"name": "Bharat Electronics",          "sector": "Infrastructure & Capital Goods"},
    "BHEL":        {"name": "Bharat Heavy Electricals",    "sector": "Infrastructure & Capital Goods"},
    "CUMMINSIND":  {"name": "Cummins India",               "sector": "Infrastructure & Capital Goods"},
    "HAL":         {"name": "Hindustan Aeronautics",       "sector": "Infrastructure & Capital Goods"},
    "IRFC":        {"name": "Indian Railway Fin. Corp.",   "sector": "Infrastructure & Capital Goods"},
    "L&TFH":       {"name": "L&T Finance",                 "sector": "Infrastructure & Capital Goods"},
    "POLYCAB":     {"name": "Polycab India",               "sector": "Infrastructure & Capital Goods"},
    "SIEMENS":     {"name": "Siemens India",               "sector": "Infrastructure & Capital Goods"},
    "SUPREMEIND":  {"name": "Supreme Industries",          "sector": "Infrastructure & Capital Goods"},
    "TIINDIA":     {"name": "Tube Investments of India",   "sector": "Infrastructure & Capital Goods"},
    "THERMAX":     {"name": "Thermax",                     "sector": "Infrastructure & Capital Goods"},

    # Telecom
    "INDUSTOWER":  {"name": "Indus Towers",                "sector": "Telecom"},
    "MTNL":        {"name": "MTNL",                        "sector": "Telecom"},
    "TATACOMM":    {"name": "Tata Communications",         "sector": "Telecom"},

    # Chemicals
    "AAVAS":       {"name": "Aavas Financiers",            "sector": "Chemicals"},
    "ATUL":        {"name": "Atul",                        "sector": "Chemicals"},
    "DEEPAKNTR":   {"name": "Deepak Nitrite",              "sector": "Chemicals"},
    "FLUOROCHEM":  {"name": "Gujarat Fluorochemicals",     "sector": "Chemicals"},
    "GNFC":        {"name": "Gujarat Narmada Valley Fert.","sector": "Chemicals"},
    "NAVINFLUOR":  {"name": "Navin Fluorine Intl.",        "sector": "Chemicals"},
    "PIDILITIND":  {"name": "Pidilite Industries",         "sector": "Chemicals"},
    "PIIND":       {"name": "PI Industries",               "sector": "Chemicals"},
    "SRF":         {"name": "SRF",                         "sector": "Chemicals"},
    "TATACHEM":    {"name": "Tata Chemicals",              "sector": "Chemicals"},
    "UPL":         {"name": "UPL",                         "sector": "Chemicals"},
    "VINDHYATEL":  {"name": "Vindhya Telelinks",           "sector": "Chemicals"},

    # Media
    "NAZARA":      {"name": "Nazara Technologies",         "sector": "Media & Entertainment"},
    "NETWORK18":   {"name": "Network18 Media",             "sector": "Media & Entertainment"},
    "PVR":         {"name": "PVR INOX",                    "sector": "Media & Entertainment"},
    "SAREGAMA":    {"name": "Saregama India",              "sector": "Media & Entertainment"},
    "SUNTV":       {"name": "Sun TV Network",              "sector": "Media & Entertainment"},
    "ZEEL":        {"name": "Zee Entertainment",           "sector": "Media & Entertainment"},

    # Midcap
    "ABCAPITAL":   {"name": "Aditya Birla Capital",        "sector": "Financial Services"},
    "ASTRAL":      {"name": "Astral",                      "sector": "Infrastructure & Capital Goods"},
    "CROMPTON":    {"name": "Crompton Greaves Cons.",      "sector": "Infrastructure & Capital Goods"},
    "DELHIVERY":   {"name": "Delhivery",                   "sector": "Infrastructure & Capital Goods"},
    "DIXON":       {"name": "Dixon Technologies",          "sector": "IT & Technology"},
    "GLENMARK":    {"name": "Glenmark Pharmaceuticals",    "sector": "Pharma & Healthcare"},
    "HINDPETRO":   {"name": "Hindustan Petroleum Corp.",   "sector": "Energy & Power"},
    "JIOFIN":      {"name": "Jio Financial Services",      "sector": "Financial Services"},
    "MFSL":        {"name": "Max Financial Services",      "sector": "Financial Services"},
    "PAGEIND":     {"name": "Page Industries",             "sector": "FMCG & Consumer"},
    "PETRONET":    {"name": "Petronet LNG",                "sector": "Energy & Power"},
    "VOLTAS":      {"name": "Voltas",                      "sector": "Infrastructure & Capital Goods"},
    "ADANITRANS":  {"name": "Adani Transmission",          "sector": "Energy & Power"},
}


def get_sector_for_symbol(symbol: str) -> str:
    """Return the sector for a given symbol, or 'Unknown' if not found."""
    return STOCK_INFO.get(symbol, {}).get("sector", "Unknown")


def get_name_for_symbol(symbol: str) -> str:
    """Return the company name for a given symbol."""
    return STOCK_INFO.get(symbol, {}).get("name", symbol)


def all_symbols() -> list[str]:
    """Return a deduplicated list of all tracked symbols."""
    seen = set()
    out  = []
    for syms in SECTORS.values():
        for s in syms:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out
