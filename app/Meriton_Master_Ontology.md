# Meriton Enterprise Master Ontology (Global Business Vocabulary & Semantic Layer)
**Version:** 1.0.0  
**Target Architecture:** Microsoft Fabric / DirectLake / Lakehouse / Multi-Semantic Model Catalog  
**Domain Scope:** Accounts Payable (AP Automation), Purchase-to-Pay (P2P), Vendor Management, Job Costing, Sales Bookings & Commission Credits  
**Authoritative Status:** Certified Master Business Vocabulary (Decoupled from physical table implementations)

---

## 1. Architectural Role & Pre-Flight Contract

This Master Ontology serves as the **Enterprise Single Source of Truth (SSOT)** for all business terms, conceptual entities, controlled vocabularies, metric formulas, and natural language synonyms across the Meriton enterprise landscape.

### Governing Rules for AI / MCP Gateway Agents:
1. **Model Decoupling:** This ontology defines *what business concepts mean*, their conceptual hierarchies, mathematical definitions, and disambiguation policies. It is **not** bound to a single physical table or model.
2. **Pre-Flight Semantic Binding:** Before formulating any DAX or SQL query, the agent MUST:
   - Identify the business concept from the user query.
   - Look up the Canonical Term and Definition in this Master Ontology.
   - Inspect the target Semantic Model / Lakehouse schema (`GetSemanticModelSchema`) to bind the canonical measure or physical column.
3. **No-Silent-Guessing Rule:** If a user query contains ambiguous phrasing (e.g., "overcharged" without stating price vs. quantity, or "spend" without specifying invoiced vs. committed), the agent MUST request clarification or state the exact ontological branch being evaluated.

---

## 2. Core Business Domains & Conceptual Entities

The enterprise ontology bridges two primary operational wings:
* **Procure-to-Pay & AP Lifecycle (What Was Billed & Ordered):** PO lifecycle, invoice matching, vendor and property spend, and exception resolution.
* **Commercial Operations & Revenue Credit (What Was Sold & Who Gets Credit):** Sales bookings, job line items, commissions, territories, and sales reps.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 COMMERCIAL OPERATIONS                                  │
│                                                                                        │
│  [Date] ──dated by──> [Booking] ──sold to──> [Customer]                                │
│                          │ (against)                                                   │
│                          ▼                                                             │
│                   [Job Line Item] ──supplied by──> [Vendor]                            │
│                          │ (part of)                                                   │
│                          ▼                                                             │
│                        [Job] ──belongs to──> [Company]                                 │
│                          ▲                                                             │
│                          │ (credits job * split %)                                     │
│                 [Commission Split] ──attributed to──> [Assigned Account]               │
│                          │                                                             │
│                          └──in territory──> [Territory]                                │
│                          │                                                             │
│                          └──credited to───> [Salesperson]                              │
└────────────────────────────────────────────┬───────────────────────────────────────────┘
                                             │ (Shared Company & Vendor Dimensions)
┌────────────────────────────────────────────▼───────────────────────────────────────────┐
│                               ACCOUNTS PAYABLE & P2P                                   │
│                                                                                        │
│       [Company] ─────────────── parent of ───────────────┐                             │
│           │                                              │                             │
│           ├──────────────> [Property] <──────┐           │                             │
│           │                                  │           │                             │
│           ├──────────────> [Vendor]          │           │                             │
│           │                   │              │           │                             │
│           ▼                   ▼              │           │                             │
│  [Purchase Order (PO)] ──> [PO Line] ──belongs to        │                             │
│           │                    │                         ▼                             │
│           │                    └──matched to──┐    [Invoice]                           │
│           ▼                                   │        │                               │
│  [PO Lifecycle Summary]                       ▼        ▼                               │
│                                           [Match Fact] <── [Invoice Line]              │
│                                               │                                        │
│                        ┌──────────────────────┴──────────────────────┐                 │
│                        ▼                                             ▼                 │
│             [Vendor Spend Summary]                        [Property Spend Summary]     │
│                        │                                                               │
│                        ▼                                                               │
│             [Exception Queue]                                                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Entity Dictionary

