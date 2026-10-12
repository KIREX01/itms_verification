# Compliance & Audit Standards

**Document ID:** ENT-06  
**Category:** Compliance & Security  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** All OHAS Components, All Countries of Operation  

---

## 1. Current State

```
What exists:
  ✅ Audit logging (audit_logs table tracks actions)
  ✅ Error tracking (errors table)
  ✅ Security event logging
  ✅ Email verification on signup
  ✅ RLS for data isolation

What's missing:
  ✗ No documented compliance framework
  ✗ No data processing agreement (DPA)
  ✗ No privacy policy
  ✗ No consent tracking at signup
  ✗ No data subject access request (DSAR) process
  ✗ No data breach notification procedure
  ✗ No country-by-country compliance analysis
  ✗ No minor/children data handling policy
  ✗ No data transfer impact assessment
  ✗ No compliance audit schedule
```

---

## 2. Applicable Laws by Country

```
┌───────────────────┬────────────────────────────────────────────┬──────────────────────┐
│ Country           │ Data Protection Law                        │ Key Requirements     │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Kenya             │ Data Protection Act, 2019                  │ → Registration with  │
│                   │ Data Protection Regulations, 2021          │   Office of the Data │
│                   │                                            │   Protection Commissioner│
│                   │                                            │ → Data Protection    │
│                   │                                            │   Officer required   │
│                   │                                            │ → Cross-border       │
│                   │                                            │   transfer assessment│
│                   │                                            │ → 72-hour breach     │
│                   │                                            │   notification       │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Uganda            │ Data Protection and Privacy Act, 2019      │ → Registration with  │
│                   │                                            │   Personal Data      │
│                   │                                            │   Protection Office  │
│                   │                                            │ → Consent required   │
│                   │                                            │ → Right to rectify/  │
│                   │                                            │   erase              │
│                   │                                            │ → Breach notification│
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Tanzania          │ Personal Data Protection Act, 2022         │ → Data Protection    │
│                   │                                            │   Commission         │
│                   │                                            │ → Cross-border       │
│                   │                                            │   transfer approval  │
│                   │                                            │ → Data localization  │
│                   │                                            │   considerations     │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Rwanda            │ Law No. 058/2021 Relating to Personal      │ → National ID        │
│                   │ Data Protection                            │   numbers are        │
│                   │                                            │   sensitive data     │
│                   │                                            │ → Modeled on GDPR    │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Ethiopia          │ No comprehensive data protection law       │ → Follow GDPR as     │
│                   │ Computer Crime Proclamation No. 958/2016   │   best practice      │
│                   │ has some privacy provisions                │                      │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ South Sudan       │ No data protection law                     │ → Follow GDPR as     │
│                   │                                            │   best practice      │
├───────────────────┼────────────────────────────────────────────┼──────────────────────┤
│ Burundi           │ No comprehensive data protection law       │ → Follow GDPR as     │
│                   │                                            │   best practice      │
└───────────────────┴────────────────────────────────────────────┴──────────────────────┘

Strategy: Implement GDPR-level protections across ALL operations.
This covers the strictest requirements and simplifies compliance.
```

---

## 3. Data Processing Agreement (DPA)

### 3.1 DPA Template

