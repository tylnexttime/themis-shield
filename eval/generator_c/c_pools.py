"""Pools for generator C of the themis-shield evaluation set: names, phrases, organisations, identifier makers.

Written independently of generators A and B (their code and cases were not read). Everything here is fictional.
Python 3.12 standard library only.
"""
from __future__ import annotations

import random
import re

# ----------------------------------------------------------------------------------------------------------------------
# Name pools. Each culture: given names as (name, gender, nickname or None); family names; name order; optional
# precomposed native-script pairs (roman given, roman family, script full name, gender).
# ----------------------------------------------------------------------------------------------------------------------
CULTURES: dict[str, dict] = {
    "anglo": {
        "given": [("Harriet", "f", "Hattie"), ("Declan", "m", None), ("Imogen", "f", "Immy"), ("Rufus", "m", None),
                  ("Tamsin", "f", None), ("Barnaby", "m", "Barney"), ("Rosalind", "f", "Roz"), ("Percy", "m", None),
                  ("Gwendolyn", "f", "Gwen"), ("Lachlan", "m", "Lachie"), ("Edwina", "f", None), ("Wilfred", "m", "Wilf")],
        "family": ["Ashdown", "Pendlebury", "Catchpole", "Winterbourne", "Fairweather", "Tredwell", "Haskett",
                   "Ormerod", "Quayle", "Brightmore", "Sallis", "Netherwood"],
        "order": "gf",
    },
    "italian": {
        "given": [("Ludovica", "f", "Ludo"), ("Tommaso", "m", None), ("Ginevra", "f", None), ("Pierluigi", "m", "Gigi"),
                  ("Benedetta", "f", None), ("Ottavio", "m", None)],
        "family": ["Castellani", "Ferraro", "Vitiello", "Lombardi", "Sgarbi", "Pozzi"],
        "order": "gf",
    },
    "portuguese": {
        "given": [("Thiago", "m", None), ("Luana", "f", None), ("Rodrigo", "m", None), ("Beatriz", "f", "Bia"),
                  ("Caetano", "m", None), ("Mariana", "f", None)],
        "family": ["Albuquerque", "Figueiredo", "Nascimento", "Carvalho", "Paiva", "Ribeiro"],
        "order": "gf",
    },
    "spanish": {
        "given": [("Ximena", "f", None), ("Joaquín", "m", None), ("Renata", "f", None), ("Santiago", "m", "Santi"),
                  ("Valentina", "f", "Vale"), ("Nicolás", "m", "Nico")],
        "family": ["Echeverría", "Zambrano", "Villalobos", "Quiñones", "Aristizábal", "Olmedo"],
        "order": "gf",
    },
    "korean": {
        "given": [("Ji-woo", "f", None), ("Seo-yeon", "f", None), ("Min-jun", "m", None), ("Ha-eun", "f", None),
                  ("Do-yun", "m", None), ("Ye-jin", "f", None)],
        "family": ["Baek", "Hwang", "Seo", "Yoon", "Jang", "Ahn"],
        "order": "gf",
        "script": [("Ji-woo", "Baek", "백지우", "f"), ("Seo-yeon", "Hwang", "황서연", "f"), ("Min-jun", "Seo", "서민준", "m"),
                   ("Ha-eun", "Yoon", "윤하은", "f"), ("Do-yun", "Jang", "장도윤", "m"), ("Ye-jin", "Ahn", "안예진", "f")],
    },
    "chinese": {
        "given": [("Weiming", "m", "Will"), ("Xiaolan", "f", None), ("Zhihao", "m", None), ("Yuxin", "f", None),
                  ("Jianhong", "m", None), ("Meiling", "f", "Mei")],
        "family": ["Ouyang", "Zhou", "Xu", "Feng", "Lu", "Qiu"],
        "order": "gf",
        "script": [("Weiming", "Ouyang", "欧阳伟明", "m"), ("Xiaolan", "Zhou", "周晓兰", "f"), ("Zhihao", "Xu", "徐志浩", "m"),
                   ("Yuxin", "Feng", "冯雨欣", "f"), ("Jianhong", "Lu", "卢建宏", "m"), ("Meiling", "Qiu", "邱美玲", "f")],
    },
    "vietnamese": {
        "given": [("Thị Mai", "f", None), ("Văn Đức", "m", None), ("Ngọc Hân", "f", "Tina"), ("Minh Khang", "m", None),
                  ("Thu Hà", "f", None), ("Quốc Bảo", "m", None)],
        "family": ["Trần", "Phạm", "Hoàng", "Vũ", "Đặng", "Bùi"],
        "order": "fg",
    },
    "arabic": {
        "given": [("Yusra", "f", None), ("Tariq", "m", None), ("Lamia", "f", None), ("Bilal", "m", None),
                  ("Dalal", "f", None), ("Hamza", "m", None)],
        "family": ["Haddad", "Mansour", "Barakat", "Sabbagh", "Khoury", "Zaidan"],
        "order": "gf",
        "script": [("Yusra", "Haddad", "يسرى حداد", "f"), ("Tariq", "Mansour", "طارق منصور", "m"),
                   ("Lamia", "Barakat", "لمياء بركات", "f"), ("Bilal", "Sabbagh", "بلال صباغ", "m")],
    },
    "greek": {
        "given": [("Eleftheria", "f", "Effie"), ("Stavros", "m", None), ("Kalliopi", "f", "Popi"), ("Thanasis", "m", None),
                  ("Despina", "f", None), ("Lefteris", "m", None)],
        "family": ["Mavromatis", "Kanellopoulos", "Xenakis", "Doukas", "Vlachos", "Tsoukalas"],
        "order": "gf",
        "script": [("Eleftheria", "Mavromatis", "Ελευθερία Μαυρομάτη", "f"), ("Stavros", "Kanellopoulos", "Σταύρος Κανελλόπουλος", "m"),
                   ("Kalliopi", "Xenakis", "Καλλιόπη Ξενάκη", "f"), ("Thanasis", "Doukas", "Θανάσης Δούκας", "m")],
    },
    "cyrillic": {
        "given": [("Dragana", "f", None), ("Nemanja", "m", None), ("Oksana", "f", None), ("Bohdan", "m", None),
                  ("Yevgenia", "f", "Zhenya"), ("Milorad", "m", None)],
        "family": ["Jovanović", "Petrović", "Kovalenko", "Lysenko", "Horbach", "Ilić"],
        "order": "gf",
        "script": [("Dragana", "Jovanović", "Драгана Јовановић", "f"), ("Oksana", "Kovalenko", "Оксана Коваленко", "f"),
                   ("Bohdan", "Horbach", "Богдан Горбач", "m"), ("Yevgenia", "Lysenko", "Євгенія Лисенко", "f")],
    },
    "marathi": {
        "given": [("Aarohi", "f", None), ("Vedant", "m", None), ("Ishita", "f", None), ("Pranav", "m", None),
                  ("Sanika", "f", None), ("Omkar", "m", None)],
        "family": ["Deshpande", "Kulkarni", "Joshi", "Bhosale", "Pawar", "Sawant"],
        "order": "gf",
        "script": [("Aarohi", "Deshpande", "आरोही देशपांडे", "f"), ("Vedant", "Kulkarni", "वेदांत कुलकर्णी", "m"),
                   ("Ishita", "Joshi", "इशिता जोशी", "f"), ("Pranav", "Bhosale", "प्रणव भोसले", "m")],
    },
    "thai": {
        "given": [("Kanya", "f", None), ("Thanakorn", "m", None), ("Pimchanok", "f", "Pim"), ("Wichai", "m", None),
                  ("Natthida", "f", None), ("Anurak", "m", None)],
        "family": ["Srisawat", "Thongchai", "Rattanakul", "Chaiyasit", "Boonmee", "Phetcharat"],
        "order": "gf",
        "script": [("Kanya", "Srisawat", "กัญญา ศรีสวัสดิ์", "f"), ("Thanakorn", "Rattanakul", "ธนากร รัตนกุล", "m"),
                   ("Pimchanok", "Boonmee", "พิมพ์ชนก บุญมี", "f")],
    },
    "hebrew": {
        "given": [("Noa", "f", None), ("Yair", "m", None), ("Shira", "f", None), ("Eitan", "m", None),
                  ("Tamar", "f", None), ("Omri", "m", None)],
        "family": ["Ben-Ami", "Shapira", "Peretz", "Mizrahi", "Katzir", "Rosen"],
        "order": "gf",
        "script": [("Noa", "Ben-Ami", "נועה בן-עמי", "f"), ("Yair", "Shapira", "יאיר שפירא", "m"), ("Tamar", "Peretz", "תמר פרץ", "f")],
    },
    "japanese": {
        "given": [("Haruto", "m", None), ("Aoi", "f", None), ("Ren", "m", None), ("Himari", "f", None),
                  ("Sora", "m", None), ("Yui", "f", None)],
        "family": ["Fujimoto", "Ishikawa", "Kobayashi", "Matsuda", "Nakano", "Sugiyama"],
        "order": "gf",
        "script": [("Haruto", "Fujimoto", "藤本陽翔", "m"), ("Aoi", "Ishikawa", "石川葵", "f"), ("Ren", "Matsuda", "松田蓮", "m"),
                   ("Himari", "Nakano", "中野ひまり", "f")],
    },
    "tamil": {
        "given": [("Karthik", "m", None), ("Lakshmi", "f", None), ("Arjun", "m", None), ("Meenakshi", "f", "Meena"),
                  ("Vignesh", "m", "Viggy"), ("Priyanka", "f", None)],
        "family": ["Ramanathan", "Subramaniam", "Venkataraman", "Krishnamurthy", "Natarajan", "Sivakumar"],
        "order": "gf",
    },
    "maori": {
        "given": [("Aroha", "f", None), ("Tane", "m", None), ("Hinewai", "f", None), ("Rangi", "m", None),
                  ("Mereana", "f", None), ("Wiremu", "m", None)],
        "family": ["Ngata", "Parata", "Te Rangi", "Rewi", "Kapa", "Haimona"],
        "order": "gf",
    },
    "pasifika": {
        "given": [("Sione", "m", None), ("Mele", "f", None), ("Tevita", "m", None), ("Losana", "f", None),
                  ("Viliami", "m", None), ("Salote", "f", None)],
        "family": ["Taufa", "Fifita", "Havili", "Latu", "Vuki", "Finau"],
        "order": "gf",
    },
    "nigerian": {
        "given": [("Chidera", "f", None), ("Oluwaseun", "m", "Seun"), ("Ngozi", "f", None), ("Tobenna", "m", "Tobe"),
                  ("Adaeze", "f", None), ("Emeka", "m", None)],
        "family": ["Okonkwo", "Adebayo", "Nwachukwu", "Oyelaran", "Eze", "Balogun"],
        "order": "gf",
    },
    "ethiopian": {
        "given": [("Selam", "f", None), ("Dawit", "m", None), ("Hiwot", "f", None), ("Yonas", "m", None),
                  ("Mekdes", "f", None), ("Tesfaye", "m", None)],
        "family": ["Gebremariam", "Alemu", "Haile", "Bekele", "Tadesse", "Woldemariam"],
        "order": "gf",
    },
    "polish": {
        "given": [("Jagoda", "f", "Jaga"), ("Bartosz", "m", "Bartek"), ("Weronika", "f", "Wera"), ("Mateusz", "m", None),
                  ("Dominika", "f", "Domi"), ("Radosław", "m", "Radek")],
        "family": ["Wiśniewska", "Zieliński", "Szymańska", "Woźniak", "Dąbrowski", "Kaczmarek"],
        "order": "gf",
    },
    "german": {
        "given": [("Annelies", "f", None), ("Jürgen", "m", None), ("Friederike", "f", "Rike"), ("Lukas", "m", None),
                  ("Gudrun", "f", None), ("Matthias", "m", None)],
        "family": ["Steinbrecher", "Hoffmann", "Vogelsang", "Brandt", "Kessler", "Ziegler"],
        "order": "gf",
    },
    "dutch": {
        "given": [("Floortje", "f", "Floor"), ("Daan", "m", None), ("Sanne", "f", None), ("Bram", "m", None),
                  ("Lotte", "f", None), ("Jeroen", "m", None)],
        "family": ["van der Berg", "de Vries", "Jansen", "Bakker", "Visser", "van Dijk"],
        "order": "gf",
    },
    "finnish": {
        "given": [("Aino", "f", None), ("Eero", "m", None), ("Helmi", "f", None), ("Väinö", "m", None),
                  ("Tuuli", "f", None), ("Ilkka", "m", None)],
        "family": ["Koskinen", "Lehtonen", "Mäkelä", "Virtanen", "Niemi", "Hämäläinen"],
        "order": "gf",
    },
    "hungarian": {
        "given": [("Réka", "f", None), ("Bence", "m", None), ("Zsófia", "f", "Zsófi"), ("Ádám", "m", None),
                  ("Eszter", "f", None), ("Gergő", "m", None)],
        "family": ["Szabó", "Kovács", "Tóth", "Nagy", "Horváth", "Fekete"],
        "order": "fg",
    },
    "indonesian": {
        "given": [("Putri", "f", None), ("Agus", "m", None), ("Dewi", "f", None), ("Bayu", "m", None),
                  ("Sari", "f", None), ("Rizky", "m", None)],
        "family": ["Wijaya", "Santoso", "Kusuma", "Hidayat", "Pratama", "Nugroho"],
        "order": "gf",
    },
    "filipino": {
        "given": [("Jhoanna", "f", "Jho"), ("Jericho", "m", "Echo"), ("Marites", "f", None), ("Ronaldo", "m", "Nald"),
                  ("Analyn", "f", None), ("Jomar", "m", None)],
        "family": ["Dela Cruz", "Bautista", "Villanueva", "Pangilinan", "Mendoza", "Dizon"],
        "order": "gf",
    },
    "punjabi": {
        "given": [("Harpreet", "f", None), ("Gurdeep", "m", None), ("Jaswinder", "f", "Jas"), ("Manpreet", "m", "Manny"),
                  ("Sukhvir", "f", None), ("Rajwant", "m", None)],
        "family": ["Sandhu", "Gill", "Dhillon", "Brar", "Sidhu", "Grewal"],
        "order": "gf",
    },
    "persian": {
        "given": [("Niloufar", "f", "Nilou"), ("Arash", "m", None), ("Shirin", "f", None), ("Kaveh", "m", None),
                  ("Parisa", "f", None), ("Babak", "m", None)],
        "family": ["Rostami", "Farahani", "Ahmadi", "Jafari", "Moradi", "Karimi"],
        "order": "gf",
        "script": [("Niloufar", "Rostami", "نیلوفر رستمی", "f"), ("Arash", "Farahani", "آرش فراهانی", "m")],
    },
    "turkish": {
        "given": [("Elif", "f", None), ("Baran", "m", None), ("Zeynep", "f", None), ("Kerem", "m", None),
                  ("Ayşe", "f", None), ("Emre", "m", None)],
        "family": ["Yıldırım", "Kaya", "Demir", "Şahin", "Çelik", "Aydın"],
        "order": "gf",
    },
    "somali": {
        "given": [("Hodan", "f", None), ("Abdirahman", "m", "Abdi"), ("Ayaan", "f", None), ("Liban", "m", None),
                  ("Sahra", "f", None), ("Guled", "m", None)],
        "family": ["Warsame", "Farah", "Jama", "Elmi", "Hersi", "Awale"],
        "order": "gf",
    },
}

