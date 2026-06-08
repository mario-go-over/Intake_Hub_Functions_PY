# Intake Hub Functions - Project Analysis

## Executive Summary

**Intake Hub Functions** is a Python-based Azure Functions application designed for **insurance record deduplication and clearance**. It provides two core functionalities:
1. **ETL/Sync Pipeline**: Synchronizes insured records from multiple MSSQL sources to a centralized PostgreSQL database
2. **Duplicate Scoring API**: A FastAPI-based HTTP service that checks incoming records against existing data to detect duplicates

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Azure Functions Host                                │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │  function_app.py (AsgiFunctionApp wrapper)                              │ │
│  │       │                                                                  │ │
│  │       └── FastAPI Application (scorer/app.py)                           │ │
│  │              ├── /ping (Health check)                                   │ │
│  │              ├── /healthz (DB health check)                             │ │
│  │              └── /duplicates/check (Main duplicate detection endpoint)  │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                           ETL Sync Process                                   │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐  │
│  │   MSSQL Source  │───▶│   Normalization │───▶│   PostgreSQL Target     │  │
│  │   (tblInsureds  │    │   + Blocking    │    │   (clearanceinsureds)   │  │
│  │   tblLocations) │    │   Keys          │    │                         │  │
│  └─────────────────┘    └─────────────────┘    └─────────────────────────┘  │
│          ▲                                                                   │
│          │ Checkpoint-based incremental sync                                │
│          └──────────────────────────────────────────────────────────────────│
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Component Analysis

### 1. Configuration Layer (`config.py`)

**Purpose**: Centralized environment variable management with validation

**Key Features**:
- Uses `python-dotenv` for local development
- Supports multi-source MSSQL configurations via `MSSQL_SOURCES` environment variable
- Per-source checkpoint file management for incremental ETL
- Backward compatibility for single-source deployments

**Environment Variables**:
| Variable | Description | Required |
|----------|-------------|----------|
| `PG_DSN` | PostgreSQL connection string | Yes |
| `HMAC_KEY` | Secret key for tax ID hashing (first 32 chars) | No |
| `BATCH_SIZE` | Records per ETL batch (default: 2000) | No |
| `MSSQL_SOURCES` | Comma-separated source names | Yes* |
| `MSSQL_DSN_<NAME>` | DSN for each source | Per source |
| `CHECKPOINT_DIR` | Directory for checkpoint files | No |

---

### 2. Data Normalization Layer (`normalize/`)

This is the **core intelligence** of the duplicate detection system.

#### 2.1 Name Normalization (`name.py`)

**Algorithm**:
1. Replace `&` with "and"
2. Expand ordinals (1st → first, 2nd → second, etc.)
3. Apply `slugify` to remove punctuation and accents
4. Strip company suffixes (Inc, LLC, Ltd, Corp, etc.)
5. Collapse whitespace
6. Generate phonetic key using **Double Metaphone**

**Example**:
```
Input:  "ABC Industries, Inc."
Output: { "name_norm": "abc industries", "name_phon": "APKN" }
```

#### 2.2 Address Normalization (`address.py`)

