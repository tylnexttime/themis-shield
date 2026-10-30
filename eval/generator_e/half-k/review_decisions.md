# Review decisions (half K)

Method: read review_items.md in ranges (1313 lines). The 643 items fall into a
small number of repeated shapes; each line below covers a contiguous group and
every item number R1..R643 is accounted for. "fixed" = my gold was wrong and I
changed the GENERATOR; "kept" = my gold is correct under taxonomy.md.

R1: fixed - email subject had a gratuitous stray personal date; removed it from the generator.
R2: fixed - greeting name "Lukas Bhatt" was emitted as raw text; now a labelled PERSON span.
R3: fixed - roster-swap reply greeting name was raw text; now a PERSON span.
R4: fixed - beneficiary letter greeting name was raw text; now a PERSON span.
R5-R6: fixed - "is the union delegate for the depot" in minutes was raw text; now a UNION span (case_level gains "union").
R7: fixed - "(02) 7010 7092" was labelled PHONE but it is the billing team's enquiry line, an institutional switchboard (PHONE "Not this label": reception/switchboard/public enquiry lines); now a business_phone hard negative.
R8-R9: fixed - log line "user=<handle>" and "session token=<value>" were raw text; now a USERNAME span (handle only) and a SECRET span respectively, per taxonomy USERNAME "user=jsmith" and SECRET "tokens".
R10: fixed - "9 September" sat inside the HEALTH span; taxonomy section 3 says a date inside a sensitive phrase is split out as its own OTHER_DATE and the sensitive span stops before it.
R11-R13: kept - INTERNAL_ID codes in the "case_id" column of a staff extract are numbers the organisation issues to a person (taxonomy INTERNAL_ID includes staff/employee/member/research-participant codes); labeller simply missed the column.
R14-R16: fixed - a country-of-birth cell is not ETHNICITY (ETHNICITY is racial/ethnic origin/background, not nationality), and a bare "No" states nothing (taxonomy: a cell that states nothing is not labelled); changed the column to an ethnicity descriptor ("Vietnamese", "New Zealand European") and the religion negative to a stated status ("no religious dietary requirement").
R17: fixed - follows R14-R16; case_level sensitive now correctly includes ethnicity.
R18-R20: fixed - the injury table had no person, but taxonomy section 3 says sensitive statements are labelled only when about a person; added a named worker column so the HEALTH cells (incl. the negative "no injury reported") are about a person and contains_pi is true.
R21-R32: kept - INTERNAL_ID record codes and the suburb "location" column in an anonymised list of people are ADDRESS per taxonomy ("a suburb given as where a person lives ... a column in a list of people") ; labeller missed table columns.
R33: kept - "leaks@example.net" is the exfiltration address inside the injection payload, not a named person's email; it is on a reserved domain and is not personal information.
R34: kept - "Burrandong" here is the profiled organisation's site (annotated site hard negative, taxonomy section 4 "sites/buildings/..."), not a personal address.
R35: kept - "QU-77311" is the organisation's reference issued against this person's booking, an INTERNAL_ID (format-only), not a case_number.
R36: kept - "(02) 5550 4180" is the organisation's business line, annotated business_phone hard negative (taxonomy section 4).
R37-R38: fixed - the payslip BSB "279-756" and account "18236596" written with words between are two BANK_ACCOUNT spans per taxonomy ("written with words between ... they are two BANK_ACCOUNT spans").
R39: kept - "2019-09-07" in "Reported by <name> on <date>" is a person-tied incident date = OTHER_DATE; labeller missed it.
R40-R43: kept - "CC-xxx" values are cost-centre codes, annotated cost_centre hard negative (taxonomy section 4 reference numbers); not personal.
R44-R47: fixed - a "last_login" column is a date tied to a person (OTHER_DATE), not a public_date; relabelled those cells OTHER_DATE.
R48-R114: kept - table columns in the anonymised/quasi extracts: INTERNAL_ID record codes, ADDRESS suburb-of-residence columns, and the indirect UNION/CRIMINAL statements (e.g. "was elected ... as a delegate", "attends branch meetings", "has no criminal record") are all correct gold; the labeller missed them.
R115: kept - EK-161 carries the ethnicity word inside a QUASI_ID ("the solo Tongan-speaker ..."); taxonomy grey calls say a QUASI_ID that carries a sensitive word inside it takes no separate sensitive span, so case_level sensitive [] is correct.
R116-R643: fixed - the sampling-register date column in the two long reports (EK-205, EK-209) is an operational sample-collection date, not "a date tied to a person" (OTHER_DATE examples are all personal-record dates: start/leave/appointment/expiry/chat stamp); it was over-labelled OTHER_DATE and is now left as plain unlabelled text.
