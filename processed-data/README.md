# processed-data — Gas Generator tender dataset

Segregated copies of `data/procurement-data`. **No processing, no renaming, no content
extraction** — files are byte-identical to the source (verified by SHA-256 in
`manifest.csv`). Originals are untouched.

Package: ADN-AEC-ME-SPC-026 — Gas Genset (client: ASTRA / ADNOC-side MR).

## Layout

```
processed-data/
├── 01-client-mr-rfq/      client-issued MR, datasheet and the MOM that revised it
├── 02-vendor-bids/
│   ├── ADPOWER/           3 files
│   ├── AESL/              1 file
│   ├── KERUI/             20 files
│   └── MKON/              6 files
├── 03-evaluation/         client/internal comparative statement
└── manifest.csv           dataset_path → source_path, size, sha256
```

37 files, ~44 MB.

## Classification rules used

| Bucket | Rule |
|---|---|
| `01-client-mr-rfq` | Documents originating from the client that define the scope of supply: the MR specification (`ADN-AEC-ME-SPC-026`), the blank/base datasheet (`DOD-30201-50150-BH-000-16-00-004`), and the 2024-11-11 MOM that superseded the MR. |
| `02-vendor-bids` | Everything a vendor submitted back, one folder per vendor — quotations, BOMs, deviation forms, filled/commented datasheets, drawings, spares and consumption lists. |
| `03-evaluation` | Comparative statement (CS) Rev.2 — downstream evaluation output, neither client input nor vendor submission. |

Notes on judgement calls:

- The client MR and base datasheet were physically stored inside vendor folders
  (ADPOWER, MKON) because each vendor was issued a copy. They are classified by
  **origin**, not by which folder they sat in, so they live in `01-client-mr-rfq`.
- Vendor-returned copies of client templates (e.g. KERUI
  `Attachment-1 DataSheet Gas Generator commented.pdf`, KERUI `01 DOD-...DataSheet
  Gas Generator.pdf`, MKON `Attachment-2 Vendor Deviation Form.pdf`) stay under the
  submitting vendor — they carry that vendor's filled-in values.
- No standalone RFQ/ITB cover document exists in the source set; the MR + datasheet
  are the enquiry package.
- ADPOWER `ADP-13158-2024-935.pdf` and `...(Rev1).pdf` are both kept — Rev1 is the
  later revision of the same quotation.

## Regenerating

The dataset is a pure copy; delete the folder and re-copy from `data/procurement-data`
following the table above. `manifest.csv` records every source path.

This folder is gitignored — it holds confidential client tender documents.