# Pronoun sets by gender
PRON = {"f": {"he": "she", "his": "her", "him": "her", "hers": "hers", "He": "She", "His": "Her"},
        "m": {"he": "he", "his": "his", "him": "him", "hers": "his", "He": "He", "His": "His"}}

# ----------------------------------------------------------------------------------------------------------------------
# Sensitive statements. Mostly indirect: the category is rarely named. {he}/{his}/{him} are filled per person.
# Each entry is a predicate phrase (verb included), as the taxonomy's span rule requires.
# ----------------------------------------------------------------------------------------------------------------------
SENS: dict[str, dict[str, list[str]]] = {
    "HEALTH": {
        "pred": [
            "has been off since the chemo started", "is on the waiting list for a knee replacement",
            "takes insulin before lunch and keeps it in the staff fridge", "needs the lights dimmed when a migraine comes on",
            "is twelve weeks along and has told only the roster team", "has an inhaler in the top drawer for the asthma",
            "is still on crutches after the ankle reconstruction", "goes to dialysis on Tuesdays and Thursdays",
            "has been seeing someone about the panic attacks", "had a seizure in the car park last month",
            "is weaning off the antidepressants under {his} GP", "can't lift above shoulder height since the rotator cuff tear",
            "has a pacemaker", "is in remission and back on full hours", "tested positive on Monday and is isolating",
            "has coeliac disease, so the shared toaster is a problem", "is on methotrexate and catches every infection going",
            "has had two miscarriages this year", "manages {his} ADHD with medication {he} takes at 7am",
            "is being treated for gestational diabetes", "had a stent put in over the long weekend",
            "has a shellfish allergy serious enough to need an EpiPen", "gets vertigo on ladders since the inner-ear infection",
            "is doing IVF and will be in and out for scans", "is on a mental health plan with {his} psychologist",
            "lost most of the hearing in {his} left ear after the explosion", "has a heart murmur the medical picked up",
            "sees a rheumatologist for the lupus", "is sleeping badly since the concussion", "is on a GLP-1 for weight and feels nauseous most mornings",
        ],
        "neg": ["is not pregnant", "has no known allergies", "has never been diagnosed with anything",
                "is not on any medication"],
        "cell": ["type 2 diabetes", "epilepsy", "long COVID", "Crohn's disease", "asthma", "bipolar II", "pregnant, due March",
                 "hip replacement (recovering)", "anaphylaxis (peanuts)", "chronic fatigue syndrome", "glaucoma", "PTSD"],
    },
    "DISABILITY": {
        "pred": [
            "lip-reads, so keep your hands away from your face in the briefing", "brings {his} assistance dog to the office",
            "uses a screen reader and can't do the PDF forms", "needs the step-free entrance at the back",
            "has a stammer and prefers written questions", "wears hearing aids in both ears", "has a prosthetic left leg",
            "needs captions on every video call", "has dyslexia and asks for extra time on written tests",
            "is autistic and has a quiet-room arrangement", "has a vision impairment and works at 200% zoom",
            "has an NDIS plan that covers transport to work", "needs an ergonomic chair for {his} spinal injury",
            "cannot stand for longer than ten minutes", "uses a powered wheelchair and needs the hoist bay",
            "has an acquired brain injury and needs instructions one step at a time", "relies on the hearing loop in the hall",
            "has a support worker with {him} two days a week", "uses a communication board", "has chronic pain and a flexible-start arrangement",
            "is colour-blind and can't read the red-green dashboard", "has Tourette's and has asked the team not to react to the tics",
        ],
        "neg": ["does not have a disability", "has no support needs", "has not asked for any adjustments"],
        "cell": ["Deaf, Auslan interpreter", "wheelchair user", "low vision", "ADHD (adjustments agreed)", "hearing aids",
                 "autistic", "amputee (right arm)", "cerebral palsy", "dyslexia", "psychosocial disability"],
    },
    "ETHNICITY": {
        "pred": [
            "is a proud Wiradjuri woman", "is Torres Strait Islander on {his} mother's side", "grew up speaking Dinka at home",
            "is Hazara and came through Indonesia in 2013", "is of Hmong background", "identifies as Kurdish, not Turkish",
            "is Rohingya", "has Ngāi Tahu whakapapa", "is Romani", "is Tamil Sri Lankan", "is Han Chinese from Guangdong",
            "is Ashkenazi", "is Tongan-Australian", "is of Lebanese heritage", "is a Noongar man", "is Oromo",
            "is Palawa", "is Karen, from the camps on the Thai border", "is of Dutch-Indonesian descent", "is Pitjantjatjara",
            "is Yolŋu and goes home for ceremony in the dry", "is Igbo", "is Bosniak", "is Fijian-Indian",
        ],
        "neg": ["is not Aboriginal or Torres Strait Islander", "does not identify as Indigenous", "is not Māori"],
        "cell": ["Yes, Aboriginal", "No", "Torres Strait Islander", "Māori (Ngāpuhi)", "Aboriginal and Torres Strait Islander",
                 "Yes", "Hazara", "Tamil", "Kurdish", "Samoan"],
    },
    "SEXUALITY": {
        "pred_f": [
            "brought {his} wife to the awards night", "is bi and out at work", "is asexual and finds the dating-app chatter uncomfortable",
            "came out last year and {his} girlfriend came to the Christmas do", "is in a same-sex marriage", "is a lesbian",
            "is pansexual", "mentioned {his} girlfriend at the barbecue", "and {his} wife are expecting", "is queer and on the Pride float",
        ],
        "pred_m": [
            "mentioned {his} boyfriend at the barbecue", "is bi and out at work", "came out as gay last year",
            "and {his} husband are adopting", "is in a same-sex marriage", "is pansexual", "took leave for {his} civil partnership with another man",
            "is asexual and finds the dating-app chatter uncomfortable", "brought {his} husband to the awards night", "is gay and on the Pride float",
        ],
        "neg": ["is straight", "is not gay", "is heterosexual"],
        "cell": ["Gay", "Bisexual", "Queer", "Heterosexual", "Lesbian", "Asexual", "Pansexual", "Straight"],
    },
    "RELIGION": {
        "pred": [
            "fasts during Ramadan so skip the lunch meeting", "prays at midday and uses the quiet room",
            "doesn't work Saturdays for religious reasons", "wears a turban and a kirpan", "goes to Mass on Sundays",
            "keeps kosher", "only eats halal", "is a Jehovah's Witness and won't attend the birthday morning tea",
            "observes Lent and skips meat on Fridays", "attends the gurdwara every week", "is Buddhist and meditates in the courtyard at lunch",
            "is a lay preacher at {his} church", "is Baha'i", "goes to temple for Diwali", "wears a hijab",
            "leaves early on Fridays for Shabbat", "is Coptic and fasts before Christmas", "is a practising Hindu and vegetarian for that reason",
            "attends Friday prayers at the mosque", "is a Quaker", "is Mormon and doesn't drink coffee", "is Druze", "is Zoroastrian",
        ],
        "neg": ["is not religious", "is an atheist", "has no religion"],
        "cell": ["Muslim", "Hindu", "No religion", "Sikh", "Orthodox Christian", "halal", "kosher", "Catholic", "Buddhist", "Jewish",
                 "Pentecostal", "Atheist"],
    },
    "CRIMINAL": {
        "pred": [
            "has a suspended sentence hanging over {him}", "is on a good behaviour bond until March", "did eighteen months in Marrowgate",
            "has to report to the police station every Friday", "can't drive because of the drink-driving conviction",
            "has a spent conviction from {his} twenties", "is on a community corrections order", "was charged with assault after the pub fight",
            "has an outstanding warrant in Queensland", "wears an ankle monitor", "is out on bail", "was convicted of fraud in 2019",
            "has two disclosable court outcomes on {his} police check", "finished parole in June", "got off with a caution for the shoplifting",
            "has a restricted licence after the interlock order", "did community service for the graffiti charge",
            "was acquitted of the theft but the charge still shows", "has a juvenile record that was sealed", "is a registered sex offender",
            "can't work with cash because of the embezzlement conviction", "was deported from New Zealand on character grounds",
        ],
        "neg": ["has no criminal record", "has a clear police check", "has never been charged with anything"],
        "cell": ["Disclosable court outcome", "No disclosable outcomes", "Parole until 03/2027", "Pending charge", "Clear",
                 "Spent conviction", "Good behaviour bond", "Working with children check refused"],
    },
    "FAMILY_VIOLENCE": {
        "pred": [
            "has an intervention order against {his} former partner", "can't have {his} roster posted on the noticeboard because of {his} ex",
            "moved into a refuge with the kids last month", "is using {his} ten days of family violence leave",
            "asked that {his} new address never go on the system", "has a safety plan with the police",
            "left after years of abuse at home", "had the locks changed after {his} ex turned up at the house",
            "has a protection order with a 200-metre condition", "is in the safe-at-home program",
            "gets escorted to {his} car after late shifts because of {his} ex-partner", "has a DVO lodged by {his} sister",
            "needs calls from {his} husband screened by reception", "has an AVO that lists the workplace as a protected address",
            "is a respondent on an intervention order taken out by {his} wife", "took the kids and left on a Tuesday night with one bag",
            "is in hiding from {his} ex and uses a PO box for everything", "has flagged {his} brother as a no-contact person",
        ],
        "neg": ["has never experienced family violence", "has no protection orders in place"],
        "cell": ["FDV leave 3 days", "Protection order in place", "Refuge address, do not disclose", "Safety plan on file",
                 "Intervention order (respondent)", "Yes", "No"],
    },
    "UNION": {
        "pred": [
            "is the delegate for the night crew", "sits on the bargaining team for the members", "pays {his} dues through payroll",
            "has been the HSR and the rep since the strike", "walked off with the rest of the members on Tuesday", "is a member of the nurses' union",
            "chairs the local branch", "is the shop steward on the loading dock", "resigned from the union after the vote",
            "is covered by the union's legal fund over the dismissal", "was elected delegate last week", "wore the union badge to the hearing",
            "is organising the depot", "is on the union's state council", "brought a union official to the meeting as {his} support person",
            "signed up half the warehouse to the union", "is the union's nominee on the consultative committee",
        ],
        "neg": ["is not in the union", "is not a union member", "let {his} union membership lapse"],
        "cell": ["Member", "Delegate", "Non-member", "HSR (union)", "Member (dues via payroll)", "Not a member"],
    },
}

