import json
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.utils.html import format_html

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    FacilitySyncMetadata,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    OperatorITMSSession,
    OperatorProfile,
    SafeAuditScan,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
    VehicleInstallationPair,
)

User = get_user_model()


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("session_key", "get_decoded_user", "expire_date", "is_expired")
    search_fields = ("session_key",)
    list_filter = ("expire_date",)
    readonly_fields = ("session_key", "expire_date", "decoded_session_data")
    ordering = ("-expire_date",)

    @admin.display(description="User / Identity")
    def get_decoded_user(self, obj: Session) -> str:
        try:
            data = obj.get_decoded()
            user_id = data.get("_auth_user_id")
            if user_id:
                user = User.objects.filter(pk=user_id).first()
                if user:
                    return f"{user.username} ({user.get_full_name() or 'No Name'})"
            return "(Anonymous)"
        except Exception:
            return "(Invalid / Corrupted)"

    @admin.display(boolean=True, description="Expired")
    def is_expired(self, obj: Session) -> bool:
        return obj.expire_date < timezone.now()

    @admin.display(description="Decoded Session Data")
    def decoded_session_data(self, obj: Session) -> str:
        try:
            decoded = obj.get_decoded()
            return format_html("<pre>{}</pre>", json.dumps(decoded, indent=2, default=str))
        except Exception as exc:
            return f"Error decoding session data: {exc}"


@admin.register(OperatorProfile)
class OperatorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "active_facility", "get_allowed_facilities", "updated_at")
    list_filter = ("active_facility",)
    search_fields = ("user__username", "user__email", "active_facility")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Allowed Facilities")
    def get_allowed_facilities(self, obj: OperatorProfile) -> str:
        return ", ".join(obj.allowed_facilities or []) or "ALL"


@admin.register(OperatorITMSSession)
class OperatorITMSSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user", "itms_email", "itms_display_name", "active_bond_code",
        "is_authenticated", "expires_at", "last_verified_at",
    )
    list_filter = ("is_authenticated", "active_bond_code")
    search_fields = (
        "user__username", "itms_email", "itms_display_name",
        "itms_user_uuid", "active_bond_code", "active_bond_name",
    )
    readonly_fields = ("saved_at", "last_verified_at", "masked_cookies")

    @admin.display(description="Cookies (Masked)")
    def masked_cookies(self, obj: OperatorITMSSession) -> str:
        if not obj.cookies:
            return "None"
        keys = list(obj.cookies.keys())
        return f"{len(keys)} session cookies: {', '.join(keys)}"


@admin.register(FacilitySyncMetadata)
class FacilitySyncMetadataAdmin(admin.ModelAdmin):
    list_display = (
        "facility_code", "total_kits", "total_orders",
        "last_kits_synced_at", "last_orders_synced_at", "synced_by_operator",
    )
    search_fields = ("facility_code", "synced_by_operator")


@admin.register(IngestionBatch)
class IngestionBatchAdmin(admin.ModelAdmin):
    list_display = (
        "batch_id", "source_type", "source_label", "created_at",
        "total_files", "ingested_count", "duplicate_count", "failed_count",
    )
    list_filter = ("source_type", "created_at")
    search_fields = ("batch_id", "source_label")
    readonly_fields = ("id", "created_at")


@admin.register(InstallationOrder)
class InstallationOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "registration_number", "plate_serial", "tracker_id", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("order_number", "registration_number", "plate_serial", "tracker_id")


@admin.register(InstallationKit)
class InstallationKitAdmin(admin.ModelAdmin):
    list_display = ("kit_code", "registration_number", "front_plate", "rear_plate", "gps_tracker", "status", "created_date")
    list_filter = ("status", "warehouse")
    search_fields = ("kit_code", "registration_number", "front_plate", "rear_plate", "gps_tracker")