| Conceptual Entity | Grain | Semantic Definition | Key Attributes | Core Relationships |
| :--- | :--- | :--- | :--- | :--- |
| **Company** | One legal corporate entity | Legal parent operating unit (e.g., `MERNSW`, `MERVIC`, `TAS`, `CFM`). Scopes all child operational entities. | `companyCode`, `companyName`, `taxRegistration` | Parent of Property, Vendor, PO, Invoice, Job. |
| **Property** | One development, building site, or hotel | Real-estate asset, construction project, or hotel property where capital or operational spend is deployed. | `propertyId`, `propertyName`, `propertyType` | Belongs to Company; referenced by Purchase Orders, Jobs, and Invoices. |
| **Vendor** | One supplier / manufacturer per legal entity | External supplier, trade contractor, or manufacturer providing materials or services. Inherits matching rules. | `vendorAccount`, `vendorName`, `aliasName`, `matchingPolicy` | Belongs to Company; referenced by POs, Invoices, and Job Line Items. |
| **Purchase Order (PO)** | One commercial procurement agreement | Official commercial contract issued to a vendor committing funds for specific properties. | `poNumber`, `companyCode`, `orderDate`, `status`, `poType` | Parent of PO Lines; belongs to Company, Vendor, Property. |
| **PO Line** | One line item on a Purchase Order | Granular commitment representing a specific product/service code, quantity, and agreed unit price. | `poNumber`, `lineNumber`, `productCode`, `orderedQty`, `unitPrice` | Child of PO; matched against Invoice Lines. |
| **Invoice** | One billing claim from a vendor | Financial billing document submitted by a vendor claiming payment for goods/services. | `invoiceNumber`, `companyCode`, `invoiceDate`, `grossAmount` | Belongs to Company, Vendor; parent of Invoice Lines. |
| **Invoice Line** | One line item on an Invoice | Granular billed item specifying billed product code, invoiced quantity, and invoiced unit price. | `invoiceNumber`, `lineNumber`, `productCode`, `invoicedQty`, `unitPrice` | Child of Invoice; matched against PO Line. |
| **Match Fact** | One evaluated Invoice Line against PO Line | Automated reconciliation record capturing variance, status, and financial alignment between PO Line and Invoice Line. | `matchFactId`, `matchStatus`, `quantityDelta`, `priceDelta`, `varianceFlag` | Derived from Invoice Line + PO Line; rolls up into Spend Summaries. |
| **Exception Queue** | One un-reconciled Match Fact | Actionable queue of invoice items requiring human intervention due to tolerance failure or missing POs. | `matchFactId`, `exceptionPriority`, `failureReason` | Filtered subset of Match Fact (`matchStatus != 'auto_bound'`). |
| **Job** | One commercial deal or sales project | High-level commercial sales contract or construction job against which line items and bookings are registered. | `jobId`, `jobName`, `verticalMarket` | Belongs to Company; has many Job Line Items; credited by Commission Splits. |
| **Job Line Item (JLI)** | One line item within a Job | Granular deliverable or equipment line within a sales job. | `jobLineItemId`, `jobId`, `productCode`, `costAmount`, `sellAmount` | Belongs to Job; supplied by Vendor; booked against Booking dates. |
| **Booking** | One transaction event (estimate or revision) | Dated financial sales booking event capturing sold value and revision dates. | `bookingId`, `bookingDate`, `bookingAmount`, `bookingType` | Linked to Job Line Item; sold to Customer. |
| **Customer** | One client / purchaser | External buyer or entity to whom the job/line item was sold. | `customerId`, `customerName`, `clientSegment` | Sold-to party for Bookings. |
| **Commission Split** | One credit distribution rule per role | Percentage allocation of job credit assigned to sales reps and accounts. | `splitId`, `jobId`, `salespersonId`, `splitPercentage` | Credits Job; attributed to Assigned Account and Territory. |
| **Salesperson** | One sales executive / representative | Employee credited for sales bookings based on commission split rules. | `salespersonId`, `employeeName`, `roleTitle` | Receives credit via Commission Split. |
| **Territory** | One sales geographic region | Regional sales domain governing accounts and sales representatives. | `territoryId`, `regionName`, `state` | Classifies Commission Splits. |
| **Assigned Account** | One designated corporate client account | Named corporate account portfolio assigned to specific sales teams. | `accountId`, `accountName`, `tier` | Attributed via Commission Split. |