# QUASI_ID: descriptions that single out one person without naming them.
QUASI_PROSE = [
    "the only wheelchair user on the night shift at Dock 7", "the sole Mandarin-speaking pharmacist at the {site}",
    "the one apprentice over fifty in the {site} workshop", "the only forklift ticket holder on B crew",
    "the newest hire in the three-person payroll team", "the only woman on the night roster at {site}",
    "the left-handed cellist in the pit orchestra", "the single parent on the graveyard shift who rides the 6:10 bus",
    "the only staff member who speaks Dinka", "the one Deaf employee on the {site} floor",
    "the only overseas-trained vet in the practice", "the solitary beekeeper on the council payroll",
    "the only person in the depot with a helicopter licence", "the one trainee who commutes from Marrowgate by ferry",
    "the oldest member of the kitchen crew, who started in 1979", "the only man in the midwifery team",
    "the sole hyperbaric technician on the island", "the only employee born in Eritrea",
    "the pilot-boat coxswain who lives aboard", "the one receptionist on a 457 visa", "the twin who works the morning shift",
    "the organist who also drives the school bus", "the only mature-age student at the campus doing both Latin and Physics",
]
QUASI_TABLE_TITLES = [  # rare roles for anonymised tables (singling out next to DOB/postcode)
    "Falconer (sole)", "Night pharmacist (only one)", "Organ tuner", "Chaplain", "Pilot boat coxswain", "Hyperbaric technician",
    "Auslan interpreter (only one on site)", "Apiarist", "Crane operator (only female on site)", "Taxidermist", "Cheesemaker",
    "Resident composer", "Lighthouse keeper", "Head sommelier", "Only overseas-trained surgeon", "Sole night security officer",
    "Farrier", "Milliner", "Glassblower", "Bell-ringer", "Sole Dinka interpreter", "Only apprentice over 50", "Thatcher",
    "Dive supervisor (sole)", "Cartographer", "Beekeeper (1 of 1)", "Sign-writer", "Only part-time CEO", "Snake handler",
    "Resident luthier",
]
QUASI_TABLE_COMMON = [  # common roles that do NOT single anyone out in a sizeable team
    "Administration officer", "Customer service officer", "Registered nurse", "Warehouse assistant", "Software engineer",
    "Cleaner", "Teacher", "Driver", "Accounts clerk", "Technician", "Project officer", "Sales associate", "Receptionist",
    "Labourer", "Analyst", "Carer", "Cook", "Security officer", "Mechanic", "Pharmacist",
]

