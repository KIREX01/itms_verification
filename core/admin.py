from django.contrib import admin

from core.models import (
    EvidenceImage,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)


@admin.register(InstallationOrder)
class InstallationOrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "registration_number", "plate_serial", "tracker_id", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("order_number", "registration_number", "plate_serial", "tracker_id")


@admin.register(EvidenceImage)
class EvidenceImageAdmin(admin.ModelAdmin):
    list_display = (
        "id", "detected_plate", "orientation", "orientation_confidence",
        "ocr_confidence", "status", "ingested_at",
    )
    list_filter = ("status", "orientation")
    search_fields = ("detected_plate", "file_hash", "original_source_path")
    readonly_fields = ("id", "file_hash", "ingested_at")


class AuditLogInline(admin.TabularInline):
    model = SubmissionAuditLog
    extra = 0
    readonly_fields = ("action", "result", "message", "simulated_token", "timestamp")
    can_delete = False


@admin.register(VehicleInstallationPair)
class VehicleInstallationPairAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number_detected", "order", "match_type", "match_score",
        "is_complete", "verification_status", "updated_at",
    )
    list_filter = ("verification_status", "match_type", "is_complete")
    search_fields = ("registration_number_detected", "order__order_number")
    inlines = [AuditLogInline]


@admin.register(SubmissionAuditLog)
class SubmissionAuditLogAdmin(admin.ModelAdmin):
    list_display = ("pair", "action", "result", "timestamp")
    list_filter = ("action", "result")
    readonly_fields = [f.name for f in SubmissionAuditLog._meta.fields]
