# Data Export & Import

**Document ID:** ENT-21  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), Supabase  

---

## 1. Current State

```
What exists:
  ✅ Data stored in structured PostgreSQL tables
  ✅ API endpoints return JSON data
  ✅ No bulk export endpoint
  ✅ No import functionality
  ✅ No data portability for users (GDPR requirement partially met via ENT-06)

What's missing:
  ✗ No bulk data export for admins (CSV, Excel, JSON)
  ✗ No data import for bulk migrations
  ✗ No scheduled export jobs
  ✗ No export format standardization
  ✗ No export size limits or throttling
```

---

## 2. Export Formats & Endpoints

### 2.1 Admin Bulk Export

```python
# backend/app/api/v1/export.py

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
import csv
import io
import json
from typing import Literal

router = APIRouter()


ExportFormat = Literal["csv", "json", "xlsx"]


class ExportRequest(BaseModel):
    """Request body for bulk data export."""
    resource: str = Field(..., description="Resource to export: reports, participants, users, goals")
    format: ExportFormat = Field(default="csv", description="Export format")
    filters: Optional[dict] = Field(default=None, description="Filters to apply (date range, country, etc.)")
    include_headers: Optional[list[str]] = Field(default=None, description="Specific columns to include")


@router.post("/export")
async def export_data(
    request: ExportRequest,
    current_user: Profile = Depends(get_current_user),
):
    """
    Export data in bulk.
    Available formats: CSV, JSON, XLSX
    Rate limited: 5 exports per minute per user.
    """
    # Check rate limit
    # ... rate limiting logic ...

    # Fetch data based on resource and filters
    data = await fetch_export_data(
        resource=request.resource,
        filters=request.filters,
        user=current_user,
    )

    if not data:
        raise HTTPException(404, f"No {request.resource} found matching filters")

    # Generate export file
    if request.format == "csv":
        return generate_csv_export(data, request.resource)
    elif request.format == "json":
        return generate_json_export(data, request.resource)
    elif request.format == "xlsx":
        return generate_xlsx_export(data, request.resource)


def generate_csv_export(data: list, resource: str) -> StreamingResponse:
    """Generate a CSV download response."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    for row in data:
        writer.writerow(row)
    
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="ohas_{resource}_export.csv"',
        },
    )


def generate_json_export(data: list, resource: str) -> StreamingResponse:
    """Generate a JSON download response."""
    output = json.dumps({
        "resource": resource,
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(data),
        "data": data,
    }, indent=2, default=str)
    
    return StreamingResponse(
        iter([output]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="ohas_{resource}_export.json"',
        },
    )
```

### 2.2 User Data Export (GDPR Right to Access)

See `docs/enterprise-practices/06-compliance-security/06-compliance-audit-standards.md` §5.1 for the personal data export endpoint.

---

## 3. Data Import

### 3.1 Bulk Import Endpoint

```python
# backend/app/api/v1/import.py

from fastapi import APIRouter, Depends, UploadFile, File
import csv
import json

router = APIRouter()


@router.post("/import/{resource}")
async def import_data(
    resource: str,
    file: UploadFile = File(...),
    current_user: Profile = Depends(check_role(["top_administrator", "country_admin"])),
):
    """
    Import data from CSV or JSON file.
    
    Supported resources: participants, reports (bulk), users (bulk invite)
    File format: CSV (with header row) or JSON (array of objects)
    
    The import is validated before any data is written:
    1. File format validated
    2. Headers/keys validated against schema
    3. Each row validated against business rules
    4. If ANY row fails, entire import is rejected
    5. On success, all rows are inserted atomically
    """
    # Validate resource type
    valid_resources = ["participants", "users", "reports"]
    if resource not in valid_resources:
        raise HTTPException(400, f"Import not supported for: {resource}")

    # Read and parse file
    content = await file.read()
    
    if file.filename.endswith(".csv"):
        records = parse_csv(content)
    elif file.filename.endswith(".json"):
        records = parse_json(content)
    else:
        raise HTTPException(400, "Unsupported file format. Use CSV or JSON.")

    # Validate all records before importing
    validation_errors = []
    for i, record in enumerate(records):
        errors = validate_import_record(resource, record)
        if errors:
            validation_errors.append({"row": i + 1, "errors": errors})

    if validation_errors:
        raise HTTPException(
            400,
            content={
                "error": "Import validation failed",
                "total_records": len(records),
                "valid_records": len(records) - len(validation_errors),
                "invalid_records": len(validation_errors),
                "errors": validation_errors[:10],  # First 10 errors
            },
        )

    # All valid — import
    import_result = await execute_import(resource, records, current_user)

    return {
        "message": f"Successfully imported {len(records)} {resource}",
        "imported_count": import_result["count"],
        "resource": resource,
    }
```