# ----------------------------------------------------------------------------------------------------------------------
# Sensitive look-alikes with no private individual (hard negatives), for the invented organisations
# ----------------------------------------------------------------------------------------------------------------------
POLICY_STATEMENTS = [
    "staff with a disability can request reasonable adjustments", "employees experiencing family or domestic violence may take ten days of paid leave",
    "prayer and reflection rooms are available at every site", "halal and kosher options are provided at catered events",
    "a national police check is required before a start date is confirmed", "union delegates may attend induction sessions",
    "staff who are pregnant may ask for a risk assessment", "mental health first aiders are listed on the intranet",
    "Aboriginal and Torres Strait Islander employees may take ceremonial leave", "same-sex partners are recognised for all leave entitlements",
    "hearing loops are installed in the main meeting rooms", "the Employee Assistance Program is confidential and free",
]
PUBLIC_EVENTS = ["Ramadan", "Pride Month", "NAIDOC Week", "Diwali", "Lunar New Year", "Harmony Week", "R U OK? Day",
                 "International Day of People with Disability", "Easter", "Eid al-Fitr", "Hanukkah", "Vesak"]
PUBLIC_FIGURES = ["Marie Curie", "Ada Lovelace", "Isaac Newton", "Florence Nightingale", "Alan Turing", "Katherine Johnson",
                  "Charles Darwin", "Rosalind Franklin", "Grace Hopper", "Nikola Tesla"]
PUBLIC_PLACES = ["Sydney Harbour Bridge", "the Great Ocean Road", "Uluru", "Lake Burley Griffin", "the Nullarbor Plain",
                 "Cradle Mountain", "the Daintree", "Kings Park"]

FICTION = [  # (character, statement) - invented micro-fiction; the statement is a sensitive look-alike
    ("Captain Odalys Vane", "served seven years in the salt prisons of Marrowgate for a crime she did not commit"),
    ("Brother Tiberon", "fasted forty days in the bell tower and heard the sea answer"),
    ("Ilse of the Reeds", "could not hear the warning horn and read the panic on every face instead"),
    ("the Widow Garrowby", "fled her husband's house in the night with the children sewn into her coat"),
    ("Pell Anstruther", "stole the magistrate's seal and was hanged at the crossroads"),
    ("Doctor Mireille Fontayne", "treated her own consumption with laudanum and sea air"),
    ("Old Hesketh", "led the dockers out on strike the week the ice came"),
    ("Sister Amaranth", "kept the Sabbath so strictly the lighthouse went dark on Saturdays"),
    ("Jory Pennhallow", "loved the blacksmith's son and said so at the harvest fair"),
    ("Tamsin Wake", "limped from the shipwreck on one good leg and a carved whalebone"),
]
NEWS = [  # (organisation, public news statement) - fictional organisations in public proceedings
    ("Brightwater Freight Pty Ltd", "was fined $1.2 million by the Federal Court for underpaying 300 drivers"),
    ("Coralspire Aged Care", "was ordered to pay compensation after a Royal Commission hearing into restraint practices"),
    ("the Northern Reef Council", "lost its appeal over the unlawful dismissal of a whistleblower"),
    ("Kestrel Mining Services", "pleaded guilty to safety breaches after the 2024 conveyor collapse"),
    ("Pennywhistle Childcare", "had its licence suspended by the regulator pending an investigation"),
    ("Greywater Energy", "settled a class action over the 2025 data breach for an undisclosed sum"),
]

