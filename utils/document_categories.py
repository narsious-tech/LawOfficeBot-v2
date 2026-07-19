"""Canonical document categories and versions for the LawOfficeBot DMS."""

DOCUMENT_CATEGORIES = {
    "PLEADINGS": "Pleadings",
    "ORDERS": "Orders",
    "EVIDENCE": "Evidence",
    "JUDGMENTS": "Judgments",
    "CORRESPONDENCE": "Correspondence",
    "MISCELLANEOUS": "Miscellaneous",
}

DOCUMENT_VERSIONS = {
    "DRAFT": "Draft",
    "FINAL": "Final",
    "REVISED": "Revised",
    "SIGNED": "Signed",
    "CERTIFIED_COPY": "Certified_Copy",
}

CATEGORY_ALIASES = {
    "pleading": "PLEADINGS", "pleadings": "PLEADINGS",
    "order": "ORDERS", "orders": "ORDERS",
    "evidence": "EVIDENCE", "evidences": "EVIDENCE",
    "judgment": "JUDGMENTS", "judgments": "JUDGMENTS",
    "judgement": "JUDGMENTS", "judgements": "JUDGMENTS",
    "correspondence": "CORRESPONDENCE", "letter": "CORRESPONDENCE", "letters": "CORRESPONDENCE",
    "misc": "MISCELLANEOUS", "miscellaneous": "MISCELLANEOUS",
}


def normalize_document_category(value: str) -> str:
    if not value:
        return "MISCELLANEOUS"
    upper = value.strip().upper()
    if upper in DOCUMENT_CATEGORIES:
        return upper
    return CATEGORY_ALIASES.get(value.strip().lower(), "MISCELLANEOUS")


def category_folder_name(category: str) -> str:
    return DOCUMENT_CATEGORIES.get(category, "Miscellaneous")


def normalize_document_version(value: str) -> str:
    value = (value or "FINAL").strip().upper().replace(" ", "_")
    return value if value in DOCUMENT_VERSIONS else "FINAL"


def version_label(version: str) -> str:
    return DOCUMENT_VERSIONS.get(normalize_document_version(version), "Final")
