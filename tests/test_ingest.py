"""Приём пакета: опасные архивы, имена файлов из Windows-архиваторов, типы файлов, info.xml из чужих рук."""

import zipfile

import pytest

from zerocycle.ingest.files import PackageError, open_package, sniff
from zerocycle.ingest.pde import read_info_xml
from zerocycle.model import Media

GUID_A = "0f8fad5b-d9cb-469f-a165-70867728950e"
GUID_B = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def test_zip_slip_is_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../evil.txt", "x")
    with pytest.raises(PackageError):
        open_package(archive, tmp_path / "out")


def test_cp866_names_from_windows_archivers(tmp_path):
    archive = tmp_path / "pkg.zip"
    name = "01 ПЗ/2345-ПЗ.pdf"

    class Cp866Info(zipfile.ZipInfo):  # имя в cp866 без флага UTF-8 — как пишут старые архиваторы
        def _encodeFilenameFlags(self):
            return self.filename.encode("cp866"), self.flag_bits & ~0x800

    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(Cp866Info(name), b"%PDF-1.4\n%%EOF\n")
    _root, files = open_package(archive, tmp_path / "out")
    assert [f.path for f in files] == [name]
    assert files[0].media == Media.pdf


def test_sniff_by_content_not_extension(tmp_path):
    xml = tmp_path / "a.pdf"
    xml.write_bytes(b"\xef\xbb\xbf<?xml version='1.0'?><a/>")
    assert sniff(xml) == Media.xml
    xlsx = tmp_path / "b.bin"
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/workbook.xml", "<x/>")
    assert sniff(xlsx) == Media.xlsx


def _info(tmp_path, body: str):
    p = tmp_path / "info.xml"
    p.write_text(body, encoding="utf-8")
    return read_info_xml(p, "info.xml", "0" * 64)


def test_info_xml_structure_and_checks(tmp_path):
    info = _info(
        tmp_path,
        f"""<?xml version="1.0" encoding="UTF-8"?>
<Package Created="2026-10-01T10:00:00" Organization="ООО «Проект»">
  <Project Id="{GUID_A}">
    <Attributes><Attribute Name="ProjectName" Value="Жилой дом"/><Attribute Name="Code" Value="2345"/></Attributes>
    <Children>
      <Folder Id="{GUID_B}" Type="Section">
        <Attributes><Attribute Name="Number" Value="1"/><Attribute Name="Code" Value="ПЗ"/></Attributes>
        <Children>
          <Document Id="{GUID_B}" FileName="01 ПЗ\\2345-ПЗ.pdf">
            <Attributes><Attribute Name="Code" Value="2345-ПЗ"/></Attributes>
          </Document>
          <Document Id="не-guid" FileName="01 ПЗ\\2345-ПЗ-С.pdf"/>
        </Children>
      </Folder>
      <Folder Type="Chapter"/>
    </Children>
  </Project>
</Package>""",
    )
    assert (info.project_name, info.project_code, info.project_guid) == ("Жилой дом", "2345", GUID_A)
    docs = [n for n in info.nodes if n.kind == "document"]
    assert docs[0].file_name == "01 ПЗ/2345-ПЗ.pdf"
    assert docs[0].parent == 0 and info.nodes[0].type == "Section"
    text = " | ".join(info.issues)
    assert "повтор GUID" in text and "не GUID" in text and "Chapter" in text and "нет Id" in text


def test_info_xml_entities_are_not_expanded(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("ТАЙНА", encoding="utf-8")
    info = _info(
        tmp_path,
        f"""<?xml version="1.0"?>
<!DOCTYPE Package [
  <!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY xxe SYSTEM "file:///{secret.as_posix()}">
]>
<Package><Project Id="{GUID_A}"><Attributes><Attribute Name="ProjectName" Value="&c;"/>
<Attribute Name="Code" Value="&xxe;"/></Attributes></Project></Package>""",
    )
    assert len(info.project_name or "") < 1000
    assert "ТАЙНА" not in (info.project_code or "")