# ----------------------------------------------------------------------------------------------------------------------
# Invented workplaces (generator C's own). Nothing here is a real organisation.
# ----------------------------------------------------------------------------------------------------------------------
ORGS: list[dict] = [
    {
        "key": "vet", "name": "Brindle & Moss Veterinary Hospital", "forms": ["Brindle & Moss Veterinary Hospital", "Brindle & Moss", "the hospital"],
        "domain": "brindlemoss.example", "sector": "veterinary",
        "sites": [{"name": "Pelican Flats", "forms": ["Pelican Flats clinic", "Pelican Flats surgery"], "reception": "(02) 5550 2210",
                   "address": "14 Shearwater Avenue, Pelican Flats"},
                  {"name": "Thistlewood", "forms": ["Thistlewood clinic"], "reception": "(02) 7010 2211", "address": "3/90 Bramble Street, Thistlewood"}],
        "buildings": ["Recovery Ward", "Large Animal Barn", "Imaging Suite"],
        "mailboxes": ["bookings@brindlemoss.example", "accounts@brindlemoss.example", "afterhours@brindlemoss.example", "careers@brindlemoss.example"],
        "lines": [("After-hours emergency line", "1300 975 708")],
        "roles": ["Practice Manager", "Head Veterinarian", "Senior Veterinary Nurse", "Clinical Director"],
        "products": [("VX-DHPP", "Canine core vaccination"), ("DENT-2", "Dental grade 2 clean"), ("DESEX-F", "Feline desexing package")],
        "projects": [("PRJ-HOSP-07", "Imaging Suite refit")],
        "committees": ["Clinical Governance Committee", "Wellbeing and Peer Support Group"],
        "initiatives": ["Fear Free handling training", "Mental Health Week morning tea", "Compassion Fatigue workshop"],
        "refs": {"client": "CL-\\d{6}", "invoice": "BM-INV-\\d{5}", "job": "JOB-\\d{5}", "asset": "BMA-\\d{4}"},
        "staff_id": ("EMP-", 5), "customer_id": ("CL-", 6),
        "public_dates": [("Clinic closed (Christmas Day)", "25/12/2026"), ("Puppy school term starts", "02/02/2027"), ("Annual open morning", "14/03/2027")],
    },
    {
        "key": "bakery", "name": "Corvid Lane Bakery Co-operative", "forms": ["Corvid Lane Bakery Co-operative", "Corvid Lane", "the Co-op"],
        "domain": "corvidlane.example", "sector": "food",
        "sites": [{"name": "Ironbark Heights", "forms": ["Ironbark Heights shop", "Ironbark Heights bakehouse"], "reception": "(03) 5550 3310",
                   "address": "22 Corvid Lane, Ironbark Heights"},
                  {"name": "Wattlebend", "forms": ["Wattlebend kiosk"], "reception": "(03) 7010 3311", "address": "Shop 4, Wattlebend Markets, Wattlebend"}],
        "buildings": ["the bakehouse", "the Cool Room", "the Mill Loft"],
        "mailboxes": ["orders@corvidlane.example", "members@corvidlane.example", "hello@corvidlane.example"],
        "lines": [("Wholesale orders line", "1300 975 710")],
        "roles": ["Head Baker", "Co-op Secretary", "Front of House Lead", "Treasurer"],
        "products": [("SRD-800", "Sourdough 800g"), ("RYE-SEED", "Seeded rye"), ("CRS-BOX6", "Croissant box of six")],
        "projects": [("PRJ-OVEN-2", "Second deck oven")],
        "committees": ["Members' Council", "Inclusion Circle"],
        "initiatives": ["Pay-it-forward loaf scheme", "Ramadan night-bake", "Pride Month rainbow cookie"],
        "refs": {"order": "ORD-\\d{6}", "invoice": "CLB-\\d{5}", "member": "MBR-\\d{4}"},
        "staff_id": ("WKR-", 4), "customer_id": ("MBR-", 4),
        "public_dates": [("Annual general meeting", "28/10/2026"), ("Closed for Good Friday", "02/04/2027"), ("Harvest fair stall", "21/03/2027")],
    },
    {
        "key": "legal", "name": "Tarrow Street Community Legal Centre", "forms": ["Tarrow Street Community Legal Centre", "Tarrow Street CLC", "the Centre"],
        "domain": "tarrowclc.example", "sector": "legal",
        "sites": [{"name": "Marrowgate", "forms": ["Marrowgate office"], "reception": "(02) 5550 4410", "address": "Level 2, 18 Tarrow Street, Marrowgate"},
                  {"name": "Cobb's Crossing", "forms": ["Cobb's Crossing outreach"], "reception": "(02) 7010 4411", "address": "Community Hub, 5 Ferry Road, Cobb's Crossing"}],
        "buildings": ["Interview Room 1", "Interview Room 2", "the Duty Lawyer desk"],
        "mailboxes": ["intake@tarrowclc.example", "admin@tarrowclc.example", "volunteers@tarrowclc.example", "privacy@tarrowclc.example"],
        "lines": [("Advice line", "1800 975 709")],
        "roles": ["Principal Solicitor", "Intake Officer", "Duty Lawyer", "Community Education Coordinator"],
        "products": [("SVC-FVA", "Family violence advice clinic"), ("SVC-TEN", "Tenancy advice clinic"), ("SVC-FIN", "Fines clinic")],
        "projects": [("PRJ-LAW-14", "Night court outreach")],
        "committees": ["Management Committee", "Cultural Safety Working Group"],
        "initiatives": ["Law Week open day", "Family violence prevention forum", "Reconciliation Action Plan"],
        "refs": {"file": "TS-\\d{5}/\\d{2}", "matter": "MTR-\\d{6}"},
        "staff_id": ("STF-", 4), "customer_id": ("CLT-", 6),
        "public_dates": [("Management Committee meeting", "03/11/2026"), ("Law Week open day", "17/05/2027"), ("Office closed for stocktake", "30/06/2027")],
    },
    {
        "key": "robotics", "name": "Quillon Robotics Pty Ltd", "forms": ["Quillon Robotics Pty Ltd", "Quillon Robotics", "Quillon"],
        "domain": "quillon.example", "sector": "technology",
        "sites": [{"name": "Foundry Park", "forms": ["Foundry Park lab", "Foundry Park HQ"], "reception": "(03) 5550 5510", "address": "Unit 9, 400 Foundry Park Drive, Foundry Park"}],
        "buildings": ["Bay 3", "the Clean Room", "the Test Arena"],
        "mailboxes": ["support@quillon.example", "security@quillon.example", "noreply@quillon.example", "ops@quillon.example"],
        "lines": [("Support line", "1300 975 711")],
        "roles": ["Chief Technology Officer", "Firmware Lead", "Site Reliability Engineer", "Head of People"],
        "products": [("QR-ARM-7", "Quillon seven-axis arm"), ("QR-GRIP-2", "Soft gripper v2"), ("QR-OS 4.2.1", "Quillon OS")],
        "projects": [("PRJ-ORCA", "Orca controller"), ("PRJ-FINCH", "Finch vision stack")],
        "committees": ["Security Review Board", "Diversity Council"],
        "initiatives": ["Wear It Purple morning tea", "Accessibility sprint", "Hackathon 2026"],
        "refs": {"ticket": "QR-\\d{5}", "build": "build #\\d{4}", "incident": "INC-\\d{4}", "pr": "PR #\\d{3,4}"},
        "staff_id": ("QE", 4), "customer_id": ("ACC-", 6),
        "public_dates": [("QR-OS 4.3 release", "30/11/2026"), ("Hackathon 2026", "06/11/2026"), ("Office shutdown", "24/12/2026")],
    },
    {
        "key": "aged", "name": "Saltbush Grove Residential Aged Care", "forms": ["Saltbush Grove Residential Aged Care", "Saltbush Grove", "the home"],
        "domain": "saltbushgrove.example", "sector": "aged care",
        "sites": [{"name": "Hollow Creek", "forms": ["Hollow Creek home", "Hollow Creek site"], "reception": "(08) 5550 6610", "address": "77 Saltbush Road, Hollow Creek"}],
        "buildings": ["Banksia Wing", "Melaleuca Wing", "the Memory Support Unit", "Sunroom"],
        "mailboxes": ["admissions@saltbushgrove.example", "roster@saltbushgrove.example", "feedback@saltbushgrove.example", "don@saltbushgrove.example"],
        "lines": [("Family enquiries", "1800 975 707")],
        "roles": ["Director of Nursing", "Clinical Care Coordinator", "Lifestyle Coordinator", "Facility Manager"],
        "products": [("PKG-L3", "Home care package level 3"), ("RESP-14", "Respite stay 14 nights")],
        "projects": [("PRJ-MSU", "Memory Support Unit extension")],
        "committees": ["Residents' and Relatives' Committee", "Medication Advisory Committee", "Spiritual Care Group"],
        "initiatives": ["Dementia Action Week", "Falls prevention program", "Multicultural lunch", "Dignity of Risk training"],
        "refs": {"incident": "SG-IR-\\d{4}", "roster": "RST-\\d{6}", "po": "PO-SG-\\d{5}"},
        "staff_id": ("SG", 5), "customer_id": ("RES-", 4),
        "public_dates": [("Accreditation visit", "09/11/2026"), ("Christmas lunch", "18/12/2026"), ("Residents' Committee", "12/11/2026")],
    },
    {
        "key": "council", "name": "Redgum Shire Council", "forms": ["Redgum Shire Council", "Redgum Shire", "the Shire", "the Council"],
        "domain": "redgumshire.example", "sector": "local government",
        "sites": [{"name": "Redgum", "forms": ["Redgum Civic Centre", "the Civic Centre"], "reception": "(02) 5550 7710", "address": "1 Civic Square, Redgum"},
                  {"name": "Yabby Creek", "forms": ["Yabby Creek depot", "Yabby Creek Works Depot"], "reception": "(02) 7010 7711", "address": "Lot 3, Quarry Road, Yabby Creek"}],
        "buildings": ["Council Chamber", "Redgum Library", "Yabby Creek Aquatic Centre", "Function Room A"],
        "mailboxes": ["council@redgumshire.example", "rates@redgumshire.example", "records@redgumshire.example", "mayor@redgumshire.example", "planning@redgumshire.example"],
        "lines": [("Customer service", "1300 975 707"), ("After-hours emergency", "1800 975 710")],
        "roles": ["General Manager", "Mayor", "Director of Infrastructure", "Governance Officer", "Rates Officer"],
        "products": [("DA-2026/0412", "Development application"), ("RL-2027", "Rating category residential"), ("WST-GRN", "Green waste service")],
        "projects": [("PRJ-BRG-3", "Yabby Creek bridge renewal"), ("PRJ-POOL", "Aquatic Centre heating")],
        "committees": ["Audit, Risk and Improvement Committee", "Access and Inclusion Advisory Committee", "Reconciliation Advisory Committee"],
        "initiatives": ["Seniors Festival", "Youth Week", "Harmony Week picnic", "Pride flag raising", "Mental Health Month"],
        "refs": {"request": "CRM-\\d{7}", "da": "DA-2026/\\d{4}", "minute": "Min. \\d{3}/26"},
        "staff_id": ("RSC", 5), "customer_id": ("ASS-", 7),
        "public_dates": [("Ordinary Council meeting", "27/10/2026"), ("Ordinary Council meeting", "24/11/2026"), ("Rates instalment due", "30/11/2026"),
                         ("Seniors Festival", "10/02/2027"), ("Australia Day ceremony", "26/01/2027")],
    },
    {
        "key": "courier", "name": "Orrin Street Couriers", "forms": ["Orrin Street Couriers", "Orrin Street", "OSC"],
        "domain": "orrincouriers.example", "sector": "logistics",
        "sites": [{"name": "Gullwing", "forms": ["Gullwing depot", "Gullwing Depot"], "reception": "(07) 5550 8810", "address": "Bay 12, 300 Freightway, Gullwing"}],
        "buildings": ["Dock 7", "the Sorting Floor", "the Yard Office"],
        "mailboxes": ["dispatch@orrincouriers.example", "fleet@orrincouriers.example", "hr@orrincouriers.example", "claims@orrincouriers.example"],
        "lines": [("Dispatch", "1300 975 709")],
        "roles": ["Fleet Manager", "Dispatch Supervisor", "Night Shift Lead", "Yard Controller"],
        "products": [("SVC-SAMEDAY", "Same-day metro"), ("SVC-COLD", "Cold chain"), ("SVC-PAL", "Palletised freight")],
        "projects": [("PRJ-EV-12", "Electric van trial")],
        "committees": ["Safety Committee", "Consultative Committee"],
        "initiatives": ["Fatigue management campaign", "Enterprise Agreement 2027 negotiations", "R U OK? Day barbecue"],
        "refs": {"consignment": "CON\\d{9}", "job": "RUN-\\d{5}", "vehicle": "fleet \\d{3}", "claim": "CLM-\\d{6}"},
        "staff_id": ("DRV", 4), "customer_id": ("ACC", 6),
        "public_dates": [("Enterprise Agreement vote", "11/11/2026"), ("Peak season starts", "01/12/2026"), ("Depot stocktake", "03/01/2027")],
    },
    {
        "key": "theatre", "name": "Pennyroyal Theatre Company", "forms": ["Pennyroyal Theatre Company", "Pennyroyal", "the company"],
        "domain": "pennyroyal.example", "sector": "arts",
        "sites": [{"name": "Lantern Street", "forms": ["the Lantern Street theatre", "Lantern Street Playhouse"], "reception": "(03) 5550 9910", "address": "61 Lantern Street, Lantern Street"}],
        "buildings": ["the Green Room", "Studio B", "the Fly Tower", "the Scene Dock"],
        "mailboxes": ["boxoffice@pennyroyal.example", "stagedoor@pennyroyal.example", "casting@pennyroyal.example", "donate@pennyroyal.example"],
        "lines": [("Box office", "1300 975 711")],
        "roles": ["Artistic Director", "Stage Manager", "Company Manager", "Head of Wardrobe"],
        "products": [("PROD-2027-1", "The Salt Widow"), ("PROD-2027-2", "A Comedy of Tides"), ("SUB-GOLD", "Gold subscription")],
        "projects": [("PRJ-FLY", "Fly tower counterweight upgrade")],
        "committees": ["Board of Directors", "Access Advisory Panel"],
        "initiatives": ["Relaxed performance program", "Auslan-interpreted Fridays", "Pride Night gala"],
        "refs": {"booking": "BK-\\d{7}", "cue": "cue \\d{3}", "po": "PRT-PO-\\d{4}"},
        "staff_id": ("PT-", 4), "customer_id": ("PATRON-", 6),
        "public_dates": [("Opening night, The Salt Widow", "19/02/2027"), ("Season launch", "05/11/2026"), ("Board meeting", "16/11/2026")],
    },
    {
        "key": "mining", "name": "Halcyon Ridge Mining Services", "forms": ["Halcyon Ridge Mining Services", "Halcyon Ridge", "HRMS"],
        "domain": "halcyonridge.example", "sector": "mining services",
        "sites": [{"name": "Dry Gully", "forms": ["Dry Gully camp", "Dry Gully Village"], "reception": "(08) 5550 1120", "address": "Camp Office, Haul Road 2, Dry Gully"},
                  {"name": "Port Carrick", "forms": ["Port Carrick office"], "reception": "(08) 7010 1121", "address": "Suite 4, 12 Wharf Street, Port Carrick"}],
        "buildings": ["the Wet Mess", "Dry Mess", "Workshop 2", "the Crib Room"],
        "mailboxes": ["rostering@halcyonridge.example", "camp@halcyonridge.example", "hse@halcyonridge.example", "payroll@halcyonridge.example"],
        "lines": [("Camp reception", "1800 975 711"), ("HSE hotline", "1800 975 708")],
        "roles": ["Site Senior Executive", "Camp Manager", "HSE Superintendent", "Shift Supervisor"],
        "products": [("SWING-8/6", "Eight-on six-off swing"), ("SWING-2/1", "Two-and-one swing")],
        "projects": [("PRJ-PIT-4", "Pit 4 dewatering"), ("PRJ-CAMP-B", "Camp B expansion")],
        "committees": ["Site Safety and Health Committee", "Indigenous Participation Committee"],
        "initiatives": ["Fatigue and fitness-for-work program", "Mates in Mining", "NAIDOC Week on site", "Drug and alcohol testing program"],
        "refs": {"permit": "PTW-\\d{6}", "hazard": "HAZ-\\d{5}", "swing": "swing \\d{2}"},
        "staff_id": ("HR", 6), "customer_id": ("CONTR-", 5),
        "public_dates": [("Site shutdown", "20/12/2026"), ("Safety reset day", "04/11/2026"), ("NAIDOC Week on site", "05/07/2027")],
    },
    {
        "key": "credit", "name": "Blue Gum Mutual Credit Union", "forms": ["Blue Gum Mutual Credit Union", "Blue Gum Mutual", "the credit union"],
        "domain": "bluegummutual.example", "sector": "finance",
        "sites": [{"name": "Kingfisher Point", "forms": ["Kingfisher Point branch"], "reception": "(03) 5550 1230", "address": "5 Esplanade, Kingfisher Point"},
                  {"name": "Dunmore", "forms": ["Dunmore branch"], "reception": "(03) 7010 1231", "address": "112 High Street, Dunmore"}],
        "buildings": ["the Contact Centre", "the Vault Room", "Branch Floor"],
        "mailboxes": ["members@bluegummutual.example", "cards@bluegummutual.example", "fraud@bluegummutual.example", "complaints@bluegummutual.example"],
        "lines": [("Member services", "1300 975 710"), ("Lost or stolen cards", "1800 975 709")],
        "roles": ["Branch Manager", "Lending Officer", "Fraud Analyst", "Member Services Consultant"],
        "products": [("ESV-01", "Everyday saver"), ("HL-FIX3", "Three-year fixed home loan"), ("TD-180", "180-day term deposit")],
        "projects": [("PRJ-CORE-27", "Core banking migration")],
        "committees": ["Board Risk Committee", "Member Advisory Panel"],
        "initiatives": ["Financial literacy for seniors", "Scam Awareness Week", "Hardship assistance program"],
        "refs": {"case": "FR-\\d{7}", "application": "APP-\\d{6}", "dispute": "DSP-\\d{5}"},
        "staff_id": ("BG", 5), "customer_id": ("MEM", 7),
        "public_dates": [("Annual general meeting", "19/11/2026"), ("Scam Awareness Week", "24/08/2026"), ("Branch closed (Labour Day)", "08/03/2027")],
    },
]