```markdown
# Data Processing Agreement

Between: One Hope Africa System ("Controller")
And: [Country Partner Organization] ("Processor")

## 1. Subject Matter
OHAS processes program data (reports, participant information, goal tracking)
on behalf of the Controller for the purpose of program management and reporting.

## 2. Duration
This agreement is valid for the duration of the partnership and 2 years after
termination (to allow for data retrieval requests).

## 3. Nature and Purpose
OHAS collects, stores, and processes data submitted by reporters and accessed
by administrators for the purpose of:
- Program monitoring and evaluation
- Goal tracking and reporting
- Participant management
- Organizational analytics

## 4. Types of Personal Data
- User profiles (name, email, phone, role)
- Reporter submissions (program data, observations)
- Participant information (names, enrollment data)
- Audit logs (access patterns, system actions)

## 5. Categories of Data Subjects
- Reporters (field staff submitting data)
- Administrators (country and ministry level)
- Program participants (beneficiaries)
- System users (all authenticated users)

## 6. Controller Obligations
- Ensure lawful basis for processing
- Obtain consent from data subjects
- Respond to data subject access requests
- Notify Processor of any regulatory changes

## 7. Processor Obligations
- Process data only on documented instructions
- Ensure confidentiality of processing personnel
- Implement appropriate technical and organizational measures
- Assist Controller with data subject requests
- Notify Controller of data breaches within 24 hours
- Delete or return data at end of service

## 8. Sub-processors
OHAS uses the following sub-processors:
- Supabase (database hosting, US/EU region)
- [Email provider] (notification delivery)
- [Hosting provider] (application hosting)

## 9. International Transfers
Data is stored in [Supabase region]. Cross-border transfers are governed by
Standard Contractual Clauses (SCCs) or adequacy decisions.

## 10. Security Measures
See Appendix A: Technical and Organizational Security Measures.

## Signatures
Controller: ________________________ Date: ___________
Processor:  ________________________ Date: ___________
```

### 3.2 Technical and Organizational Measures (Appendix A)

```markdown
# Appendix A: Security Measures

## Technical Measures
- Encryption in transit (TLS 1.2+) for all data
- Encryption at rest (Supabase-maned, AES-256)
- Role-based access control (5 roles with least privilege)
- Row Level Security (database-level data isolation)
- Multi-factor authentication (for admin roles)
- Session management with automatic expiry
- Input validation and sanitization (SQL injection, XSS prevention)
- Automated security monitoring and alerting
- Regular security audits and penetration testing
- Automated backup with Point-in-Time Recovery

## Organizational Measures
- Staff trained on data protection principles
- Incident response plan with defined RTO/RPO
- Regular disaster recovery drills (quarterly)
- Data retention and deletion policies enforced
- Access to production systems logged and audited
- Background checks for personnel with data access
- Confidentiality agreements for all staff
- Regular compliance reviews
```

---

## 4. Consent Management

### 4.1 Consent at Signup

```python
# backend/app/api/v1/auth.py — Modified signup

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: str = Field(..., min_length=2)
    role: str = "reporter"
    country_id: UUID
    
    # Consent fields
    consent_data_processing: bool = Field(
        ...,
        description="I consent to OHAS processing my personal data for program management purposes",
    )
    consent_privacy_policy: bool = Field(
        ...,
        description="I have read and agree to the Privacy Policy",
    )


@router.post("/signup", response_model=Token)
async def signup(signup_data: SignupRequest):
    # Validate consent
    if not signup_data.consent_data_processing:
        raise HTTPException(
            400,
            "You must consent to data processing to use OHAS",
        )
    if not signup_data.consent_privacy_policy:
        raise HTTPException(
            400,
            "You must accept the Privacy Policy to use OHAS",
        )

    # Store consent record
    consent_record = {
        "user_id": str(auth_response.user.id),
        "consent_type": "data_processing",
        "consented_at": datetime.utcnow().isoformat(),
        "ip_address": request.client.host,
        "user_agent": request.headers.get("user-agent"),
        "privacy_policy_version": "1.0",
    }
    
    supabase.table("user_consents").insert(consent_record).execute()

    # ... rest of signup flow ...
```

### 4.2 Consent Database Table

```sql
-- supabase/migrations/20260404_consent_tracking.sql

CREATE TABLE user_consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    consent_type VARCHAR(50) NOT NULL,  -- data_processing, privacy_policy, marketing
    consented_at TIMESTAMPTZ DEFAULT NOW(),
    withdrawn_at TIMESTAMPTZ,
    ip_address INET,
    user_agent TEXT,
    privacy_policy_version VARCHAR(10),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_user_consents_user_id ON user_consents(user_id);
CREATE INDEX idx_user_consents_active ON user_consents(user_id) WHERE withdrawn_at IS NULL;

-- RLS
ALTER TABLE user_consents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own consents" ON user_consents
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "System can manage consents" ON user_consents
    FOR ALL USING (true) WITH CHECK (true);
```