@admin.register(EvidenceImage)
class EvidenceImageAdmin(admin.ModelAdmin):
    list_display = (
        "id", "detected_plate", "orientation", "batch", "status",
        "is_file_pruned", "ocr_confidence", "ingested_at", "submitted_at",
    )
    list_filter = ("status", "orientation", "is_file_pruned", "batch")
    search_fields = ("detected_plate", "file_hash", "original_source_path", "batch__batch_id")
    readonly_fields = ("id", "file_hash", "ingested_at", "submitted_at", "pruned_at")


class AuditLogInline(admin.TabularInline):
    model = SubmissionAuditLog
    extra = 0
    readonly_fields = ("action", "result", "message", "simulated_token", "timestamp")
    can_delete = False


@admin.register(VehicleInstallationPair)
class VehicleInstallationPairAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number_detected", "order", "match_type", "match_score",
        "is_complete", "verification_status", "submitted_at", "updated_at",
    )
    list_filter = ("verification_status", "match_type", "is_complete")
    search_fields = ("registration_number_detected", "order__order_number")
    inlines = [AuditLogInline]


@admin.register(SubmissionAuditLog)
class SubmissionAuditLogAdmin(admin.ModelAdmin):
    list_display = ("pair", "action", "result", "timestamp")
    list_filter = ("action", "result")
    readonly_fields = [f.name for f in SubmissionAuditLog._meta.fields]


# ==========================================
# WAREHOUSE STOCK & RECONCILIATION ADMIN
# ==========================================

class StockDeliveryItemInline(admin.TabularInline):
    model = StockDeliveryItem
    extra = 0
    readonly_fields = ("registration_number", "plate_category", "plate_serial", "kit_code", "created_at")
    can_delete = False


@admin.register(StockDelivery)
class StockDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "delivery_number", "supplier", "plate_category",
        "total_plates_count", "delivery_date", "received_by", "created_at",
    )
    list_filter = ("plate_category", "supplier", "delivery_date")
    search_fields = ("delivery_number", "paper_note_reference", "supplier", "received_by")
    readonly_fields = ("created_at", "updated_at")
    inlines = [StockDeliveryItemInline]


@admin.register(StockBondTransfer)
class StockBondTransferAdmin(admin.ModelAdmin):
    list_display = (
        "transfer_number", "transfer_type", "plate_category",
        "other_bond_name", "plates_count", "transfer_date", "operator_name",
    )
    list_filter = ("transfer_type", "plate_category", "transfer_date")
    search_fields = ("transfer_number", "other_bond_name", "operator_name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StockDispatchScan)
class StockDispatchScanAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number", "bond_code", "plate_category",
        "status", "dispatched_by", "dispatched_at", "work_date",
    )
    list_filter = ("status", "plate_category", "bond_code", "work_date")
    search_fields = ("registration_number", "bond_code", "dispatched_by", "operator_name")
    readonly_fields = ("created_at",)


@admin.register(StockReturnScan)
class StockReturnScanAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number", "plate_category", "reason",
        "returned_by", "returned_at", "work_date",
    )
    list_filter = ("reason", "plate_category", "work_date")
    search_fields = ("registration_number", "returned_by", "operator_name", "reason")
    readonly_fields = ("created_at",)


@admin.register(SafeAuditScan)
class SafeAuditScanAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number", "plate_category", "bond_code",
        "scanned_by", "scanned_at", "work_date",
    )
    list_filter = ("plate_category", "bond_code", "work_date")
    search_fields = ("registration_number", "bond_code", "scanned_by", "operator_name")
    readonly_fields = ("created_at",)


@admin.register(DailyStockLedger)
class DailyStockLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "work_date", "warehouse_name", "opening_stock", "delivered_count",
        "installed_count", "closing_stock", "is_closed", "variance", "updated_at",
    )
    list_filter = ("is_closed", "warehouse_name", "work_date")
    search_fields = ("warehouse_name", "work_date")
    readonly_fields = ("created_at", "updated_at")