---

## 3. Controlled Vocabularies & Business Rules

### 3.1 Invoice Matching Status (`matchStatus`)
Evaluated strictly in sequential hierarchical order (**Level 1 $ightarrow$ Level 2 $ightarrow$ Level 3**). An item fails at the first broken threshold; downstream checks are bypassed.

```
                    [Invoice Line Received]
                                │
                                ▼
            ┌───────────────────────────────────────┐
            │ L1: PO Line Existence Check           │
            │ Does PO Line exist for Co + PO + Item?│
            └───────────────────┬───────────────────┘
                       NO       │       YES
        ┌───────────────────────┘       └───────────────────────┐
        ▼                                                       ▼
  [unmatched]                               ┌───────────────────────────────────────┐
  (Priority 1: Most Urgent)                 │ L2: Quantity Tolerance Check          │
                                            │ Within underPct/overPct & Receipt?    │
                                            └───────────────────┬───────────────────┘
                                                       NO       │       YES
                                        ┌───────────────────────┘       └───────────────────────┐
                                        ▼                                                       ▼
                                 [qty_variance]                             ┌───────────────────────────────────────┐
                                 (Priority 3)                               │ L3: Price Tolerance Check             │
                                                                            │ Within $0.50 AND 1% of PO Unit Price? │
                                                                            └───────────────────┬───────────────────┘
                                                                                       NO       │       YES
                                                                        ┌───────────────────────┘       └───────────────┐
                                                                        ▼                                               ▼
                                                                 [price_variance]                                 [auto_bound]
                                                                 (Priority 2)                                     (Clean Match)
```

| Value | Technical Logic / Rule | Priority Ranking | Action Required |
| :--- | :--- | :---: | :--- |
| **`unmatched`** | **L1 Failure:** No matching PO Line exists for the company code, PO number, and product code combination (`po.lotId IS NULL`). | **1** (Highest) | Human review: Missing PO or incorrect PO number supplied by vendor. |
| **`price_variance`** | **L3 Failure:** Unit price differs from PO unit price by more than **both** `$0.50` (absolute) **and** `1.0%` (relative). | **2** | Human review: Vendor overcharged/undercharged unit price outside tolerance. |
| **`qty_variance`** | **L2 Failure:** Billed quantity falls outside PO line tolerance (`orderedQty ± tolerance`), or exceeds `remainingReceived` for Three-Way vendors. | **3** | Human review: Quantity discrepancy or unreceived goods delivery. |
| **`auto_bound`** | **Pass (L1 + L2 + L3):** All checks succeed. Line item is verified clean. | *None* | Straight-through processing; immediate release for settlement. |

### 3.2 Vendor Matching Policies
A structural property of the Vendor profile that dictates the severity of quantity verification:
* **`TwoWayMatching` (Standard):** Evaluates PO vs. Invoice only (Checks quantity tolerance against `orderedQty` and unit price against PO price). Does not require goods-receipt vouchers.
* **`ThreeWayMatching` (Strict):** Evaluates PO vs. Invoice vs. Receiving. Invoice quantity must pass standard PO quantity tolerance **AND** cannot exceed the quantity physically received on dock (`remainingReceived`).

### 3.3 Default Tolerance Bands (Provisional Baseline)
* **Price Absolute Tolerance:** `±$0.50`
* **Price Relative Tolerance:** `±1.0%`
* **Quantity Tolerance Bands:** `0.0%` (unless custom over/under percentages are explicitly configured on the specific PO Line).

---

## 4. Canonical Enterprise Metrics & Mathematical Formulas

These metrics represent the certified mathematical definitions across the enterprise. Agents must look for certified semantic measures bound to these definitions before computing any ad-hoc expressions.