### 4.3 Consent Withdrawal

```python
# backend/app/api/v1/profile.py

@router.post("/users/me/withdraw-consent")
async def withdraw_consent(
    consent_type: str = Body(..., embed=True),
    current_user: Profile = Depends(get_current_user),
):
    """
    Withdraw consent for data processing.
    This will deactivate the account and anonymize personal data.
    """
    # Record withdrawal
    supabase.table("user_consents")\
        .update({"withdrawn_at": datetime.utcnow().isoformat()})\
        .eq("user_id", str(current_user.id))\
        .eq("consent_type", consent_type)\
        .eq("withdrawn_at", None)\
        .execute()

    # Deactivate account
    supabase.table("profiles")\
        .update({"is_active": False})\
        .eq("id", str(current_user.id))\
        .execute()

    return {
        "message": f"Consent for '{consent_type}' withdrawn. Your account has been deactivated.",
        "note": "Organizational data (reports, audit logs) is retained but anonymized.",
    }
```

---

## 5. Data Subject Rights

### 5.1 Right to Access (DSAR)

```python
# backend/app/api/v1/profile.py

@router.get("/users/me/data-export")
async def export_my_data(current_user: Profile = Depends(get_current_user)):
    """
    Export all personal data for this user (GDPR Article 15).
    Returns a comprehensive JSON dump of all data associated with the user.
    """
    export_data = {
        "profile": await user_service.get_profile(current_user.id),
        "consents": supabase.table("user_consents")\
            .select("*")\
            .eq("user_id", str(current_user.id))\
            .execute().data,
        "reports_submitted": supabase.table("reports")\
            .select("*")\
            .eq("submitted_by", str(current_user.id))\
            .execute().data,
        "notifications": supabase.table("notifications")\
            .select("*")\
            .eq("user_id", str(current_user.id))\
            .execute().data,
        "audit_trail": supabase.table("audit_logs")\
            .select("action, created_at, new_data")\
            .eq("user_id", str(current_user.id))\
            .execute().data,
        "sessions": supabase.table("sessions")\
            .select("created_at, last_active_at, ip_address, user_agent")\
            .eq("user_id", str(current_user.id))\
            .execute().data,
    }

    return {
        "data": export_data,
        "meta": {
            "export_date": datetime.utcnow().isoformat(),
            "user_id": str(current_user.id),
            "format": "JSON",
            "note": "This is your personal data under GDPR Article 15 (Right of Access)",
        },
    }
```

### 5.2 Right to Deletion (Already Documented)

See `docs/enterprise-practices/03-data-management/04-data-retention-archival-policies.md` §5 for the deletion procedure.

---

## 6. Data Breach Notification Procedure

```
┌─────────────────────────────────────────────────────────────────┐
│              Data Breach Notification Timeline                   │
│                                                                 │
│  T+0: Breach detected                                           │
│  → Activate incident response (see DR runbooks)                 │
│  → Contain breach, preserve evidence                            │
│                                                                 │
│  T+24 hours: Internal notification                              │
│  → Notify all top_admins and developers                         │
│  → Assess scope: what data was affected?                        │
│  → Determine if personal data was involved                      │
│                                                                 │
│  T+48 hours: Legal assessment                                   │
│  → Consult legal counsel on notification obligations            │
│  → Identify affected countries and applicable laws              │
│  → Prepare notification content                                 │
│                                                                 │
│  T+72 hours: Regulatory notification (GDPR requirement)        │
│  → Kenya: Notify Office of Data Protection Commissioner         │
│  → Uganda: Notify Personal Data Protection Office              │
│  → Tanzania: Notify Data Protection Commission                  │
│  → Rwanda: Notify relevant authority                            │
│  → Other countries: Follow local requirements or GDPR standard  │
│                                                                 │
│  T+72 hours: User notification (if high risk)                  │
│  → Email all affected users                                     │
│  → Explain what happened, what data was affected                │
│  → Explain what we're doing about it                            │
│  → Provide contact for questions                                │
│                                                                 │
│  T+7 days: Follow-up report                                     │
│  → Document the breach and response                             │
│  → Report to management                                         │
│  → Update procedures to prevent recurrence                      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Children's Data Handling

```
OHAS reporters may collect data on program participants including minors.

