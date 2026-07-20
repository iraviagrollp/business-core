"""Temp smoke test for po_pdf.render_po_pdf — deleted after use."""
import io
import po_pdf
from pypdf import PdfReader


def page_count(pdf_bytes):
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


BULK_PO = {
    'po_type': 'BULK',
    'po_no': 'IAL/2627/1',
    'po_date': '2026-07-20',
    'supplier_company_name': 'ACME AGRO CHEMICALS PVT LTD',
    'supplier_address_line1': 'Plot 12, Industrial Area',
    'supplier_address_line2': 'Phase II',
    'supplier_address_line3': 'Hyderabad, Telangana',
    'supplier_gstin': '36AAECA1439J1ZR',
    'technical_name': 'ACETAMIPRID 20% SP',
    'quantity': 5000,
    'quantity_unit': 'KGS',
    'rate': 250,
    'gst_rate': 18,
    'amount': 1250000,
    'gst_amount': 225000,
    'total_value': 1475000,
    'terms': '30 days credit',
    'dispatch': 'Within 7 days',
    'transport': "Vendor's Own",
    'bill_to_company_name': 'IRAVI AGRO LIFE LLP',
    'bill_to_address_line1': 'Flat No. 102, BVR Plaza',
    'bill_to_state': 'Telangana',
    'bill_to_gstin': '37AALFI2946J1ZY',
    'ship_to_company_name': 'IRAVI AGRO LIFE LLP - Warehouse',
    'ship_to_address_line1': 'Warehouse Road',
    'note': 'Please confirm dispatch schedule in advance.',
    'signatory_name': 'K. Ramesh',
    'signatory_title': 'Procurement Head',
    'signatory_department': 'Procurement',
}


def job_work_po(n_items, include_terms=True):
    items = []
    for i in range(n_items):
        items.append({
            'technical_id': i + 1,
            'technical_name': f'TECHNICAL COMPOUND {i+1}',
            'brand_name': f'BRAND{i+1}',
            'packaging': '25 KG BAG',
            'quantity': 100 + i,
            'rate': 200 + i,
            'amount': (100 + i) * (200 + i),
        })
    return {
        'po_type': 'JOB_WORK',
        'po_no': f'IAL/2627/{n_items}',
        'po_date': '2026-07-20',
        'supplier_company_name': 'JOB WORKER INDUSTRIES',
        'supplier_address_line1': 'Sy No. 45, Industrial Estate',
        'supplier_address_line2': 'Patancheru',
        'supplier_gstin': '36AAECA1439J1ZR',
        'technical_name': 'ACETAMIPRID 20% SP',
        'brand_name': 'ACEPRIDE',
        'quantity': 5.00,
        'quantity_unit': 'TONNE',
        'gst_rate': 18,
        'terms': '30 days credit',
        'dispatch': 'Within 7 days',
        'transport': "Vendor's Own",
        'bill_to_company_name': 'IRAVI AGRO LIFE LLP',
        'bill_to_address_line1': 'Flat No. 102, BVR Plaza',
        'ship_to_company_name': 'IRAVI AGRO LIFE LLP - Warehouse',
        'note': 'Please confirm dispatch schedule in advance.',
        'signatory_name': 'K. Ramesh',
        'signatory_title': 'Procurement Head',
        'signatory_department': 'Procurement',
        'items': items,
        'include_terms': include_terms,
    }


results = {}

pdf = po_pdf.render_po_pdf(BULK_PO)
results['BULK terms-on'] = page_count(pdf)

pdf = po_pdf.render_po_pdf(job_work_po(2, include_terms=True))
results['JOB_WORK 2-row terms-on'] = page_count(pdf)

for n in (5, 6, 7, 8, 9, 10):
    try:
        pdf = po_pdf.render_po_pdf(job_work_po(n, include_terms=True))
        results[f'JOB_WORK {n}-row terms-on'] = page_count(pdf)
    except Exception as e:
        results[f'JOB_WORK {n}-row terms-on'] = f'ERROR: {e}'

pdf = po_pdf.render_po_pdf(job_work_po(15, include_terms=False))
results['JOB_WORK 15-row terms-off'] = page_count(pdf)

pdf = po_pdf.render_po_pdf(job_work_po(2, include_terms=False))
results['JOB_WORK 2-row terms-off'] = page_count(pdf)

pdf = po_pdf.render_po_pdf({**BULK_PO, 'include_terms': False})
results['BULK terms-off'] = page_count(pdf)

for k, v in results.items():
    print(f'{k}: {v} page(s)')

print()
print('=== content check: JOB_WORK 8-row terms-on, page-by-page text ===')
pdf8 = po_pdf.render_po_pdf(job_work_po(8, include_terms=True))
reader = PdfReader(io.BytesIO(pdf8))
print('pages:', len(reader.pages))
markers = ['JOB WORKER', 'PRODUCT', 'ORDER DETAILS', 'TOTAL ORDER VALUE', 'TO BE BILLED ON',
           'COMMERCIAL TERMS', 'Please confirm dispatch', 'Yours faithfully', 'TERMS & CONDITIONS',
           'Non-conforming', 'Hyderabad, Telangana.', 'GSTIN: 36AAECA1439J1ZR']
for i, page in enumerate(reader.pages, 1):
    txt = page.extract_text()
    present = [m for m in markers if m in txt]
    print(f'page {i} (len={len(txt)}): {present}')
