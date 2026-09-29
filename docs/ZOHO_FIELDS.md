# Zoho CRM field reference (Deals / Opportunity module = post-qualification)

Provided by the OBL team as context for STARS. These are **display labels** with
meanings; Zoho **API names** (what COQL/queries use) are usually the label with
spaces/hyphens → underscores, but must be confirmed via `discover_zoho_fields`
(the friendly group_by keys below carry the best-guess API name, overridable by env).

The Deals module holds **qualified leads (opportunities)**. A lead is created in
Leads, and on qualification it converts to a Deal (the Lead stays, flagged Converted).

## Fields that matter most for analysis

| What | Display label | Meaning | Likely API name / how to use |
|---|---|---|---|
| Salesperson (FLS) | **Sales Person Email ID** | Email of the sales person handling the deal (always populated) | `group_by='salesperson'` → `Sales_Person_Email_ID` |
| Salesperson name | Sales Person Name | Name of the sales person | — |
| Salesperson emp id | Sales Person Emp ID | Emp id of the sales person (in use) | — |
| New/Active/Closed | **Stage Category** | The New / Active / Closed section shown in the sales app | `group_by='status'` → `Stage_Category` |
| Pipeline stage | **Stage** | Deal stage: Qualification, Spoken to Customer, Scheduled a visit, Samples shared, Quotation Shared, Visited Store, Closed Won, Closed Lost, Junk Lead, … | `stage=` filter / `group_by='stage'` → `Stage` |
| Lead status | Lead Status | Status of the lead | — |
| Source | Lead Source | Where the lead came from (Website, Meta, …) | `source=` / `group_by='source'` → `Lead_Source` |
| Sub-source | **Sub-source** | Lead sub-source (note: hyphen in label; Leads module uses `Sub_Source`) | `group_by='sub_source'` — confirm API name for Deals |
| Dealer (CP) | Assigned CP Name | Name of the assigned channel partner/dealer (from CP code) | `group_by='dealer'` → `Assigned_CP_Name` |
| Zone | Zone | North/South/East/West | `group_by='zone'` → `Zone` |
| Branch/region | Branch Area | Area/region of the assigned salesperson/branch manager | `group_by='branch'` → `Branch_Area` |
| Category | Category | Adhesive or non-Adhesive | `Category` |
| Expected amount | Amount | Amount expected (Currency) | `Amount` (SUM — see note) |
| Won amount | Won Amount | Adhesive sale amount closed (Currency) | `Won_Amount` |
| Volume | Volume (In Sq. Mtr.) | Volume won/closed in sq mtr | `Volume_In_Sq_Mtr` |
| Created | Created Time | Record creation time | `date_field='created'` → `Created_Time` |
| Modified | Modified Time | Record last-modified time (use for "stale/untouched") | `date_field='modified'` → `Modified_Time` |
| Stage updated | Current Stage Update Date/Time | When the stage was last updated | — |
| Lead→Opp | Lead Conversion Time | When the lead converted to opportunity | — |
| Closing | Closing Date | Deal won/lost date | `date_field='closing'` → `Closing_Date` |
| Won date | Closed Won Date | Deal won date | — |
| Closed by | Closed By / Closed By FLS/CP | Who marked it closed won (FLS/BH/CP) | — |
| Next action | Next Follow up | Next date to reach out | — |
| Time to contact | Days Difference | Days between creation and first sales-person contact/update | — |
| Website journey | Pages Visited | Last ~10 pages of the user journey; last page ≈ where the lead was filled | — |
| Requirement | Tiles Requirement for / Tile Category / Tile Requirement in Area (Sq Mtr / Sq ft) | What/where the customer needs tiles | — |
| Geography | State, City, Area in City, Zip Code (pincode) | Customer location | — |
| Contact | Contact Name / Opportunity Name / Mobile / Phone / Email | Customer identity & contact | — |

## Hierarchy (people above the FLS)

BM (Branch Manager), BH / PCH (Branch Head — code, email, phone), NH (National
Head), ZH (Zonal Head), ZM (Zonal Manager, incl. email), Opportunity Owner Name
(lead owner).

## Call-centre (L2) fields

`L2 Owner`, `L2 Call Status`, `L2 Status`, `L2 First Attempt Date/Time`,
`L2 Purchase Value`, `L2 Purchase/Invoice Date`, `L2 Remarks`, `L2 Agent Number` —
second-touch call-centre tracking.

## Stage-change remark fields

Remarks the sales team add in the app when changing stage: `Remarks spoken to
customer`, `Remarks of Scheduled a Visit`, `Remarks of Sample shared`, `Remarks of
quotation shared`, `Remarks of visit store`, `Remarks of Junk Lead`,
`Junk Lead Reason Remarks B2B`, `Not Interested Reason`.

## Known NOT-IN-USE / stale fields (don't rely on)

All Traffic Sources (empty), Campaign Name (2y), CP Name (2y), Project Name /
Project City (1.5y), Qty_delivered (2.5y), Scoring (1.5y), StateCode (1.5y),
Currently Active Business, BH Employee ID / BH Phone (unused), Stages Date,
Sales Person Employee ID / Employee Code ("not needed" — use Sales Person Emp ID).

## Note on SUM metrics

Revenue/volume questions (won amount by rep/zone, volume by dealer) need a SUM of
`Amount`/`Won_Amount`/`Volume_In_Sq_Mtr`, not a COUNT. The current breakdown counts
records; a `metric=sum` option is the planned next step.