**Algorithm**:
1. Slugify the address
2. Expand abbreviations (St→Street, Ave→Avenue, N→North, etc.)
3. Extract house number (first numeric token)
4. Remove unit identifiers (Apt, Suite, Unit, #)
5. Generate phonetic key for street core (excluding directionals)

**Example**:
```
Input:  "123 N Main St, Apt 4B"
Output: { "house_no": "123", "street_core": "north main street", "street_phon": "MNST" }
```

#### 2.3 Tax ID Normalization (`taxid.py`)

**Security-Focused Design**:
- Extracts only last 4 digits for comparison
- Uses HMAC-SHA256 hash for exact matching without storing raw tax IDs
- Sensitive data is never stored in plain text

**Example**:
```
Input:  "12-3456789"
Output: { "tax_last4": "6789", "tax_hash": "a1b2c3..." (64 hex chars) }
```

#### 2.4 Blocking Keys (`blocking.py`)

**Purpose**: Reduce comparison space from O(n²) to manageable candidate sets

**Four Blocking Strategies**:
| Key | Formula | Use Case |
|-----|---------|----------|
| `bk1` | `zip5\|name_phon[:6]` | Same ZIP + similar name sounds |
| `bk2` | `city\|state\|house_no\|street_phon[:8]` | Same location pattern |
| `bk3` | `state\|tax_last4` | Same state + tax ID suffix |
| `bk4` | `zip5\|name_norm[:10]` | Same ZIP + name prefix |

---

### 3. Database Layer (`db/`)

#### 3.1 MSSQL Connector (`mssql.py`)

- Uses `pyodbc` for SQL Server connectivity
- Joins `tblInsureds` with `tblInsuredLocations`
- Implements checkpoint-based incremental fetching via `DateAdded` column
- Uses `READPAST` hint to avoid lock contention

#### 3.2 PostgreSQL Connector (`postgres.py`)

- Uses `psycopg2` with batch execution
- Implements UPSERT pattern (`ON CONFLICT ... DO UPDATE`)
- Target table: `public.clearanceinsureds`
- Stores normalized fields + all 4 blocking keys

---

### 4. Models (`models/record.py`)

**Two Core Data Classes**:

```python
@dataclass
class Record:        # Raw input
    id, name, address, city, state, zip5, tax_id

@dataclass
class Canonical:     # Normalized output
    id, name_norm, name_phon, house_no, street_core, street_phon,
    city, state, zip5, tax_last4, tax_hash
```

The `canonicalize()` function transforms `Record` → `Canonical`

---

### 5. ETL Sync Process (`sync.py`)

**Flow**:
1. Load checkpoint (last processed timestamp) for each source
2. Fetch batch from MSSQL where `DateAdded > checkpoint`
3. Transform each row through normalization pipeline
4. Generate blocking keys
5. Upsert to PostgreSQL
6. Update checkpoint file
7. Repeat until no more rows

**Key Design Decisions**:
- Per-source checkpointing enables independent recovery
- Batch processing with configurable size
- Transaction-safe with explicit commits

---

### 6. Duplicate Scorer API (`scorer/app.py`)

**FastAPI Endpoints**:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/ping` | GET | Simple health check |
| `/healthz` | GET | Database connectivity check |
| `/duplicates/check` | POST | Main duplicate detection |

**Duplicate Detection Algorithm**:

```
1. SHORTCUT #1: Exact Tax Hash Match
   → Return AUTO_DUPLICATE immediately

2. SHORTCUT #2: Exact (name_norm + zip5 + house_no) Match
   → Return AUTO_DUPLICATE immediately

3. BLOCKING KEY SEARCH:
   → Call surg_find_dup_candidates_by_bk() stored procedure
   → Returns candidates matching any of 4 blocking keys

4. FUZZY SCORING:
   For each candidate:
   - name_score = JaroWinkler(incoming.name_norm, candidate.name_norm)
   - address_score = 0.7 * JaroWinkler(street_core) + 0.10 (if house_no matches)
   
   If tax_id available:
     final_score = 0.45*name + 0.35*address + 0.20*tax_match
   Else:
     final_score = 0.55*name + 0.45*address

5. DECISION THRESHOLDS:
   - score >= 0.90 → AUTO_DUPLICATE
   - score >= 0.80 → REVIEW
   - score <  0.80 → NOT_DUPLICATE
```

---

### 7. Azure Functions Integration (`function_app.py`)

Uses the **Azure Functions v2 programming model** with `AsgiFunctionApp` to wrap FastAPI as an HTTP trigger. This enables:
- Serverless scaling
- Azure-managed infrastructure
- Integration with Azure monitoring

---

## Technology Stack

| Category | Technology |
|----------|------------|
| Runtime | Python 3.x |
| Cloud Platform | Azure Functions |
| Web Framework | FastAPI |
| MSSQL Driver | pyodbc |
| PostgreSQL Driver | psycopg2-binary |
| Fuzzy Matching | rapidfuzz (Jaro-Winkler) |
| Phonetic Encoding | Metaphone (Double Metaphone) |
| Text Normalization | python-slugify |
| Configuration | python-dotenv |

---

## Data Flow Diagram

```
┌──────────────────────┐
│   Insurance System   │
│   (MSSQL Sources)    │
│   - tblInsureds      │
│   - tblInsuredLocs   │
└──────────┬───────────┘
           │ ETL Sync (main.py)
           ▼
┌──────────────────────┐
│   Normalization      │
│   - Name cleanup     │
│   - Address parsing  │
│   - Tax ID hashing   │
│   - Blocking keys    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   PostgreSQL         │
│   clearanceinsureds  │
│   - Normalized data  │
│   - Blocking keys    │
│   - HMAC hashes      │
└──────────┬───────────┘
           │ Query (scorer API)
           ▼
┌──────────────────────┐
│   Duplicate Scorer   │
│   POST /duplicates   │
│   /check             │
│   → Blocking lookup  │
│   → Fuzzy scoring    │
│   → Decision         │
└──────────────────────┘
```

---

## Security Considerations

### Current Security Measures

1. **Tax ID Protection**: Only last 4 digits stored, full ID hashed with HMAC-SHA256
2. **No Raw Sensitive Data**: Original tax IDs never persisted
3. **Environment-based Secrets**: Credentials via environment variables

### Security Gaps to Address

1. ⚠️ **Hardcoded Credentials in `local.settings.json`**: Production credentials visible
2. ⚠️ **Short HMAC Key**: Current implementation truncates to 32 characters
3. ⚠️ **No Input Validation**: API accepts arbitrary dict without schema validation
4. ⚠️ **SQL Injection Risk**: While parameterized queries are used, stored procedure calls should be audited
5. ⚠️ **No Authentication**: API endpoints have `authLevel: anonymous`

---

---

# Deep Questions for Improvement

## Architecture & Design

### Q1: Scalability of Blocking Key Strategy
**Current State**: Four blocking keys are generated and stored for each record, enabling efficient candidate retrieval.

**Questions**:
- What is the selectivity of each blocking key in production data? Are some keys retrieving too many candidates?
- Has there been analysis of false negative rates (true duplicates missed because they don't share any blocking key)?
- Should blocking keys be adaptive based on data distribution (e.g., high-density ZIP codes)?
- Would a probabilistic data structure (Bloom filter) reduce blocking key storage while maintaining recall?

### Q2: Scoring Algorithm Weights
**Current State**: Fixed weights (0.45 name / 0.35 address / 0.20 tax for tax-available; 0.55 / 0.45 otherwise)

**Questions**:
- How were these weights determined? Was there A/B testing or precision/recall analysis?
- Should weights be configurable per source or per use case?
- Would machine learning (logistic regression, gradient boosting) improve accuracy over hand-tuned weights?
- Has the 0.80/0.90 threshold been validated against ground truth?

### Q3: Multi-Source Data Conflicts
**Current State**: Each source syncs independently; conflicts resolved by "last write wins" via UPSERT.

**Questions**:
- What happens when the same `insured_guid` appears in multiple sources with different data?
- Should there be source priority ranking for conflict resolution?
- Is there audit logging for data overwrites?
- Should historical versions be retained?

### Q4: Checkpoint Reliability
**Current State**: File-based checkpointing with ISO datetime strings.

**Questions**:
- What happens if the process crashes between Postgres commit and checkpoint write?
- Are there duplicate records in Postgres due to checkpoint gaps?
- Should checkpointing be transactional (stored in Postgres itself)?
- How does checkpoint recovery work in distributed/scaled deployments?

---

## Performance & Optimization

### Q5: Database Query Efficiency
**Current State**: Stored procedure `surg_find_dup_candidates_by_bk` retrieves candidates.

**Questions**:
- What indexes exist on `clearanceinsureds` table for blocking key columns?
- What is the average/P95/P99 latency of the duplicate check endpoint?
- Should there be composite indexes on blocking key combinations?
- Would materialized views improve query performance?

### Q6: Batch Processing Optimization
**Current State**: Fixed 2000-record batches with sequential processing.

**Questions**:
- Is 2000 the optimal batch size for the current infrastructure?
- Would parallel processing across sources improve throughput?
- Should normalization be parallelized within a batch (multiprocessing/threading)?
- What is the bottleneck: MSSQL read, normalization, or Postgres write?

### Q7: Connection Management
**Current State**: Single connection per operation; reconnection on failure.

**Questions**:
- Should connection pooling be implemented for production workloads?
- What is the connection timeout and retry strategy?
- How does the API handle connection pool exhaustion under load?
- Are connections properly cleaned up in all error paths?

---

## Code Quality & Maintainability

### Q8: Type Safety & Validation
**Current State**: Minimal type hints; `dict` used extensively; no Pydantic models for API.

**Questions**:
- Should FastAPI Pydantic models replace raw `dict` parameters?
- Would strict dataclass validation catch data quality issues earlier?
- Are there runtime type checking tools (e.g., beartype) that could help?
- Should mypy strict mode be enforced?

### Q9: Error Handling Strategy
**Current State**: Basic try/except with generic exception handling.

**Questions**:
- What is the error classification strategy (retryable vs fatal)?
- Should there be dead letter queue for failed records?
- How are partial batch failures handled (10 of 2000 fail)?
- Is there alerting on error rates?

### Q10: Testing Coverage
**Current State**: No test files visible in project structure.

**Questions**:
- What is the current test coverage percentage?
- Are there unit tests for normalization functions?
- Are there integration tests for the ETL pipeline?
- Is there a test dataset with known duplicates for regression testing?

---

## Data Quality & Accuracy

### Q11: Normalization Edge Cases
**Current State**: Fixed normalization rules for names and addresses.

**Questions**:
- How are international addresses handled (non-US formats)?
- What about names with special characters (O'Brien, McDonald, José)?
- Are there industry-specific abbreviations missing from `STREET_MAP`?
- How are PO Box addresses normalized?
- What about addresses with "Suite" in the middle vs end?

### Q12: Phonetic Algorithm Choice
**Current State**: Double Metaphone for both names and addresses.

**Questions**:
- Has Soundex, NYSIIS, or Beider-Morse been compared for accuracy?
- Is Double Metaphone optimal for company names (vs personal names)?
- Should different algorithms be used for names vs addresses?
- How do phonetic false positives affect precision?

### Q13: Data Staleness
**Current State**: Records sync based on `DateAdded` from source.

**Questions**:
- How are updated records (same GUID, changed data) captured?
- What about deleted records in source systems?
- Is there a full reconciliation process to catch missed updates?
- What is the maximum acceptable data latency?

---

## Operational Excellence

### Q14: Monitoring & Observability
**Current State**: Basic logging via Python logging module.

**Questions**:
- Are there structured logs (JSON) for log aggregation?
- What metrics are tracked (sync rate, match rate, error rate)?
- Is there distributed tracing (OpenTelemetry)?
- Are there dashboards for operational visibility?

### Q15: Deployment & CI/CD
**Current State**: No CI/CD configuration visible.

**Questions**:
- What is the deployment strategy (GitHub Actions, Azure DevOps)?
- Is there automated testing before deployment?
- How are database migrations managed?
- What is the rollback strategy?

### Q16: Disaster Recovery
**Current State**: No DR configuration visible.

**Questions**:
- What is the RPO/RTO for this system?
- Are checkpoints backed up?
- Is there multi-region redundancy?
- How is Postgres backed up?

---

## Feature Enhancements

### Q17: Real-time vs Batch
**Current State**: Batch-only ETL with separate API for real-time checks.

**Questions**:
- Should there be a real-time sync option (CDC/Change Data Capture)?
- Would Kafka/Event Hub enable near-real-time duplicate detection?
- Can the API write new records directly (bypass ETL)?

### Q18: API Enhancements
**Current State**: Single `/duplicates/check` endpoint.

**Questions**:
- Should there be bulk check endpoint for batch submissions?
- Would async processing (webhook callback) improve UX for large batches?
- Should there be a "register" endpoint to add new records?
- Is there need for duplicate resolution/merge endpoint?

### Q19: Explainability
**Current State**: API returns score and decision only.

**Questions**:
- Should the API explain *why* a match was made (which fields matched)?
- Would field-level scores help human reviewers?
- Should there be a "diff" view showing normalized vs original values?

### Q20: Machine Learning Integration
**Current State**: Rule-based scoring with fixed weights.

**Questions**:
- Would a trained classifier improve accuracy?
- Is there labeled training data (confirmed duplicates/non-duplicates)?
- Should there be active learning to improve from human review decisions?
- Would embeddings (sentence transformers) improve name matching?

---

## Immediate Action Items

### Priority 1: Security
1. [ ] Move credentials out of `local.settings.json` to Azure Key Vault
2. [ ] Add API authentication (Azure AD, API keys)
3. [ ] Implement request validation with Pydantic models
4. [ ] Audit stored procedure for SQL injection

### Priority 2: Reliability
1. [ ] Implement connection pooling
2. [ ] Add retry logic with exponential backoff
3. [ ] Move checkpoints to database for transactional consistency
4. [ ] Add comprehensive error handling

### Priority 3: Observability
1. [ ] Add structured logging
2. [ ] Implement Azure Application Insights
3. [ ] Create operational dashboards
4. [ ] Add alerting for error rates

### Priority 4: Quality
1. [ ] Add unit tests for normalization functions
2. [ ] Add integration tests for ETL pipeline
3. [ ] Implement type hints throughout
4. [ ] Set up CI/CD pipeline

---

## Conclusion

The Intake Hub Functions project demonstrates a well-architected approach to record deduplication with thoughtful normalization and blocking strategies. The separation of concerns (normalization, blocking, scoring) enables maintainability and testing.

Key strengths:
- Clean modular architecture
- Security-conscious tax ID handling
- Efficient blocking key strategy
- Flexible multi-source support

Areas for improvement:
- Testing and type safety
- Operational observability
- Security hardening
- Performance optimization

This analysis provides a roadmap for evolving the system from a functional prototype to a production-grade, enterprise-ready duplicate detection service.

---

*Generated: December 5, 2025*
*Analysis Scope: Full codebase review*