| Canonical Metric | Conceptual Definition | Mathematical Formulation | Semantic Binding Target (Measures) |
| :--- | :--- | :--- | :--- |
| **Committed Spend** | Total financial expenditure officially committed via approved purchase orders, regardless of invoicing status. | $$\sum 	ext{PO\_Line}[	ext{lineAmount}]$$ | `[Total Committed Amount]` / `[Committed Spend]` |
| **Invoiced Spend** | Total gross financial expenditure billed by vendors across matched invoice lines in the AP pipeline. | $$\sum 	ext{Match\_Fact}[	ext{invoiceAmount}]$$ | `[Total Invoiced Amount]` / `[Invoiced Spend]` |
| **Variance to Committed** | Absolute delta between billed invoice spend and committed PO value. Negative = mid-project under-invoiced; Positive = over-run risk. | $$	ext{Invoiced Spend} - 	ext{Committed Spend}$$ | `[Variance to Committed]` / `[Budget Spend Variance]` |
| **Exception Rate** | Cleanliness ratio of vendor billing. Proportion of invoice lines failing automated checks. | $$rac{\sum 	ext{Match\_Fact}[	ext{matchStatus} 
eq 	ext{'auto\_bound'}]}{	ext{Total Invoice Lines}}$$ | `[Exception Rate]` / `[Vendor Error Rate]` |
| **Remaining PO Balance** | Total open un-invoiced balance remaining across active purchase order commitments. | $$\sum 	ext{PO}[	ext{remainingAmount}]$$ | `[Total Remaining Amount]` / `[Open PO Balance]` |
| **Sales Booking Value** | Total monetary value of commercial sales contracts booked within a specified date period. | $$\sum 	ext{Booking}[	ext{bookingAmount}]$$ | `[Total Booking Value]` / `[Gross Bookings]` |
| **Credited Sales Value** | Portion of a job's sales booking attributed to a specific salesperson or territory based on commission split rules. | $$\sum (	ext{Booking}[	ext{bookingAmount}] 	imes 	ext{Split}[	ext{splitPercentage}])$$ | `[Credited Sales Amount]` |

---

## 5. Enterprise Linguistic Synonym & Disambiguation Matrix

This mapping defines how business users' colloquial phrasing maps to canonical ontology terms:

```json
{
  "synonym_mappings": [
    {
      "canonical_term": "auto_bound",
      "category": "Controlled Vocabulary (matchStatus)",
      "user_phrasings": ["clean", "matched", "auto-matched", "no issues", "passed", "straight through", "clean match"],
      "filter_expression": "matchStatus = 'auto_bound'"
    },
    {
      "canonical_term": "unmatched",
      "category": "Controlled Vocabulary (matchStatus)",
      "user_phrasings": ["no PO found", "missing PO", "orphan invoice", "unlinked", "no matching purchase order"],
      "filter_expression": "matchStatus = 'unmatched'"
    },
    {
      "canonical_term": "qty_variance",
      "category": "Controlled Vocabulary (matchStatus)",
      "user_phrasings": ["quantity mismatch", "qty discrepancy", "over delivered", "short delivery", "wrong count", "count variance"],
      "filter_expression": "matchStatus = 'qty_variance'"
    },
    {
      "canonical_term": "price_variance",
      "category": "Controlled Vocabulary (matchStatus)",
      "user_phrasings": ["price mismatch", "rate difference", "rate discrepancy", "wrong unit price", "rate variance"],
      "filter_expression": "matchStatus = 'price_variance'"
    },
    {
      "canonical_term": "exception",
      "category": "Operational Status",
      "user_phrasings": ["flagged", "failed match", "needs review", "needs a human", "in error", "problem invoices", "queue"],
      "filter_expression": "matchStatus != 'auto_bound'"
    },
    {
      "canonical_term": "invoiced_spend",
      "category": "Core Metric",
      "user_phrasings": ["billed us", "total billed", "gross spend", "invoiced amount", "vendor billing", "claims paid", "amount invoiced"],
      "resolves_to_metric": "Invoiced Spend"
    },
    {
      "canonical_term": "committed_spend",
      "category": "Core Metric",
      "user_phrasings": ["ordered amount", "PO value", "contracted spend", "what we committed to", "budget committed", "orders placed"],
      "resolves_to_metric": "Committed Spend"
    },
    {
      "canonical_term": "exception_rate",
      "category": "Core Metric",
      "user_phrasings": ["error rate", "vendor error rate", "cleanliness score", "cleanliness", "how clean is this vendor", "vendor reliability rate"],
      "resolves_to_metric": "Exception Rate"
    },
    {
      "canonical_term": "sales_credit",
      "category": "Commercial Metric",
      "user_phrasings": ["rep credit", "commission credit", "attributed sales", "sales split", "rep performance"],
      "resolves_to_metric": "Credited Sales Value"
    }
  ],
  "disambiguation_rules": [
    {
      "trigger_phrase": "overcharged / undercharged",
      "ambiguity": "Could indicate a unit price discrepancy (price_variance) OR billed quantity exceeding goods received (qty_variance).",
      "action": "Prompt user: 'Do you mean overcharged on Unit Price (price variance) or Billed Quantity (quantity variance)?' If unanswerable, evaluate both dimensions simultaneously."
    },
    {
      "trigger_phrase": "spend",
      "ambiguity": "Could refer to Invoiced Spend (actual vendor claims) or Committed Spend (total purchase order commitments).",
      "action": "Default to Invoiced Spend for AP questions, but present Committed Spend alongside Variance to Committed if analyzing project budgets."
    },
    {
      "trigger_phrase": "vendor / supplier / manufacturer",
      "ambiguity": "Synonymous terms across AP and Sales Job Line Items.",
      "action": "Resolve uniformly to the Vendor entity."
    }
  ]
}
```

