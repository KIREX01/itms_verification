from typing import Optional
from textual.widgets import DataTable
from core.models import VehicleInstallationPair, EvidenceImage, IngestionBatch
from core.tui.inspectors import InspectorPane

class NavigationHandlersMixin:
    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "table-queue":
            self._update_queue_inspector(event.row_key)
        elif event.data_table.id == "table-history":
            self._update_history_inspector(event.row_key)
        elif event.data_table.id == "table-batches":
            batch_id = event.row_key.value if event.row_key else None
            if batch_id and batch_id != self._selected_batch_id:
                self._selected_batch_id = batch_id
                self._reload_batch_images_table(batch_id)
            self._update_batch_inspector(event.row_key)
        elif event.data_table.id == "table-batch-images":
            if event.row_key and event.row_key.value:
                self._selected_image_id = event.row_key.value
            self._update_batch_image_inspector(event.row_key)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id in ("table-batch-images", "table-queue", "table-history"):
            self.action_view_evidence()

    def _get_active_pair(self, table_id: str) -> Optional[VehicleInstallationPair]:
        table = self.query_one(f"#{table_id}", DataTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            pair_id = row_key.value
            return VehicleInstallationPair.objects.filter(id=pair_id).select_related(
                "order", "front_image", "rear_image"
            ).first()
        except Exception:
            return None

    def _get_active_batch_image(self) -> Optional[EvidenceImage]:
        table = self.query_one("#table-batch-images", DataTable)
        img_id = None
        if table.cursor_row is not None and table.row_count > 0:
            try:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                img_id = row_key.value
            except Exception:
                pass
        if not img_id and self._selected_image_id:
            img_id = self._selected_image_id
        if not img_id and table.row_count > 0:
            try:
                cell_key = table.coordinate_to_cell_key((0, 0))
                img_id = cell_key.row_key.value
            except Exception:
                pass

        if img_id:
            return EvidenceImage.objects.filter(id=img_id).first()
        return None

    def _update_queue_inspector(self, row_key=None):
        try:
            inspector = self.query_one("#inspector-queue", InspectorPane)
        except Exception:
            return
        pair = None
        if row_key and row_key.value:
            pair = VehicleInstallationPair.objects.filter(id=row_key.value).select_related(
                "order", "front_image", "rear_image"
            ).first()
        else:
            pair = self._get_active_pair("table-queue")
        inspector.show_pair(pair)

    def _update_history_inspector(self, row_key=None):
        try:
            inspector = self.query_one("#inspector-history", InspectorPane)
        except Exception:
            return
        if self.current_history_filter == "AUDIT_LOGS":
            inspector.update("[dim]Direct audit logs view. Select pair filter to inspect individual trails.[/dim]")
            return

        pair = None
        if row_key and row_key.value:
            pair = VehicleInstallationPair.objects.filter(id=row_key.value).select_related(
                "order", "front_image", "rear_image"
            ).first()
        else:
            pair = self._get_active_pair("table-history")
        inspector.show_audit_history(pair)

    def _update_batch_inspector(self, row_key=None):
        try:
            inspector = self.query_one("#inspector-batches", InspectorPane)
        except Exception:
            return
        batch = None
        batch_id = row_key.value if row_key and row_key.value else None
        if not batch_id:
            batch_id = self._selected_batch_id

        if batch_id:
            batch = IngestionBatch.objects.filter(batch_id=batch_id).first()
        inspector.show_batch_info(batch)

    def _update_batch_image_inspector(self, row_key=None):
        try:
            inspector = self.query_one("#inspector-batches", InspectorPane)
        except Exception:
            return
        img = None
        if row_key and row_key.value:
            img = EvidenceImage.objects.filter(id=row_key.value).first()
        else:
            img = self._get_active_batch_image()
        if img:
            inspector.show_image_vision_analysis(img)