# Harbourline Institute profile values (copied from eval/profiles/harbourline.json; institutional, never personal)
HARBOURLINE = {
    "key": "hl", "name": "Harbourline Institute", "forms": ["Harbourline Institute", "Harbourline", "the Institute"],
    "domain": "harbourline.example", "sector": "training and research", "abn": "96 444 148 279", "abn_forms": ["96 444 148 279", "96444148279"],
    "sites": [
        {"name": "Saltmere", "forms": ["Saltmere campus", "Saltmere Campus", "Harbourline Saltmere"], "reception": "(02) 5550 0141", "address": "1 Lighthouse Road, Saltmere"},
        {"name": "Kestrel Bay", "forms": ["Kestrel Bay station", "Kestrel Bay Research Station", "Harbourline Kestrel Bay"], "reception": "(02) 5550 0142", "address": "88 Spit Road, Kestrel Bay"},
        {"name": "Wyndhollow", "forms": ["Wyndhollow centre", "Wyndhollow Training Centre", "Harbourline Wyndhollow"], "reception": "(07) 5550 0143", "address": "40 Mill Race Lane, Wyndhollow"},
        {"name": "Greyfen Quay", "forms": ["Greyfen Quay office", "Greyfen Quay City Office", "Harbourline Greyfen Quay"], "reception": "(08) 7010 0144", "address": "Level 6, 210 Quayside Parade, Greyfen Quay"},
        {"name": "Larkspur Ridge", "forms": ["Larkspur Ridge field station", "Larkspur Ridge Field Station"], "reception": "(07) 5550 0145", "address": "Larkspur Ridge Road, Larkspur Ridge"},
    ],
    "buildings": ["Tidewater Building", "Lantern Hall", "Anchor Labs", "Driftwood Library", "Lovelace Lecture Theatre", "Breakwater Workshop", "Salt Store Gallery"],
    "mailboxes": ["enquiries@harbourline.example", "admissions@harbourline.example", "servicedesk@harbourline.example", "privacy@harbourline.example",
                  "payroll@harbourline.example", "people@harbourline.example", "research.office@harbourline.example", "ethics@harbourline.example",
                  "safety@harbourline.example", "facilities@harbourline.example", "wellbeing@harbourline.example", "library@harbourline.example",
                  "procurement@harbourline.example", "media@harbourline.example", "accessibility@harbourline.example", "security@harbourline.example"],
    "lines": [("General enquiries", "1300 975 707"), ("IT service desk", "1300 975 709"), ("After-hours wellbeing line", "1800 975 711")],
    "roles": ["Chief Executive", "Privacy Officer", "Director of Research", "Head of People and Culture", "Research Integrity Officer", "Site Manager",
              "Accessibility Adviser", "Wellbeing Coordinator", "Chair of the Ethics Committee", "Facilities Manager", "Chief Information Officer", "Safety Coordinator"],
    "products": [("HLP-2041", "Graduate Certificate in Coastal Data Science"), ("HLP-1187", "Certificate in Laboratory Operations"),
                 ("HLP-3302", "Diploma of Marine Engineering Practice"), ("HLP-1450", "Short Course in Workplace First Aid"),
                 ("HLP-2216", "Certificate in Applied Project Management"), ("HLP-1903", "Foundation Skills in Digital Literacy"),
                 ("HLP-1460", "Mental Health First Aid Refresher"), ("HLP-1475", "Auslan for Beginners")],
    "projects": [("RP-2026-014", "Seagrass Recovery Monitoring"), ("RP-2026-031", "Low-cost Tide Sensors"), ("RP-2025-118", "Fatigue in Shift Workers")],
    "committees": ["Diversity and Inclusion Committee", "Health and Safety Committee", "Reconciliation Working Group", "Staff Pride Network", "Faith and Wellbeing Group"],
    "initiatives": ["Workplace Health and Safety Audit", "Mental Health Week", "Harmony Day lunch", "Ramadan iftar evening", "Wear It Purple Day",
                    "Accessibility Action Plan", "Enterprise Agreement consultation", "Respectful Relationships workshop"],
    "refs": {"service request": "SR-\\d{6}", "change request": "CHG-\\d{5}", "purchase order": "PO-[56]\\d{7}", "invoice": "INV-HL-\\d{5}",
             "asset tag": "HLA-\\d{5}", "ethics approval": "HREC-20\\d{2}-\\d{3}", "room booking": "RB-\\d{6}", "module": "HLM\\d{3}[A-Z]"},
    "staff_id": ("HL", 5), "customer_id": ("", 8),  # learner number [56]\d{7}
    "public_dates": [("Open Day", "22/08/2026"), ("Research Symposium", "17/09/2026"), ("Council meeting", "15/10/2026"), ("Council meeting", "19/11/2026"),
                     ("Graduation ceremony", "11/12/2026"), ("End-of-year closure starts", "24/12/2026"), ("Sites reopen", "05/01/2027"), ("Term 1 starts", "08/02/2027")],
}