---

## 6. Known Domain Invariants & Data Integrity Annotations

1. **Company Partitioning:** All operational transactions (`silver_properties`, `silver_vendors`, `silver_purchase_orders`, `silver_invoices`) are legally partitioned by `company` (e.g., `MERNSW`, `MERVIC`). A vendor account ID is only unique within its parent legal company.
2. **Gold Model Aggregation Grain:**
   - `gold_vendor_spend_summary`: Grain is exactly `(company, vendorAccount)`.
   - `gold_property_spend_summary`: Grain is exactly `(company, propertyId)`.
   - `gold_po_lifecycle_summary`: Grain is `(company, status, type)`.
3. **Data Integrity Observation — PROP-202 (Broadbeach):**
   - Active spend tracking records show `PROP-202` (Meriton Suites Broadbeach) with committed spend of ~$200.7k but only ~$66.2k invoiced. Mid-stage projects regularly exhibit negative variance to committed; this reflects active un-invoiced commitments rather than data pipeline failure.
4. **Data Integrity Observation — MERVIC (Southbank One):**
   - The entity `MERVIC` exists in core master tables (`silver_companies`, `silver_properties`), but currently registers zero Gold transactional facts. Absence in Gold tables reflects zero operational billing activity to date, not an extraction error.

---

## 7. Claude Desktop / Agent Query Execution Protocol

When Claude processes a user prompt using this Master Ontology:

1. **Phase 1: Concept Extraction:** Match raw user input against Section 5 (Linguistic Synonyms).
2. **Phase 2: Disambiguation:** Check if terms require clarification under Section 5 (Disambiguation Rules).
3. **Phase 3: Catalog & Measure Binding:**
   - Call `search_semantic_models` to locate the active workspace / dataset.
   - Call `GetSemanticModelSchema` to inspect whether canonical certified measures exist (e.g. `[Total Invoiced Spend]`, `[Vendor Exception Rate]`).
4. **Phase 4: DAX Generation:**
   - If certified measures exist: Generate DAX calling those measures (`EVALUATE SUMMARIZECOLUMNS(..., [Total Invoiced Spend], ...)`).
   - If model is unmeasured (raw tabular): Formulate dynamic DAX using the exact mathematical formulas certified in Section 4.
5. **Phase 5: Execution & Presentation:**
   - Execute query through MCP Gateway.
   - Return clean markdown tables and explain results using certified canonical definitions.
