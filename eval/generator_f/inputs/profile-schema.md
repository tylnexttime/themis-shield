# Deployment profile schema

A profile describes ONE fictional organisation's institutional (never personal) terms, so a gate can build an allow-list. Write your own organisation; do not copy any real one. JSON object with these keys:

- `_about` (str): one line: what this file is
- `organisation` (str): the organisation's name
- `organisation_forms` (list): other written forms (short name, acronym)
- `description` (str): one paragraph
- `business_identifier` (dict): {scheme (e.g. ABN), value (checksum-valid, invented), forms (written variants)}
- `email_domain` (str): a reserved domain, e.g. something.example
- `sites` (list): site/campus names
- `site_note` (str): note
- `buildings` (list): building names
- `role_mailboxes` (list): role addresses like enquiries@<domain>
- `main_lines` (list): main phone numbers (fiction ranges only)
- `reception_numbers` (list): reception numbers (fiction ranges only)
- `role_titles` (list): job/role titles used without names
- `programmes` (list): programme/service names
- `research_projects` (list): project names
- `committees` (list): committee/network names
- `initiatives` (list): initiatives and events
- `committee_note` (str): note
- `code_formats` (dict): {name: pattern} for programme/module/project codes
- `reference_number_formats` (dict): {name: pattern} for tickets, orders, invoices, asset tags etc.
- `public_dates` (list): public dates (holidays, events)
- `personal_identifier_formats_issued` (dict): {name: pattern} for identifiers the organisation issues to PEOPLE (staff number, member number...); these are personal, not allow-listed


---

## Split F adaptation (appended 11/11/2026)

Entry shapes (the `convert_profile.py` bridge accepts both bare strings and objects): `sites` = objects
`{name, forms[], address, reception}`; `programmes` and `research_projects` = objects `{code, title}`; `public_dates` =
objects `{event, date}`; `main_lines` and `reception_numbers` = plain strings. Pattern notation for `code_formats`,
`reference_number_formats` and `personal_identifier_formats_issued`: `#` is one digit, `@` is one capital letter,
every other character is literal. The reference formats are keyed by hard-negative kind (ticket_number, case_number,
reference_number, order_number, invoice_number, asset_id, cost_centre, form_number, provider_number, client_account,
software_version, build_number, payroll_group, ...). Every `personal_identifier_formats_issued` pattern starts with the
organisation's own short form as a literal prefix.