# Residential streets and suburbs for personal addresses (invented)
STREETS = ["Tern Street", "Wallaby Way", "Cormorant Close", "Old Quarry Road", "Mulberry Lane", "Fennel Court", "Heron Rise", "Ironstone Drive",
           "Pardalote Place", "Kiln Street", "Saltgrass Avenue", "Breakwater Crescent", "Marlin Parade", "Lyrebird Terrace", "Pipeclay Road"]
SUBURBS = [("Marrowgate", "2611"), ("Pelican Flats", "2287"), ("Thistlewood", "3094"), ("Cobb's Crossing", "2550"), ("Ironbark Heights", "3802"),
           ("Wattlebend", "3171"), ("Hollow Creek", "6230"), ("Yabby Creek", "2794"), ("Gullwing", "4215"), ("Dry Gully", "6753"),
           ("Kingfisher Point", "3225"), ("Dunmore", "3384"), ("Foundry Park", "3012"), ("Port Carrick", "6721"), ("Lantern Street", "3056")]
HL_SUBURBS = [("Saltmere", "2318"), ("Kestrel Bay", "2316"), ("Wyndhollow", "4573"), ("Greyfen Quay", "6005"), ("Larkspur Ridge", "4885")]
STATES = {"2": "NSW", "3": "VIC", "4": "QLD", "6": "WA"}

