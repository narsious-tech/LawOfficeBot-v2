AJAY AI — DOCUMENT INTELLIGENCE DELIVERY

Changed/new file:
  services/ai_document_intelligence_service.py

Integration instructions:
  AI_DOCUMENT_INTELLIGENCE_PATCH.txt

Purpose
-------
Adds a fail-safe verified document-context layer for Ajay AI. It finds documents
for the selected case using the existing case identifiers and case_files index.

It will include extracted/OCR text only when an appropriate text column actually
exists in the deployed database. Otherwise it explicitly reports that full
document text is unavailable. This prevents the AI from claiming it has read a
pleading/order merely because a Google Drive file is indexed.

Why commands/ai.py is not replaced in this package
---------------------------------------------------
The current repository snapshot shows commands/ai.py, but the exact object
returned by OfficeKnowledgeService.build_case_context() must be matched before
hard-wiring the CaseSummary attribute. Replacing that production file without
verifying the object shape could break /ai.

The included patch identifies the small integration point safely.
