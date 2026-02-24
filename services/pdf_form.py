"""PDFFormManager — PDF AcroForm filler with appearance stream generation.

Uses pypdf's update_page_form_field_values() for text fields to generate proper
appearance streams (/AP), so filled values render in ALL PDF viewers including
Chrome's embedded viewer (which ignores /NeedAppearances).

Checkboxes are handled separately via direct annotation writes since pypdf's
update method doesn't handle them well.
"""

import io
import logging
import requests
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

logger = logging.getLogger(__name__)


class PDFFormManager:
    def __init__(self, source_path_or_url):
        self.fields_map = {}

        if source_path_or_url.startswith("http"):
            logger.info("Downloading from: %s...", source_path_or_url)
            response = requests.get(source_path_or_url)
            self.stream = io.BytesIO(response.content)
        else:
            self.stream = open(source_path_or_url, "rb")

        self.reader = PdfReader(self.stream)
        self._init_fields_mapping()

    def _init_fields_mapping(self):
        try:
            raw_fields = self.reader.get_fields()
        except Exception:
            raw_fields = None

        if raw_fields:
            for field_id, field_data in raw_fields.items():
                field_type = field_data.get("/FT", "/Tx")
                self.fields_map[field_id] = {"type": field_type, "value": None}
        else:
            logger.warning("No fields found.")

    def generate_pdf(self, output_filename):
        writer = PdfWriter(clone_from=self.reader)

        data_to_write = {
            k: v["value"] for k, v in self.fields_map.items() if v["value"] is not None
        }

        if not data_to_write:
            with open(output_filename, "wb") as f:
                writer.write(f)
            logger.info("PDF saved to: %s (no fields to fill)", output_filename)
            return

        # Split into text fields and checkboxes
        field_types = {k: v["type"] for k, v in self.fields_map.items()}
        text_fields = {k: v for k, v in data_to_write.items() if field_types.get(k, "/Tx") != "/Btn"}
        checkbox_fields = {k: v for k, v in data_to_write.items() if field_types.get(k) == "/Btn"}

        # --- Text fields: use update_page_form_field_values() ---
        # This generates proper /AP appearance streams so values render
        # in Chrome's embedded PDF viewer (not just /NeedAppearances).
        if text_fields:
            for page in writer.pages:
                writer.update_page_form_field_values(page, text_fields)

        # --- Checkboxes: write directly to annotation objects ---
        # pypdf's update method doesn't handle checkboxes well,
        # so we set /V and /AS manually.
        if checkbox_fields:
            for page in writer.pages:
                if "/Annots" not in page:
                    continue
                for annot in page["/Annots"]:
                    obj = annot.get_object()
                    field_name = str(obj.get("/T", ""))
                    if field_name not in checkbox_fields:
                        continue
                    value = checkbox_fields[field_name]
                    obj[NameObject("/V")] = NameObject(value)
                    obj[NameObject("/AS")] = NameObject(value)

        # Also set /NeedAppearances as fallback for viewers that support it
        if "/AcroForm" in writer.root_object:
            acro_form = writer.root_object["/AcroForm"]
            if hasattr(acro_form, "get_object"):
                acro_form = acro_form.get_object()
            acro_form[NameObject("/NeedAppearances")] = BooleanObject(True)

        with open(output_filename, "wb") as f:
            writer.write(f)

        logger.info("PDF saved to: %s", output_filename)
