"""Модель пакета проектной документации: что движок знает о пакете и что он о нём говорит.

Координаты — в пунктах PDF (1 пт = 1/72 дюйма) от левого верхнего угла страницы, ось y вниз, как
в pdfplumber: рамка = (x0, top, x1, bottom). Миллиметры живут только в профилях основных надписей
и в генераторе засеянных пакетов (`zerocycle.units`).

Правило ядра: у каждого факта есть якорь, без факта нет находки (docs/adr/0004-rules-evidence-grounding.md).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

BBox = tuple[float, float, float, float]  # x0, top, x1, bottom в пт


class Media(StrEnum):
    """Тип файла по содержимому, а не по расширению."""

    pdf = "pdf"
    xlsx = "xlsx"
    docx = "docx"
    xml = "xml"
    ifc = "ifc"
    zip = "zip"
    other = "other"


class Anchor(BaseModel):
    """Где в пакете лежит факт: файл, страница и рамка; путь в XML; ячейка; GUID элемента модели."""

    file: str  # путь внутри пакета через «/»
    sha256: str
    page: int | None = None  # с 1
    bbox: BBox | None = None
    xpath: str | None = None
    cell: str | None = None
    guid: str | None = None
    quote: str | None = None  # текст по якорю — для grounding-гейта отчёта


class Designation(BaseModel):
    """Разобранное обозначение документа по ГОСТ Р 21.101-2020 (4.1.2–4.1.3, 4.2.4–4.2.5, 8.1.5–8.1.6).

    Разбор нейтральный: «2345-ИОС4.1.2» → шифр ИОС, номера (4, 1, 2). Подраздел, часть и книга это
    или марка, комплект и документ — решает тот, кто знает стадию и состав пакета
    (`zerocycle.extract.designation.interpret`).
    """

    raw: str  # как написано
    base: str  # базовое обозначение: «2345», «2345-12»
    code: str | None = None  # шифр раздела или марка, кириллицей; None — у «Состава ПД»
    numbers: tuple[int, ...] = ()  # ИОС4.1.2 → (4, 1, 2); КЖ1 → (1,)
    doc_number: int | None = None  # ЭО.1 → 1: номер документа через точку сразу после шифра
    tail: str | None = None  # хвост по стандарту организации: «АР.ГЧ» → «ГЧ»
    suffix: Literal["С", "СП"] | None = None  # «С» — содержание тома, «СП» — состав ПД
    issues: tuple[str, ...] = ()  # что не так с записью: латиница в шифре, неизвестный шифр…

    @property
    def core(self) -> str:
        """Нормализованная запись без суффикса: «2345-ИОС4.1.2»."""
        if self.code is None:
            return self.base
        tok = self.code + ".".join(str(n) for n in self.numbers)
        if self.doc_number is not None:
            tok += f".{self.doc_number}"
        if self.tail:
            tok += f".{self.tail}"
        return f"{self.base}-{tok}"

    @property
    def text(self) -> str:
        """Нормализованная запись целиком: «2345-ПЗУ2-С», «2345-СП»."""
        return f"{self.core}-{self.suffix}" if self.suffix else self.core


class SectionRef(BaseModel):
    """Место документа в составе ПД: раздел по ПП 87, подраздел, часть, книга."""

    number: int | None = None  # номер раздела по ПП 87
    code: str | None = None  # шифр раздела
    subsection: int | None = None  # подраздел (раздел 5 — ИОС1…ИОС6)
    part: int | None = None
    book: int | None = None


class SignRow(BaseModel):
    """Строка подписей основной надписи: графы 10–13."""

    role: str  # графа 10: «Разраб.», «Н. контр.», «ГИП»…
    name: str  # графа 11 — фамилия
    date: str  # графа 13 — как написано
    bbox: BBox


class ChangeRow(BaseModel):
    """Строка таблицы изменений в основной надписи: графы 14–19 (18 — подпись, текстом не читается)."""

    change: str  # 14 — номер изменения
    count: str  # 15 — количество участков
    sheet: str  # 16 — лист
    doc: str  # 17 — номер документа
    date: str  # 19
    bbox: BBox


class TitleBlock(BaseModel):
    """Основная надпись листа по ГОСТ Р 21.101-2020, прил. Ж."""

    form: int  # 3–6
    bbox: BBox  # рамка основной надписи на странице
    graphs: dict[int, str] = Field(default_factory=dict)  # графа → текст (1–9, 23–27)
    cells: dict[int, BBox] = Field(default_factory=dict)  # графа → рамка ячейки — для якорей
    signatures: list[SignRow] = Field(default_factory=list)
    changes: list[ChangeRow] = Field(default_factory=list)
    extra: list[str] = Field(default_factory=list)  # тексты ячеек, не сопоставленных с графами
    # grid — от строки шапки «Изм. … Дата» по фактическим линиям; frame — по геометрии ГОСТ у угла рамки;
    # labels — по геометрии ГОСТ от шапки без линий
    method: Literal["grid", "frame", "labels"]
    scale: float = 1.0  # ширина левой части к 65 мм ГОСТ: печать с уменьшением, нестандартные шаблоны
    confidence: float

    def get(self, graph: int) -> str:
        return self.graphs.get(graph, "")


class Page(BaseModel):
    number: int  # с 1
    width: float  # пт
    height: float
    paper: str | None = None  # формат по ГОСТ 2.301: A4, A3, A4x3…; None — нестандартный
    landscape: bool = False
    has_text: bool = True  # есть текстовый слой; нет — скан или текст переведён в кривые: нужен OCR
    title_block: TitleBlock | None = None


class PdeNode(BaseModel):
    """Папка или документ из info.xml ПДЭ (ГОСТ Р 21.101-2020, прил. Ф)."""

    kind: Literal["folder", "document"]
    guid: str | None = None
    type: str | None = None  # у папки: Section, Subsection, Set
    attrs: dict[str, str] = Field(
        default_factory=dict
    )  # папка: Number, Code, Title; документ: Code, DocumentName, Sheet
    file_name: str | None = None  # у документа: путь внутри пакета через «/»
    created: str | None = None
    parent: int | None = None  # индекс родительской папки в PdeInfo.nodes
    xpath: str


class PdeInfo(BaseModel):
    """Реквизитная часть ПДЭ — info.xml."""

    file: str
    sha256: str
    created: str | None = None
    organization: str | None = None
    project_guid: str | None = None
    project_name: str | None = None
    project_code: str | None = None
    nodes: list[PdeNode] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class Document(BaseModel):
    """Файл пакета. Логические документы внутри файла (титул, содержание, части) — по основным надписям листов."""

    path: str  # внутри пакета через «/»
    sha256: str
    size: int
    media: Media
    pages: list[Page] = Field(default_factory=list)
    designation: Designation | None = None
    title: str | None = None
    guid: str | None = None  # из info.xml
    section: SectionRef | None = None
    issues: list[str] = Field(default_factory=list)  # что не удалось прочитать


class ObjectKind(StrEnum):
    nonlinear = "nonlinear"  # объекты производственного и непроизводственного назначения
    linear = "linear"


class WorkKind(StrEnum):
    new = "new"
    reconstruction = "reconstruction"
    capital_repair = "capital_repair"
    demolition = "demolition"


class Package(BaseModel):
    root: str  # как пакет назван при приёме: папка или zip
    jurisdiction: str = "ru"
    stage: Literal["П", "Р"] = "П"
    object_kind: ObjectKind | None = None
    work_kind: WorkKind | None = None
    submitted: date | None = None  # дата подачи — по ней выбирается действующая редакция норм
    base_designation: str | None = None
    documents: list[Document] = Field(default_factory=list)
    pde: PdeInfo | None = None
    issues: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    kind: str  # «tep.total_area», «object.name», «designation», «normref»…
    value: str | float | int | None
    unit: str | None = None
    anchor: Anchor
    extractor: str  # кто извлёк: «titleblock», «table:tep», «llm:tep@1»…
    confidence: float = 1.0


class NormRef(BaseModel):
    text: str  # как написано: «СП 54.13330.2016»
    kind: str | None = None  # СП, ГОСТ, ГОСТ Р, СНиП, СанПиН, ФЗ, ПП
    number: str | None = None  # «54.13330.2016»
    registry_id: int | None = None  # запись реестра «Стройкомплекс.РФ»
    status: str | None = None  # действует / нет в реестре / не действует
    anchor: Anchor


class Family(StrEnum):
    """Семейства правил = категории замечаний (`bim-data/taxonomies/remark_and_defect_taxonomy.json`)."""

    completeness = "completeness"
    formatting = "formatting"
    consistency = "consistency"
    norms = "norms"
    calculation = "calculation"
    estimate = "estimate"
    submission_format = "submission_format"


class Severity(StrEnum):
    blocker = "blocker"
    major = "major"
    minor = "minor"
    info = "info"


class Status(StrEnum):
    violated = "violated"  # нарушено
    ok = "ok"  # норма — для отчёта о покрытии
    manual = "manual"  # проверить вручную: графика, инженерное суждение
    cannot_check = "cannot_check"  # проверить невозможно: нет входа, нечитаемо


class Finding(BaseModel):
    rule: str
    rule_version: str
    family: Family
    severity: Severity
    status: Status
    message: str
    anchors: list[Anchor] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)  # сравниваемые значения
    basis: list[str] = Field(default_factory=list)  # «ГОСТ Р 21.101-2020, 5.2.2»
    confidence: float = 1.0


class Decision(BaseModel):
    finding: str  # ключ находки: правило + якорь
    accepted: bool
    reason: str | None = None
    by: str | None = None
    at: datetime | None = None


class Run(BaseModel):
    engine: str
    packs: dict[str, str] = Field(default_factory=dict)
    norm_index: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    started: datetime
    finished: datetime | None = None
    cost_usd: float | None = None
