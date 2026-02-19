"""PDFFormManager — extracted from form_interactions.ipynb for importability."""

import io
import requests
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject


class PDFFormManager:
    def __init__(self, source_path_or_url):
        self.reader = None
        self.fields_map = {}

        if source_path_or_url.startswith("http"):
            print(f"Downloading from: {source_path_or_url}...")
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
            print("No fields found.")

    def set_value(self, field_id, value):
        if field_id in self.fields_map:
            self.fields_map[field_id]["value"] = value
        else:
            pass  # Silently skip missing fields (dual UHC/BCBS support)

    def get_fields(self):
        return dict(self.fields_map)

    def generate_pdf(self, output_filename):
        writer = PdfWriter()
        writer.append_pages_from_reader(self.reader)

        if "/AcroForm" in self.reader.root_object:
            writer.root_object.update(
                {NameObject("/AcroForm"): self.reader.root_object["/AcroForm"]}
            )

        data_to_write = {
            k: v["value"] for k, v in self.fields_map.items() if v["value"] is not None
        }

        for page in writer.pages:
            writer.update_page_form_field_values(page, data_to_write)

        if "/AcroForm" in writer.root_object:
            acro_form = writer.root_object["/AcroForm"]
            if hasattr(acro_form, "get_object"):
                acro_form = acro_form.get_object()
            acro_form.update({NameObject("/NeedAppearances"): BooleanObject(True)})

        with open(output_filename, "wb") as output_stream:
            writer.write(output_stream)

        print(f"PDF saved to: {output_filename}")