### 3.2 Import Validation

```python
def validate_participant_import(record: dict) -> list:
    """Validate a single participant import record."""
    errors = []
    
    # Required fields
    required_fields = ["full_name", "country_id", "program_id"]
    for field in required_fields:
        if not record.get(field):
            errors.append(f"Missing required field: {field}")
    
    # Validate country_id exists
    if record.get("country_id"):
        country = supabase.table("countries")\
            .select("id")\
            .eq("id", record["country_id"])\
            .single()\
            .execute()
        if not country.data:
            errors.append(f"Country not found: {record['country_id']}")
    
    # Validate program_id exists
    if record.get("program_id"):
        program = supabase.table("programs")\
            .select("id")\
            .eq("id", record["program_id"])\
            .single()\
            .execute()
        if not program.data:
            errors.append(f"Program not found: {record['program_id']}")
    
    return errors
```

---

## 4. Scheduled Exports

```python
# backend/app/worker.py — Scheduled export tasks

@celery_app.task(name="monthly_country_report_export")
async def monthly_country_report_export():
    """
    Generate and email monthly report exports to country admins.
    Runs on the 1st of each month at 6:00 AM.
    """
    countries = supabase.table("countries").select("*").execute()
    
    for country in countries.data:
        # Fetch last month's reports
        reports = supabase.table("reports")\
            .select("*")\
            .eq("country_id", str(country["id"]))\
            .gte("created_at", (datetime.utcnow() - timedelta(days=30)).isoformat())\
            .execute()
        
        if not reports.data:
            continue
        
        # Generate CSV
        csv_content = generate_csv_string(reports.data)
        
        # Email to country admins
        admins = supabase.table("profiles")\
            .select("email")\
            .eq("country_id", str(country["id"]))\
            .eq("role", "country_admin")\
            .execute()
        
        for admin in admins.data:
            await email_service.send_email(
                to_email=admin["email"],
                subject=f"Monthly Report Export — {country['name']} — {datetime.now().strftime('%B %Y')}",
                body=f"Please find attached the monthly report export for {country['name']}.",
                attachments=[{
                    "filename": f"{country['name']}_reports_{datetime.now().strftime('%Y_%m')}.csv",
                    "content": csv_content,
                    "content_type": "text/csv",
                }],
            )
```

---

## 5. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Export formats | CSV, JSON, XLSX | CSV for spreadsheet, JSON for systems, XLSX for Excel users |
| Export rate limit | 5 per minute | Prevents abuse, sufficient for admin needs |
| Export max size | 10,000 rows per export | Prevents memory issues, pagination for larger datasets |
| Import validation | All-or-nothing (fail fast) | Prevents partial imports, clean error reporting |
| Import file size | Max 10MB | Reasonable for bulk data, prevents abuse |
| Scheduled exports | Monthly to country admins | Regular reporting cadence |
| User data export | JSON via API endpoint (GDPR) | Machine-readable, comprehensive |
| Content-Disposition | Attachment with descriptive filename | Browser downloads with correct name |
| Authentication | Same as resource being exported | Country admins export country data, etc. |

---

*This document should be updated when new export formats are added or when import schemas change.*