# ----------------------------------------------------------------------------------------------------------------------
# Identifier makers and checkers (independent implementations)
# ----------------------------------------------------------------------------------------------------------------------
TFN_W = (1, 4, 3, 7, 5, 8, 6, 9, 10)
MED_W = (1, 3, 7, 9, 1, 3, 7, 9)
ABN_W = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def tfn_ok(digits: str) -> bool:
    return len(digits) == 9 and digits.isdigit() and sum(int(d) * w for d, w in zip(digits, TFN_W)) % 11 == 0


def make_tfn(rng: random.Random) -> str:
    while True:
        d8 = [rng.randint(0, 9) for _ in range(8)]
        if d8[0] == 0:
            continue
        chk = sum(d * w for d, w in zip(d8, TFN_W[:8])) % 11  # 10*d9 == -s (mod 11) -> d9 == s (mod 11)
        if chk <= 9:
            s = "".join(map(str, d8)) + str(chk)
            assert tfn_ok(s)
            return s


def medicare_ok(digits: str) -> bool:
    if len(digits) != 10 or not digits.isdigit() or digits[0] not in "23456" or digits[9] == "0":
        return False
    return sum(int(d) * w for d, w in zip(digits[:8], MED_W)) % 10 == int(digits[8])


def make_medicare(rng: random.Random) -> str:
    d = [rng.randint(2, 6)] + [rng.randint(0, 9) for _ in range(7)]
    chk = sum(x * w for x, w in zip(d, MED_W)) % 10
    s = "".join(map(str, d)) + str(chk) + str(rng.randint(1, 9))
    assert medicare_ok(s)
    return s


def abn_ok(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or digits[0] == "0":
        return False
    d = [int(x) for x in digits]
    d[0] -= 1
    return sum(x * w for x, w in zip(d, ABN_W)) % 89 == 0


def make_abn(rng: random.Random) -> str:
    while True:
        tail = [rng.randint(0, 9) for _ in range(9)]
        pairs = [(a, b) for a in range(1, 10) for b in range(10)]
        rng.shuffle(pairs)
        for a, b in pairs:
            if (a, b) == (6, 1):  # "61..." would read as an international AU phone prefix to a text scan
                continue
            s = "".join(map(str, [a, b] + tail))
            if abn_ok(s):
                return s


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 12:
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


# Payment providers' published test card numbers (never issued to anyone)
TEST_CARDS = ["4111111111111111", "4012888888881881", "4242424242424242", "4000056655665556", "5555555555554444", "5105105105105100",
              "5200828282828210", "2223003122003222", "378282246310005", "371449635398431", "378734493671000", "6011111111111117",
              "6011000990139424", "3566002020360505", "3530111333300000", "30569309025904", "38520000023237", "6200000000000005"]
TEST_CARDS = [c for c in TEST_CARDS if luhn_ok(c)]


def iban_num(s: str) -> int:
    return int("".join(str(ord(ch) - 55) if ch.isalpha() else ch for ch in s))


def iban_ok(iban: str) -> bool:
    s = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False
    return iban_num(s[4:] + s[:4]) % 97 == 1


def make_iban(rng: random.Random) -> str:
    kind = rng.choice(["GB", "DE", "NL", "FR"])
    if kind == "GB":
        bban = "XQRB" + "".join(str(rng.randint(0, 9)) for _ in range(14))
    elif kind == "DE":
        bban = "99977" + "".join(str(rng.randint(0, 9)) for _ in range(13))
    elif kind == "NL":
        bban = "ZZTB" + "".join(str(rng.randint(0, 9)) for _ in range(10))
    else:
        bban = "99999" + "".join(str(rng.randint(0, 9)) for _ in range(18))
    chk = 98 - iban_num(bban + kind + "00") % 97
    iban = f"{kind}{chk:02d}{bban}"
    assert iban_ok(iban)
    return iban


def make_ssn(rng: random.Random) -> str:
    return f"987-65-43{rng.randint(20, 29)}"


def ssn_ok(s: str) -> bool:
    return re.fullmatch(r"987-65-432\d", s) is not None


def make_nino(rng: random.Random) -> str:
    return f"QQ {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.randint(10, 99)} {rng.choice('ABCD')}"


def nino_ok(s: str) -> bool:
    return re.fullmatch(r"QQ ?\d{2} ?\d{2} ?\d{2} ?[A-D]", s) is not None


# Fiction-range phone numbers
ACMA_MOBILES = ["0491 570 006", "0491 570 156", "0491 570 157", "0491 570 158", "0491 570 159", "0491 570 110", "0491 570 313",
                "0491 570 737", "0491 571 266", "0491 571 491", "0491 571 804", "0491 572 549", "0491 572 665", "0491 573 087",
                "0491 573 770", "0491 574 118", "0491 574 632", "0491 575 254", "0491 575 789", "0491 576 398", "0491 576 801",
                "0491 577 426", "0491 577 644", "0491 578 957", "0491 579 212", "0491 579 760", "0491 578 888", "0491 579 600"]
DIGIT_WORDS = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine"}
WORD_DIGITS = {v: k for k, v in DIGIT_WORDS.items()} | {"oh": "0", "double": None}


def au_mobile_fiction(digits: str) -> bool:
    """digits: normalised national form 04xxxxxxxx."""
    return re.fullmatch(r"049157\d{4}", digits) is not None


def au_landline_fiction(digits: str) -> bool:
    return re.fullmatch(r"0[2378](5550|7010)\d{4}", digits) is not None


def au_1300_fiction(digits: str) -> bool:
    return re.fullmatch(r"1[38]00975(707|708|709|710|711)", digits) is not None


def uk_fiction(digits: str) -> bool:
    return re.fullmatch(r"07700900\d{3}", digits) is not None


def us_fiction(digits: str) -> bool:
    return re.fullmatch(r"\d{3}55501\d{2}", digits) is not None


RESERVED_DOMAIN = re.compile(r"(?:^|\.)example\.(?:com|net|org)$|\.example$", re.I)


def email_domain_ok(addr: str) -> bool:
    dom = addr.rsplit("@", 1)[-1].rstrip(".")
    return RESERVED_DOMAIN.search(dom) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Secrets: random strings with no vendor prefix
# ----------------------------------------------------------------------------------------------------------------------
def make_secret(rng: random.Random, kind: str) -> str:
    alnum = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    hexd = "0123456789abcdef"
    if kind == "password":
        words = ["Kestrel", "Tide", "Mangrove", "Saffron", "Quarry", "Lantern", "Gully", "Brindle", "Corvid", "Pennyroyal", "Halcyon", "Thistle"]
        return f"{rng.choice(words)}{rng.choice('!@#$%&*')}{rng.randint(10, 9999)}{rng.choice(['x', 'Q', 'z', '_', '#', ''])}{rng.choice(words).lower()}"
    if kind == "pin":
        return f"{rng.randint(0, 9999):04d}" if rng.random() < 0.7 else f"{rng.randint(0, 999999):06d}"
    if kind == "cvv":
        return f"{rng.randint(0, 999):03d}"
    if kind == "hex32":
        return "".join(rng.choice(hexd) for _ in range(32))
    if kind == "hex40":
        return "".join(rng.choice(hexd) for _ in range(40))
    if kind == "token":
        return "".join(rng.choice(alnum + "_-") for _ in range(rng.choice([24, 32, 40, 48])))
    if kind == "b64":
        return "".join(rng.choice(alnum + "+/") for _ in range(43)) + "="
    if kind == "uuid":
        h = "".join(rng.choice(hexd) for _ in range(32))
        return f"{h[:8]}-{h[8:12]}-4{h[13:16]}-a{h[17:20]}-{h[20:]}"
    raise ValueError(kind)