Special protections for children's data:
─────────────────────────────────────────

1. Data Minimization:
   → Never store children's names — use anonymized IDs
   → Store only aggregate counts (e.g., "15 children under 12")
   → No photos of children without explicit parental consent

2. If participant names ARE stored:
   → Must have explicit consent from parent/guardian
   → Must be deletable on request
   → Must be anonymized after 2 years
   → Must NOT be included in data exports without consent

3. Reporter training:
   → Train reporters on children's data handling
   → Provide guidelines on what can and cannot be recorded
   → Regular compliance audits of reporter submissions

4. Database protection:
   → Participant names stored separately from other data
   → Additional RLS policies restrict access to participant names
   → Audit logging tracks all access to participant data
```

---

## 8. Compliance Checklist

```
┌─────────────────────────────────────────────────────────────────┐
│              OHAS Compliance Checklist                          │
│                                                                 │
│  Data Protection:                                               │
│  ☐ Privacy policy published and linked in app                   │
│  ☐ Data Processing Agreement signed with each country partner   │
│  ☐ Consent tracked and recorded at signup                       │
│  ☐ Right to access: data export endpoint functional             │
│  ☐ Right to deletion: deletion procedure tested                 │
│  ☐ Right to rectification: profile edit available               │
│  ☐ Data breach notification procedure documented and tested     │
│  ☐ Children's data handling policy published                    │
│  ☐ Data retention policies implemented and automated            │
│                                                                 │
│  Security:                                                      │
│  ☐ All data encrypted in transit (TLS)                          │
│  ☐ All data encrypted at rest (Supabase-managed)                │
│  ☐ MFA enabled for all admin roles                              │
│  ☐ Secrets rotated within 90-day schedule                       │
│  ☐ Access logs maintained and reviewed monthly                  │
│  ☐ Security audit conducted annually                            │
│  ☐ Penetration testing conducted annually                       │
│                                                                 │
│  Operational:                                                   │
│  ☐ DR drills conducted quarterly                                │
│  ☐ Backup restoration tested within last 3 months               │
│  ☐ Incident response plan documented and accessible             │
│  ☐ Post-incident review conducted for all P0/P1 incidents       │
│  ☐ Compliance reviewed annually by legal counsel                │
│                                                                 │
│  Country-Specific:                                              │
│  ☐ Kenya: DPO registered with ODPC                              │
│  ☐ Uganda: Registered with PDPO                                 │
│  ☐ Tanzania: Registered with DPC                                │
│  ☐ All countries: Cross-border transfer assessed                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 9. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Compliance baseline | GDPR-level for all countries | Covers strictest requirements, simplifies operations |
| Consent model | Explicit opt-in at signup | Required by Kenya, Uganda, Tanzania laws |
| Consent tracking | Database table with timestamps | Auditable, queryable |
| Data breach notification | 72 hours (GDPR standard) | Meets all country requirements |
| Children's data | Anonymized by default, names require consent | Protects minors, reduces liability |
| Data export | JSON endpoint | Machine-readable, user-accessible |
| DPA | Template signed with each partner | Legal protection, clarity on responsibilities |
| Sub-processor disclosure | Listed in DPA | Transparency requirement under most laws |
| Regulatory registration | Per-country where required | Kenya, Uganda, Tanzania require registration |
| Compliance audit | Annual | Standard practice, catches gaps early |

---

*This document should be updated when laws change in operating countries, when new countries are added, or after compliance audits.*
