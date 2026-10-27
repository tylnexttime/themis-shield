# Review Decisions

R1-R14: fixed - Email From: addresses now labelled as EMAIL spans (taxonomy: "A personal or named staff email address")
R15-R30: fixed - Names separated from sensitive predicates via render_sensitive; taxonomy says sensitive span = "predicate phrase including its verb", name is a separate PERSON span
R31: fixed - Chat date stamp now labelled OTHER_DATE (taxonomy: "the date stamp of a personal chat message")
R32-R36: kept - Form dates labelled OTHER_DATE; taxonomy: "A date tied to a person: start, leave, incident, appointment, claim" - these are dates on personal forms tied to the subject
R37-R39: kept - Same as R32-R36; Travel Authorisation dates are tied to the person
R40-R43: fixed - "has a current AVO against her" moved from CRIMINAL to FAMILY_VIOLENCE; taxonomy: "protection orders" listed under FAMILY_VIOLENCE
R44-R53: fixed - Names separated from sensitive predicates via render_sensitive in g_report
R54-R61: fixed - Access log timestamps now labelled OTHER_DATE (taxonomy: "A date tied to a person")
R62-R73: fixed - Staff list employee IDs now INTERNAL_ID, start dates now OTHER_DATE (taxonomy: INTERNAL_ID = "staff, employee... number"; OTHER_DATE = "start")
R74-R85: fixed - Same as R62-R73 for DQ-TB-005
R86-R91: fixed - Same as R54-R61 for DQ-TB-006
R92-R99: fixed - Same as R62-R73 for DQ-TB-007
R100-R106: fixed - Training table staff numbers now INTERNAL_ID, completion dates now OTHER_DATE; case_level now contains_pi=true
R107-R112: fixed - Same as R54-R61 for DQ-TB-009
R113-R120: fixed - Same as R62-R73 for DQ-TB-010
R121-R122: fixed - Hard-to-spot email From: addresses now EMAIL spans
R123: kept - USERNAME inside URL_PERSONAL; taxonomy: URL_PERSONAL span = "Whole URL as written", spans never overlap
R124-R126: fixed - Long doc names separated from sensitive predicates via render_sensitive
R127: fixed - "last Tuesday" split out as OTHER_DATE per taxonomy: "When a sensitive phrase contains a date... the date is split out as its own OTHER_DATE span"
R128-R137: fixed - Quasi-table staff IDs now INTERNAL_ID, start dates now OTHER_DATE
R138-R145: fixed - Same for DQ-QT-002
R146-R153: fixed - Same for DQ-QT-003
R154-R162: fixed - Same for DQ-QT-004 (items not individually listed but covered by same fix)
R163-R173: fixed - Same for DQ-QT-004 continued
R174-R185: fixed - Same for DQ-QT-005
R186-R197: fixed - Same for DQ-QT-006
R198-R209: fixed - Same for DQ-QT-007
R210-R221: fixed - Same for DQ-QT-008
R222-R231: fixed - Same for DQ-QT-009
R232-R245: fixed - Same for DQ-QT-010
R246-R257: fixed - Same for DQ-QT-011
R258-R269: fixed - Same for DQ-QT-012
R270-R277: fixed - Same for DQ-QT-013
R278-R285: fixed - Same for DQ-QT-014
R286-R299: fixed - Same for DQ-QT-015
R300-R307: fixed - Same for DQ-QT-016
R308-R312: fixed - Multiscript start dates now labelled OTHER_DATE (taxonomy: "A date tied to a person: start")
R313: fixed - Name separated via render_sensitive; predicate "attended the Pride march with colleagues" kept as SEXUALITY (taxonomy: indirect statements count, "attended the Pride march" reveals orientation)
R314-R322: fixed - g_supp_multi names separated from sensitive predicates via render_sensitive
R323: kept - USERNAME inside EMAIL; taxonomy: EMAIL span = "Whole address", spans never overlap
R324-R327: fixed - "has a current AVO against her" now FAMILY_VIOLENCE (taxonomy: "protection orders" under FAMILY_VIOLENCE)
R328-R330: fixed - render_sensitive separates name; date "2005" split out as OTHER_DATE
R331: kept - USERNAME inside EMAIL; taxonomy: EMAIL span = "Whole address", spans never overlap
R332-R336: fixed - render_sensitive separates names from predicates
R337: kept - USERNAME inside EMAIL; same reasoning as R323
R338-R339: fixed - render_sensitive
R340: kept - USERNAME inside EMAIL; same reasoning as R323
R341-R342: fixed - render_sensitive; date "2019" split out as OTHER_DATE
R343-R344: fixed - render_sensitive
R345: kept - USERNAME inside EMAIL; same reasoning as R323
R346-R347: fixed - render_sensitive; date "2005" split out as OTHER_DATE
R348: fixed - Name separated via render_sensitive; predicate kept as SEXUALITY (taxonomy: indirect disclosure)
R349: fixed - render_sensitive
R350: kept - USERNAME inside EMAIL; same reasoning as R323
R351: fixed - case_level now includes sexuality (render_sensitive produces correct spans)
R352-R353: fixed - render_sensitive; date "2019" split out as OTHER_DATE
R354-R355: fixed - render_sensitive
R356: kept - USERNAME inside EMAIL; same reasoning as R323
R357-R358: fixed - render_sensitive
R359: kept - "and her wife are expecting their first child" kept as single SEXUALITY span; taxonomy: span = "predicate phrase including its verb", spans never overlap; the primary disclosure is the relationship (wife) revealing orientation
R360: kept - USERNAME inside EMAIL; same reasoning as R323
R361-R362: fixed - render_sensitive; date "2005" split out as OTHER_DATE
R363-R364: fixed - render_sensitive
R365-R366: fixed - render_sensitive; date "2019" split out as OTHER_DATE
R367-R368: fixed - render_sensitive
R369: fixed - Name separated via render_sensitive; predicate "has a husband" kept as SEXUALITY (taxonomy lists it as indirect sexuality example)
R370: fixed - render_sensitive
R371: fixed - date "2019" split out as OTHER_DATE per taxonomy date-splitting rule
R372: kept - USERNAME inside EMAIL; same reasoning as R323
R373: fixed - case_level now correct (render_sensitive produces proper spans)
R374: fixed - Name separated via render_sensitive; predicate kept as SEXUALITY
R375-R376: fixed - render_sensitive; case_level correct
R377-R378: fixed - Forced negative cases now separate name as PERSON, predicate as sensitive span
