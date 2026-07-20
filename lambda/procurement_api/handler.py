"""
Procurement API Lambda — CRUD backend for procurement.iraviagrolife.com.

Serves the Procurement team's "Setup" sections (Technical / Supplier / Supplier
Company configuration, Enquiries, PDC) plus reuse of the shared RBAC login.

- Auth: reuses the SAME app_users / app_roles / app_screens RBAC tables and the
  SAME JWT signing key (Secrets Manager, JWT_SECRET_ARN) as the dashboard API, so
  users are managed once in the dashboard's Access Control screen. `POST /auth/login`
  is public; every other route requires a valid bearer token (any authenticated
  user). Per-screen authorization is UI-only for now (phase 1) — same posture as
  the dashboard's read routes.
- Data: plain CRUD against the `procurement.*` schema (migration 026). No Redis —
  low-volume, write-heavy config data goes straight to RDS.

Env vars: DB_SECRET_ARN, JWT_SECRET_ARN.
"""

import base64
import json
import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal

import boto3
import psycopg2

import auth

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client('secretsmanager')


# ── infra helpers ─────────────────────────────────────────────────────────────

def _get_db_conn():
    secret = json.loads(
        secrets.get_secret_value(SecretId=os.environ['DB_SECRET_ARN'])['SecretString']
    )
    return psycopg2.connect(
        host=secret['host'],
        port=secret.get('port', 5432),
        dbname=secret['dbname'],
        user=secret['username'],
        password=secret['password'],
    )


def _ser(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f'Not JSON serializable: {type(o)}')


def _response(status: int, body) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, default=_ser),
    }


def _pdf_response(pdf_bytes: bytes, filename: str) -> dict:
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/pdf',
            'Content-Disposition': f'attachment; filename="{filename}"',
        },
        'body': base64.b64encode(pdf_bytes).decode('ascii'),
        'isBase64Encoded': True,
    }


def _json_body(event) -> dict:
    try:
        return json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        raise auth.AuthError('Invalid JSON body', 400)


def _path_id(event):
    raw = (event.get('pathParameters') or {}).get('id')
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise auth.AuthError('Invalid id', 400)


def _rows(cur):
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _s(v):
    """Normalise a form string: strip; '' -> None."""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _num(v):
    """Optional numeric -> float or None (accepts '' / None / '-')."""
    if v is None or str(v).strip() in ('', '-'):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise auth.AuthError('Invalid numeric value', 400)


def _int(v):
    if v is None or str(v).strip() in ('', '-'):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise auth.AuthError('Invalid integer value', 400)


class _InUse(Exception):
    """Raised when a delete is blocked by a foreign-key reference."""


# ── entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', '')
    path = event.get('rawPath', '')
    logger.info('%s %s', method, path)

    try:
        # Public login.
        if path == '/auth/login' and method == 'POST':
            return _handle_login(event.get('body') or '')
        if path == '/auth/me' and method == 'GET':
            return _handle_me(event)

        # Everything below requires a valid token (any authenticated user).
        auth.authenticate(event)
        return _route_data(event, method, path)
    except auth.AuthError as exc:
        return _response(exc.status, {'error': exc.message})
    except _InUse as exc:
        return _response(409, {'error': str(exc)})
    except Exception:
        logger.exception('Unhandled error')
        return _response(500, {'error': 'Internal server error'})


def _route_data(event, method, path):
    if path == '/overview':
        if method == 'GET':
            return _overview()
        return _response(405, {'error': 'Method not allowed'})

    # Purchase Order PDF export — GET /purchase-orders/{id}/pdf (before the generic
    # item-route loop, which only handles PUT/DELETE on /<resource>/{id}).
    pdf_m = re.match(r'^/purchase-orders/(\d+)/pdf$', path)
    if pdf_m:
        if method == 'GET':
            return _po_pdf(int(pdf_m.group(1)))
        return _response(405, {'error': 'Method not allowed'})

    # Collection roots and their {id} item routes.
    routes = {
        '/technicals': (_technicals_list, _technicals_create),
        '/packaging-meta': (_packaging_meta_list, _packaging_meta_create),
        '/packagings': (_packagings_list, _packagings_create),
        '/supplier-companies': (_companies_list, _companies_create),
        '/suppliers': (_suppliers_list, _suppliers_create),
        '/enquiries': (_enquiries_list, _enquiries_create),
        '/pdc': (_pdc_list, _pdc_create),
        '/signatory-authorities': (_signatories_list, _signatories_create),
        '/purchase-orders': (_po_list, _po_create),
    }
    item_routes = {
        '/technicals/': (_technicals_update, _technicals_delete),
        '/packaging-meta/': (_packaging_meta_update, _packaging_meta_delete),
        '/packagings/': (_packagings_update, _packagings_delete),
        '/supplier-companies/': (_companies_update, _companies_delete),
        '/suppliers/': (_suppliers_update, _suppliers_delete),
        '/enquiries/': (_enquiries_update, _enquiries_delete),
        '/pdc/': (_pdc_update, _pdc_delete),
        '/signatory-authorities/': (_signatories_update, _signatories_delete),
        '/purchase-orders/': (_po_update, _po_delete),
    }

    if path in routes:
        get_fn, post_fn = routes[path]
        if method == 'GET':
            return get_fn()
        if method == 'POST':
            return post_fn(event)
        return _response(405, {'error': 'Method not allowed'})

    for prefix, (put_fn, del_fn) in item_routes.items():
        if path.startswith(prefix):
            _id = _path_id(event)
            if method == 'PUT':
                return put_fn(event, _id)
            if method == 'DELETE':
                return del_fn(_id)
            return _response(405, {'error': 'Method not allowed'})

    return _response(404, {'error': 'Not found'})


# ── auth (shared RBAC) ────────────────────────────────────────────────────────

def _fetch_user_row(cur, username: str):
    cur.execute("""
        SELECT u.user_id, u.username, u.password_hash, u.is_active,
               u.role_id, r.role_name, r.is_admin
        FROM app_users u JOIN app_roles r ON r.role_id = u.role_id
        WHERE u.username = %s
    """, (username,))
    row = cur.fetchone()
    if not row:
        return None
    keys = ['user_id', 'username', 'password_hash', 'is_active', 'role_id', 'role_name', 'is_admin']
    return dict(zip(keys, row))


def _fetch_screens(cur, role_id: int, is_admin: bool) -> list:
    if is_admin:
        cur.execute('SELECT screen_key FROM app_screens ORDER BY sort_order')
    else:
        cur.execute("""
            SELECT s.screen_key
            FROM app_role_screens rs JOIN app_screens s ON s.screen_key = rs.screen_key
            WHERE rs.role_id = %s
            ORDER BY s.sort_order
        """, (role_id,))
    return [r[0] for r in cur.fetchall()]


def _handle_login(body_str: str):
    try:
        body = json.loads(body_str or '{}')
    except json.JSONDecodeError:
        return _response(400, {'error': 'Invalid JSON body'})

    username = (body.get('username') or '').strip().lower()
    password = body.get('password') or ''
    if not username or not password:
        return _response(400, {'error': 'username and password are required'})

    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            user = _fetch_user_row(cur, username)
            if user is None:
                return _response(401, {'error': 'Invalid username or password'})
            if not user['is_active']:
                return _response(401, {'error': 'Account is disabled'})
            if not auth.verify_password(password, user['password_hash']):
                return _response(401, {'error': 'Invalid username or password'})
            screens = _fetch_screens(cur, user['role_id'], user['is_admin'])
    finally:
        conn.close()

    token = auth.sign_jwt({'sub': user['username'], 'is_admin': user['is_admin']})
    return _response(200, {
        'token': token,
        'user': {
            'username': user['username'],
            'role_name': user['role_name'],
            'is_admin': user['is_admin'],
            'screens': screens,
        },
    })


def _handle_me(event):
    claims = auth.authenticate(event)
    username = (claims.get('sub') or '').lower()
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            user = _fetch_user_row(cur, username)
            if user is None or not user['is_active']:
                raise auth.AuthError('User not found or inactive', 401)
            screens = _fetch_screens(cur, user['role_id'], user['is_admin'])
    finally:
        conn.close()
    return _response(200, {
        'username': user['username'],
        'role_name': user['role_name'],
        'is_admin': user['is_admin'],
        'screens': screens,
    })


# ── generic CRUD execution helpers ────────────────────────────────────────────

def _query(sql, params=None):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = _rows(cur)
        return rows
    finally:
        conn.close()


def _write(sql, params, returning=True):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
                out = _rows(cur)[0] if (returning and cur.description) else None
            except psycopg2.errors.ForeignKeyViolation:
                conn.rollback()
                raise _InUse('This record is referenced by other records and cannot be deleted or changed.')
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise auth.AuthError('A record with the same key already exists.', 409)
            conn.commit()
        return out
    finally:
        conn.close()


def _delete(sql, params):
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
                affected = cur.rowcount
            except psycopg2.errors.ForeignKeyViolation:
                conn.rollback()
                raise _InUse('This record is in use and cannot be deleted.')
            conn.commit()
        return affected
    finally:
        conn.close()


# ── Overview (landing-page tiles) ─────────────────────────────────────────────

def _overview():
    """Aggregate tiles for the Procurement overview landing page.

    Released PDC = cheques whose post-dated date has arrived (pdc_date <= today);
    upcoming PDC = the single nearest cheque still due (pdc_date >= today), with
    its supplier company as payee.
    """
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM procurement.enquiries')
            enquiries_count = cur.fetchone()[0]

            cur.execute('SELECT COUNT(*) FROM procurement.pdc')
            pdc_count = cur.fetchone()[0]

            cur.execute(
                'SELECT COALESCE(SUM(pdc_amt), 0) FROM procurement.pdc '
                'WHERE pdc_date IS NOT NULL AND pdc_date <= CURRENT_DATE'
            )
            pdc_amount_released = cur.fetchone()[0]

            cur.execute("""
                SELECT p.pdc_date, p.pdc_amt, c.company_name
                FROM procurement.pdc p
                LEFT JOIN procurement.supplier_companies c ON c.id = p.supplier_company_id
                WHERE p.pdc_date IS NOT NULL AND p.pdc_date >= CURRENT_DATE
                ORDER BY p.pdc_date ASC
                LIMIT 1
            """)
            row = cur.fetchone()
            upcoming_pdc = (
                {'pdc_date': row[0], 'pdc_amt': row[1], 'payee': row[2]} if row else None
            )

            cur.execute('SELECT COUNT(*) FROM procurement.technicals')
            technicals_count = cur.fetchone()[0]

            cur.execute('SELECT COUNT(*) FROM procurement.suppliers')
            suppliers_count = cur.fetchone()[0]

            cur.execute('SELECT COUNT(*) FROM procurement.supplier_companies')
            companies_count = cur.fetchone()[0]
    finally:
        conn.close()

    return _response(200, {
        'enquiries_count': enquiries_count,
        'pdc_count': pdc_count,
        'pdc_amount_released': pdc_amount_released,
        'upcoming_pdc': upcoming_pdc,
        'technicals_count': technicals_count,
        'suppliers_count': suppliers_count,
        'companies_count': companies_count,
    })


# ── Technicals ────────────────────────────────────────────────────────────────

def _technicals_list():
    return _response(200, _query(
        'SELECT id, technical_name, brand_name, is_active, created_at, updated_at '
        'FROM procurement.technicals ORDER BY technical_name'
    ))


def _technicals_create(event):
    b = _json_body(event)
    name = _s(b.get('technical_name'))
    if not name:
        raise auth.AuthError('technical_name is required', 400)
    row = _write(
        'INSERT INTO procurement.technicals (technical_name, brand_name, is_active) '
        'VALUES (%s, %s, %s) RETURNING id, technical_name, brand_name, is_active, created_at, updated_at',
        (name, _s(b.get('brand_name')), bool(b.get('is_active', True))),
    )
    return _response(201, row)


def _technicals_update(event, _id):
    b = _json_body(event)
    name = _s(b.get('technical_name'))
    if not name:
        raise auth.AuthError('technical_name is required', 400)
    row = _write(
        'UPDATE procurement.technicals SET technical_name=%s, brand_name=%s, is_active=%s '
        'WHERE id=%s RETURNING id, technical_name, brand_name, is_active, created_at, updated_at',
        (name, _s(b.get('brand_name')), bool(b.get('is_active', True)), _id),
    )
    if row is None:
        return _response(404, {'error': 'Technical not found'})
    return _response(200, row)


def _technicals_delete(_id):
    if _delete('DELETE FROM procurement.technicals WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Technical not found'})
    return _response(200, {'deleted': _id})


# ── Signatory Authorities ─────────────────────────────────────────────────────

_SIGNATORY_COLS = 'id, name, title, department, is_active, created_at, updated_at'


def _signatories_list():
    return _response(200, _query(
        f'SELECT {_SIGNATORY_COLS} '
        'FROM procurement.signatory_authorities ORDER BY name'
    ))


def _signatories_create(event):
    b = _json_body(event)
    name = _s(b.get('name'))
    if not name:
        raise auth.AuthError('name is required', 400)
    row = _write(
        'INSERT INTO procurement.signatory_authorities (name, title, department, is_active) '
        f'VALUES (%s, %s, %s, %s) RETURNING {_SIGNATORY_COLS}',
        (name, _s(b.get('title')), _s(b.get('department')), bool(b.get('is_active', True))),
    )
    return _response(201, row)


def _signatories_update(event, _id):
    b = _json_body(event)
    name = _s(b.get('name'))
    if not name:
        raise auth.AuthError('name is required', 400)
    row = _write(
        'UPDATE procurement.signatory_authorities SET name=%s, title=%s, department=%s, is_active=%s '
        f'WHERE id=%s RETURNING {_SIGNATORY_COLS}',
        (name, _s(b.get('title')), _s(b.get('department')), bool(b.get('is_active', True)), _id),
    )
    if row is None:
        return _response(404, {'error': 'Signatory authority not found'})
    return _response(200, row)


def _signatories_delete(_id):
    if _delete('DELETE FROM procurement.signatory_authorities WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Signatory authority not found'})
    return _response(200, {'deleted': _id})


# ── Purchase Orders (Bulk) ────────────────────────────────────────────────────

# Full row + joined display fields for supplier / bill-to / ship-to companies,
# the product technical, and the signatory.
_PO_SELECT = """
    SELECT
      po.id, po.po_type, po.po_no, po.po_date, po.po_seq,
      po.supplier_company_id, sc.company_name  AS supplier_company_name,
      sc.address_line1 AS supplier_address_line1, sc.address_line2 AS supplier_address_line2,
      sc.address_line3 AS supplier_address_line3, sc.state AS supplier_state,
      sc.pin_code AS supplier_pin_code, sc.gstin AS supplier_gstin,
      po.product_technical_id, t.technical_name, t.brand_name,
      po.quantity, po.quantity_unit, po.rate, po.gst_rate,
      ROUND(po.quantity * po.rate, 2)                          AS amount,
      ROUND(po.quantity * po.rate * po.gst_rate / 100.0, 2)    AS gst_amount,
      ROUND(po.quantity * po.rate * (1 + po.gst_rate / 100.0), 2) AS total_value,
      po.terms, po.dispatch, po.transport,
      po.bill_to_company_id, bc.company_name AS bill_to_company_name,
      bc.address_line1 AS bill_to_address_line1, bc.address_line2 AS bill_to_address_line2,
      bc.address_line3 AS bill_to_address_line3, bc.state AS bill_to_state,
      bc.pin_code AS bill_to_pin_code, bc.gstin AS bill_to_gstin,
      po.ship_to_company_id, pc.company_name AS ship_to_company_name,
      pc.address_line1 AS ship_to_address_line1, pc.address_line2 AS ship_to_address_line2,
      pc.address_line3 AS ship_to_address_line3, pc.state AS ship_to_state,
      pc.pin_code AS ship_to_pin_code, pc.gstin AS ship_to_gstin,
      po.signatory_id, sa.name AS signatory_name, sa.title AS signatory_title,
      sa.department AS signatory_department,
      po.note, po.include_terms, po.generic_config, po.created_at, po.updated_at
    FROM procurement.purchase_orders po
    JOIN procurement.supplier_companies sc ON sc.id = po.supplier_company_id
    LEFT JOIN procurement.technicals t     ON t.id = po.product_technical_id
    LEFT JOIN procurement.supplier_companies bc ON bc.id = po.bill_to_company_id
    LEFT JOIN procurement.supplier_companies pc ON pc.id = po.ship_to_company_id
    LEFT JOIN procurement.signatory_authorities sa ON sa.id = po.signatory_id
"""
# NOTE: `t` (technicals) is a LEFT JOIN (was INNER) so GENERIC rows — which have no
# product_technical_id — still come back from list/get_one instead of being silently
# dropped. BULK/JOB_WORK always set product_technical_id, so their rows are unaffected.

_VALID_QTY_UNITS_BULK = ('KGS', 'LTRS')
_VALID_QTY_UNITS_JOB_WORK = ('KGS', 'TONNE', 'LTRS', 'KL')
_VALID_PO_TYPES = ('BULK', 'JOB_WORK', 'GENERIC')

# Default body text for a GENERIC PO when the caller sends an empty/blank body.
_GENERIC_DEFAULT_BODY = (
    'Please supply the under mentioned goods, subject to terms & conditions stated below. '
    'Please also quote this order reference in all your supply documents and future '
    'correspondence. Please dispatch the stock within 3 days of receipt of this PO.'
)

# Header quantity_unit -> (base unit stored on each line item, multiplier to convert
# the header quantity into that base unit for the reconciliation guard below).
_UNIT_BASE = {'KGS': ('KGS', 1), 'TONNE': ('KGS', 1000), 'LTRS': ('LTRS', 1), 'KL': ('LTRS', 1000)}

# JOIN chain reused from _PACKAGING_SELECT (packagings -> packaging_meta) to expose the
# same 'packaging' label on each purchase_order_items row.
_PO_ITEMS_SELECT_BASE = """
    SELECT poi.po_id, poi.sl_no, poi.technical_id, t.technical_name, t.brand_name,
           poi.packaging_id, m.label AS packaging,
           poi.quantity, poi.rate, poi.amount
    FROM procurement.purchase_order_items poi
    JOIN procurement.technicals t ON t.id = poi.technical_id
    LEFT JOIN procurement.packagings pkg ON pkg.id = poi.packaging_id
    LEFT JOIN procurement.packaging_meta m ON m.id = pkg.packaging_meta_id
"""


def _po_items_for(_id):
    """Items for a single PO, in sl_no order — po_id stripped from each row."""
    rows = _query(_PO_ITEMS_SELECT_BASE + ' WHERE poi.po_id = %s ORDER BY poi.sl_no', (_id,))
    for r in rows:
        r.pop('po_id', None)
    return rows


def _po_apply_job_work_totals(po):
    """Override the header-derived amount/gst_amount/total_value on a JOB_WORK PO dict
    with figures computed from its line items (po['items'] must already be attached).

    The header `rate` is 0 for JOB_WORK POs — all pricing lives on the line items — so
    the header-based SQL in _PO_SELECT yields 0 for these three fields. Mirrors the
    rounding convention used by _PO_SELECT for BULK: amount = Sum(item.amount) rounded
    to 2dp (each item.amount is already qty*rate rounded to 2dp — see
    _po_validate_items); gst_amount = round(amount * gst_rate / 100, 2);
    total_value = round(amount + gst_amount, 2)."""
    amount = round(sum(float(it.get('amount') or 0) for it in po.get('items') or []), 2)
    gst_rate = float(po.get('gst_rate') or 0)
    gst_amount = round(amount * gst_rate / 100.0, 2)
    po['amount'] = amount
    po['gst_amount'] = gst_amount
    po['total_value'] = round(amount + gst_amount, 2)


def _po_items_for_many(po_ids):
    """Items for several POs in one query (avoids N+1 on /purchase-orders list),
    grouped by po_id -> [item, ...]; po_id stripped from each row."""
    if not po_ids:
        return {}
    rows = _query(
        _PO_ITEMS_SELECT_BASE + ' WHERE poi.po_id = ANY(%s) ORDER BY poi.po_id, poi.sl_no',
        (list(po_ids),),
    )
    out = {}
    for r in rows:
        out.setdefault(r.pop('po_id'), []).append(r)
    return out


def _po_write_items(cur, po_id, items):
    """Replace all line items for a PO (same cursor/transaction as the header write)."""
    cur.execute('DELETE FROM procurement.purchase_order_items WHERE po_id = %s', (po_id,))
    for sl_no, it in enumerate(items, 1):
        cur.execute(
            'INSERT INTO procurement.purchase_order_items '
            '(po_id, sl_no, technical_id, packaging_id, quantity, rate, amount) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (po_id, sl_no, it['technical_id'], it['packaging_id'], it['quantity'], it['rate'], it['amount']),
        )


def _po_list():
    rows = _query(_PO_SELECT + ' ORDER BY po.po_date DESC, po.po_seq DESC')
    items_by_po = _po_items_for_many([r['id'] for r in rows if r['po_type'] == 'JOB_WORK'])
    for r in rows:
        r['items'] = items_by_po.get(r['id'], [])
        if r['po_type'] == 'JOB_WORK':
            _po_apply_job_work_totals(r)
    return _response(200, rows)


def _po_get_one(_id):
    rows = _query(_PO_SELECT + ' WHERE po.id = %s', (_id,))
    if not rows:
        return None
    po = rows[0]
    po['items'] = _po_items_for(_id) if po['po_type'] == 'JOB_WORK' else []
    if po['po_type'] == 'JOB_WORK':
        _po_apply_job_work_totals(po)
    return po


def _po_validate_items(raw_items, header_unit, header_qty):
    """Validate/coerce JOB_WORK line items and enforce the reconciliation guard: the sum
    of item quantities (each already in its base unit) must equal the header quantity
    converted to that same base unit (TONNE->x1000 KGS, KL->x1000 LTRS)."""
    if not raw_items or not isinstance(raw_items, list):
        raise auth.AuthError('items is required for JOB_WORK purchase orders', 400)
    base_unit, factor = _UNIT_BASE[header_unit]
    base_qty = header_qty * factor
    items = []
    total = 0.0
    for it in raw_items:
        it = it or {}
        technical_id = _int(it.get('technical_id'))
        if not technical_id:
            raise auth.AuthError('Each item requires a technical_id', 400)
        qty = _num(it.get('quantity'))
        if qty is None:
            raise auth.AuthError('Each item requires a quantity', 400)
        rate = _num(it.get('rate')) or 0
        items.append({
            'technical_id': technical_id,
            'packaging_id': _int(it.get('packaging_id')),
            'quantity': qty,
            'rate': rate,
            'amount': round(qty * rate, 2),
        })
        total += qty
    if abs(total - base_qty) >= 0.01:
        raise auth.AuthError(
            f'Item quantities total {total:.2f} {base_unit}, but header quantity is '
            f'{header_qty:g} {header_unit} ({base_qty:.2f} {base_unit}). They must match.',
            400,
        )
    return items


def _po_validate_generic_config(raw):
    """Validate + coerce the GENERIC-only 'generic_config' JSON blob:
    {subject, body, columns: [str,...], rows: [[str,...], ...]}. `columns` must be a
    non-empty array; each row is defensively padded/truncated to len(columns) and every
    cell coerced to a string (free-text, no numeric/rate/GST semantics). `body` defaults
    to the standard text when blank; `subject` defaults to ''."""
    raw = raw if isinstance(raw, dict) else {}
    columns = raw.get('columns')
    if not columns or not isinstance(columns, list):
        raise auth.AuthError('generic_config.columns is required and must be a non-empty array', 400)
    columns = [str(c) for c in columns]
    n = len(columns)

    raw_rows = raw.get('rows') or []
    if not isinstance(raw_rows, list):
        raise auth.AuthError('generic_config.rows must be an array', 400)
    rows = []
    for r in raw_rows:
        r = r if isinstance(r, list) else [r]
        r = ['' if c is None else str(c) for c in r]
        if len(r) < n:
            r = r + [''] * (n - len(r))
        elif len(r) > n:
            r = r[:n]
        rows.append(r)

    return {
        'subject': _s(raw.get('subject')) or '',
        'body': _s(raw.get('body')) or _GENERIC_DEFAULT_BODY,
        'columns': columns,
        'rows': rows,
    }


def _po_validate(b):
    """Validate + coerce the shared PO fields. Returns a params dict (no po_no/seq).
    For JOB_WORK, also validates + coerces b['items'] (params['items']; None otherwise).
    For GENERIC, validates b['generic_config'] instead (params['generic_config']; None
    for BULK/JOB_WORK) — no product/quantity/rate/GST/items for that type."""
    po_type = (_s(b.get('po_type')) or 'BULK').upper()
    if po_type not in _VALID_PO_TYPES:
        raise auth.AuthError('po_type must be one of BULK, JOB_WORK, GENERIC', 400)
    supplier_company_id = _int(b.get('supplier_company_id'))
    if not supplier_company_id:
        raise auth.AuthError('supplier_company_id is required', 400)

    common = {
        'supplier_company_id': supplier_company_id,
        'bill_to_company_id': _int(b.get('bill_to_company_id')),
        'ship_to_company_id': _int(b.get('ship_to_company_id')),
        'signatory_id': _int(b.get('signatory_id')),
        'note': _s(b.get('note')),
        'po_type': po_type,
        'include_terms': bool(b.get('include_terms', True)),
    }

    if po_type == 'GENERIC':
        # No product/quantity/rate/GST/items/terms-commercial fields, no reconciliation
        # guard — Generic is a free-form, non-priced PO. All its content lives in
        # generic_config.
        return {
            **common,
            'product_technical_id': None,
            'quantity': None,
            'quantity_unit': None,
            'rate': None,
            'gst_rate': None,
            'terms': None,
            'dispatch': None,
            'transport': None,
            'items': None,
            'generic_config': _po_validate_generic_config(b.get('generic_config')),
        }

    product_technical_id = _int(b.get('product_technical_id'))
    quantity = _num(b.get('quantity'))
    unit = (_s(b.get('quantity_unit')) or '').upper()
    if not product_technical_id:
        raise auth.AuthError('product_technical_id is required', 400)
    if quantity is None:
        raise auth.AuthError('quantity is required', 400)
    valid_units = _VALID_QTY_UNITS_JOB_WORK if po_type == 'JOB_WORK' else _VALID_QTY_UNITS_BULK
    if unit not in valid_units:
        raise auth.AuthError(f'quantity_unit must be one of {", ".join(valid_units)}', 400)
    items = _po_validate_items(b.get('items'), unit, quantity) if po_type == 'JOB_WORK' else None
    gst_rate = _num(b.get('gst_rate'))
    return {
        **common,
        'product_technical_id': product_technical_id,
        'quantity': quantity,
        'quantity_unit': unit,
        'rate': _num(b.get('rate')) or 0,
        'gst_rate': gst_rate if gst_rate is not None else 18,
        'terms': _s(b.get('terms')),
        'dispatch': _s(b.get('dispatch')),
        'transport': _s(b.get('transport')),
        'items': items,
        'generic_config': None,
    }


def _fy_code(po_date_str: str) -> str:
    """4-digit financial-year code for a YYYY-MM-DD date, e.g. 2026-07-16 -> '2627'
    (FY Apr-Mar: 2026-04..2027-03 -> '2627')."""
    d = datetime.strptime(po_date_str[:10], '%Y-%m-%d').date()
    start = d.year if d.month >= 4 else d.year - 1
    return f'{start % 100:02d}{(start + 1) % 100:02d}'


def _po_create(event):
    b = _json_body(event)
    p = _po_validate(b)
    po_date = _s(b.get('po_date')) or date.today().isoformat()
    fy = _fy_code(po_date)

    # Compute the next per-FY serial and insert atomically; retry on the rare race
    # where two POs in the same FY collide on (fy, po_seq) / po_no.
    conn = _get_db_conn()
    try:
        for _attempt in range(5):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        'SELECT COALESCE(MAX(po_seq), 0) + 1 FROM procurement.purchase_orders '
                        'WHERE fy = %s',
                        (fy,),
                    )
                    seq = cur.fetchone()[0]
                    po_no = f'IAL/{fy}/{seq}'
                    cur.execute(
                        'INSERT INTO procurement.purchase_orders '
                        '(po_type, po_no, po_date, fy, po_seq, supplier_company_id, product_technical_id, '
                        'quantity, quantity_unit, rate, gst_rate, terms, dispatch, transport, '
                        'bill_to_company_id, ship_to_company_id, signatory_id, note, include_terms, '
                        'generic_config) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                        (
                            p['po_type'], po_no, po_date, fy, seq, p['supplier_company_id'],
                            p['product_technical_id'], p['quantity'], p['quantity_unit'], p['rate'],
                            p['gst_rate'], p['terms'], p['dispatch'], p['transport'],
                            p['bill_to_company_id'], p['ship_to_company_id'], p['signatory_id'], p['note'],
                            p['include_terms'],
                            json.dumps(p['generic_config']) if p['generic_config'] is not None else None,
                        ),
                    )
                    new_id = cur.fetchone()[0]
                    if p['po_type'] == 'JOB_WORK':
                        _po_write_items(cur, new_id, p['items'])
                conn.commit()
                return _response(201, _po_get_one(new_id))
            except psycopg2.errors.UniqueViolation:
                conn.rollback()  # another PO grabbed this serial — recompute and retry
        raise auth.AuthError('Could not allocate a PO number, please retry', 409)
    finally:
        conn.close()


# po_no / po_date / po_seq are immutable once created — only the content changes.
_PO_UPDATE_SQL = (
    'UPDATE procurement.purchase_orders SET '
    'po_type=%s, supplier_company_id=%s, product_technical_id=%s, quantity=%s, quantity_unit=%s, '
    'rate=%s, gst_rate=%s, terms=%s, dispatch=%s, transport=%s, bill_to_company_id=%s, '
    'ship_to_company_id=%s, signatory_id=%s, note=%s, include_terms=%s, generic_config=%s '
    'WHERE id=%s RETURNING id'
)


def _po_update_params(p, _id):
    return (
        p['po_type'], p['supplier_company_id'], p['product_technical_id'], p['quantity'],
        p['quantity_unit'], p['rate'], p['gst_rate'], p['terms'], p['dispatch'], p['transport'],
        p['bill_to_company_id'], p['ship_to_company_id'], p['signatory_id'], p['note'],
        p['include_terms'],
        json.dumps(p['generic_config']) if p['generic_config'] is not None else None,
        _id,
    )


def _po_update(event, _id):
    b = _json_body(event)
    p = _po_validate(b)

    if p['po_type'] != 'JOB_WORK':
        # BULK — unchanged single-statement write via the shared _write() helper.
        row = _write(_PO_UPDATE_SQL, _po_update_params(p, _id))
        if row is None:
            return _response(404, {'error': 'Purchase order not found'})
        return _response(200, _po_get_one(_id))

    # JOB_WORK — header update + item replace must be the same transaction so a
    # failure on either rolls back both.
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(_PO_UPDATE_SQL, _po_update_params(p, _id))
                row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    return _response(404, {'error': 'Purchase order not found'})
                _po_write_items(cur, _id, p['items'])
            except psycopg2.errors.ForeignKeyViolation:
                conn.rollback()
                raise _InUse('This record is referenced by other records and cannot be deleted or changed.')
            except psycopg2.errors.UniqueViolation:
                conn.rollback()
                raise auth.AuthError('A record with the same key already exists.', 409)
            conn.commit()
    finally:
        conn.close()
    return _response(200, _po_get_one(_id))


def _po_delete(_id):
    if _delete('DELETE FROM procurement.purchase_orders WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Purchase order not found'})
    return _response(200, {'deleted': _id})


def _po_pdf(_id):
    po = _po_get_one(_id)
    if po is None:
        return _response(404, {'error': 'Purchase order not found'})
    import po_pdf  # local import so a reportlab issue never breaks the CRUD routes
    pdf_bytes = po_pdf.render_po_pdf(po)
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', po.get('po_no') or f'PO_{_id}')
    return _pdf_response(pdf_bytes, f'{safe}.pdf')


# ── Packaging Meta (master size lists per unit type) ──────────────────────────

_VALID_UNIT_TYPES = ('KG', 'LTR')


def _packaging_meta_list():
    return _response(200, _query(
        'SELECT id, unit_type, label, sort_order, is_active, created_at, updated_at '
        'FROM procurement.packaging_meta ORDER BY unit_type, sort_order, label'
    ))


def _packaging_meta_create(event):
    b = _json_body(event)
    unit_type = (_s(b.get('unit_type')) or '').upper()
    label = _s(b.get('label'))
    if unit_type not in _VALID_UNIT_TYPES:
        raise auth.AuthError('unit_type must be one of KG, LTR', 400)
    if not label:
        raise auth.AuthError('label is required', 400)
    row = _write(
        'INSERT INTO procurement.packaging_meta (unit_type, label, sort_order, is_active) '
        'VALUES (%s, %s, %s, %s) '
        'RETURNING id, unit_type, label, sort_order, is_active, created_at, updated_at',
        (unit_type, label, _int(b.get('sort_order')) or 100, bool(b.get('is_active', True))),
    )
    return _response(201, row)


def _packaging_meta_update(event, _id):
    b = _json_body(event)
    unit_type = (_s(b.get('unit_type')) or '').upper()
    label = _s(b.get('label'))
    if unit_type not in _VALID_UNIT_TYPES:
        raise auth.AuthError('unit_type must be one of KG, LTR', 400)
    if not label:
        raise auth.AuthError('label is required', 400)
    row = _write(
        'UPDATE procurement.packaging_meta SET unit_type=%s, label=%s, sort_order=%s, is_active=%s '
        'WHERE id=%s '
        'RETURNING id, unit_type, label, sort_order, is_active, created_at, updated_at',
        (unit_type, label, _int(b.get('sort_order')) or 100, bool(b.get('is_active', True)), _id),
    )
    if row is None:
        return _response(404, {'error': 'Packaging size not found'})
    return _response(200, row)


def _packaging_meta_delete(_id):
    if _delete('DELETE FROM procurement.packaging_meta WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Packaging size not found'})
    return _response(200, {'deleted': _id})


# ── Packagings (packaging sizes assigned per brand) ───────────────────────────

_PACKAGING_SELECT = """
    SELECT p.id, p.technical_id, t.brand_name, t.technical_name,
           p.packaging_meta_id, m.unit_type, m.label AS packaging,
           p.is_active, p.created_at, p.updated_at
    FROM procurement.packagings p
    JOIN procurement.technicals t ON t.id = p.technical_id
    JOIN procurement.packaging_meta m ON m.id = p.packaging_meta_id
"""


def _packagings_list():
    return _response(200, _query(
        _PACKAGING_SELECT + ' ORDER BY t.brand_name, t.technical_name, m.unit_type, m.sort_order'
    ))


def _packagings_get_one(_id):
    rows = _query(_PACKAGING_SELECT + ' WHERE p.id = %s', (_id,))
    return rows[0] if rows else None


def _packagings_create(event):
    b = _json_body(event)
    technical_id = _int(b.get('technical_id'))
    packaging_meta_id = _int(b.get('packaging_meta_id'))
    if not technical_id:
        raise auth.AuthError('technical_id is required', 400)
    if not packaging_meta_id:
        raise auth.AuthError('packaging_meta_id is required', 400)
    row = _write(
        'INSERT INTO procurement.packagings (technical_id, packaging_meta_id, is_active) '
        'VALUES (%s, %s, %s) RETURNING id',
        (technical_id, packaging_meta_id, bool(b.get('is_active', True))),
    )
    return _response(201, _packagings_get_one(row['id']))


def _packagings_update(event, _id):
    b = _json_body(event)
    technical_id = _int(b.get('technical_id'))
    packaging_meta_id = _int(b.get('packaging_meta_id'))
    if not technical_id:
        raise auth.AuthError('technical_id is required', 400)
    if not packaging_meta_id:
        raise auth.AuthError('packaging_meta_id is required', 400)
    row = _write(
        'UPDATE procurement.packagings SET technical_id=%s, packaging_meta_id=%s, is_active=%s '
        'WHERE id=%s RETURNING id',
        (technical_id, packaging_meta_id, bool(b.get('is_active', True)), _id),
    )
    if row is None:
        return _response(404, {'error': 'Packaging not found'})
    return _response(200, _packagings_get_one(_id))


def _packagings_delete(_id):
    if _delete('DELETE FROM procurement.packagings WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Packaging not found'})
    return _response(200, {'deleted': _id})


# ── Supplier Companies ────────────────────────────────────────────────────────

_COMPANY_COLS = (
    'id, company_name, location, address_line1, address_line2, address_line3, '
    'state, pin_code, gstin, is_active, created_at, updated_at'
)


def _companies_list():
    return _response(200, _query(
        f'SELECT {_COMPANY_COLS} '
        'FROM procurement.supplier_companies ORDER BY company_name'
    ))


def _companies_create(event):
    b = _json_body(event)
    name = _s(b.get('company_name'))
    if not name:
        raise auth.AuthError('company_name is required', 400)
    row = _write(
        'INSERT INTO procurement.supplier_companies '
        '(company_name, location, address_line1, address_line2, address_line3, '
        'state, pin_code, gstin, is_active) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) '
        f'RETURNING {_COMPANY_COLS}',
        (
            name, _s(b.get('location')), _s(b.get('address_line1')),
            _s(b.get('address_line2')), _s(b.get('address_line3')),
            _s(b.get('state')), _s(b.get('pin_code')), _s(b.get('gstin')),
            bool(b.get('is_active', True)),
        ),
    )
    return _response(201, row)


def _companies_update(event, _id):
    b = _json_body(event)
    name = _s(b.get('company_name'))
    if not name:
        raise auth.AuthError('company_name is required', 400)
    row = _write(
        'UPDATE procurement.supplier_companies SET '
        'company_name=%s, location=%s, address_line1=%s, address_line2=%s, '
        'address_line3=%s, state=%s, pin_code=%s, gstin=%s, is_active=%s '
        f'WHERE id=%s RETURNING {_COMPANY_COLS}',
        (
            name, _s(b.get('location')), _s(b.get('address_line1')),
            _s(b.get('address_line2')), _s(b.get('address_line3')),
            _s(b.get('state')), _s(b.get('pin_code')), _s(b.get('gstin')),
            bool(b.get('is_active', True)), _id,
        ),
    )
    if row is None:
        return _response(404, {'error': 'Supplier company not found'})
    return _response(200, row)


def _companies_delete(_id):
    if _delete('DELETE FROM procurement.supplier_companies WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Supplier company not found'})
    return _response(200, {'deleted': _id})


# ── Suppliers (contact @ company) ─────────────────────────────────────────────

_SUPPLIER_SELECT = """
    SELECT s.id, s.contact_person_name, s.company_id, c.company_name,
           s.contact_person_name || ' - ' || c.company_name AS display_name,
           s.is_active, s.created_at, s.updated_at
    FROM procurement.suppliers s
    JOIN procurement.supplier_companies c ON c.id = s.company_id
"""


def _suppliers_list():
    return _response(200, _query(_SUPPLIER_SELECT + ' ORDER BY c.company_name, s.contact_person_name'))


def _suppliers_get_one(_id):
    rows = _query(_SUPPLIER_SELECT + ' WHERE s.id = %s', (_id,))
    return rows[0] if rows else None


def _suppliers_create(event):
    b = _json_body(event)
    person = _s(b.get('contact_person_name'))
    company_id = _int(b.get('company_id'))
    if not person:
        raise auth.AuthError('contact_person_name is required', 400)
    if not company_id:
        raise auth.AuthError('company_id is required', 400)
    row = _write(
        'INSERT INTO procurement.suppliers (contact_person_name, company_id, is_active) '
        'VALUES (%s, %s, %s) RETURNING id',
        (person, company_id, bool(b.get('is_active', True))),
    )
    return _response(201, _suppliers_get_one(row['id']))


def _suppliers_update(event, _id):
    b = _json_body(event)
    person = _s(b.get('contact_person_name'))
    company_id = _int(b.get('company_id'))
    if not person:
        raise auth.AuthError('contact_person_name is required', 400)
    if not company_id:
        raise auth.AuthError('company_id is required', 400)
    row = _write(
        'UPDATE procurement.suppliers SET contact_person_name=%s, company_id=%s, is_active=%s '
        'WHERE id=%s RETURNING id',
        (person, company_id, bool(b.get('is_active', True)), _id),
    )
    if row is None:
        return _response(404, {'error': 'Supplier not found'})
    return _response(200, _suppliers_get_one(_id))


def _suppliers_delete(_id):
    if _delete('DELETE FROM procurement.suppliers WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Supplier not found'})
    return _response(200, {'deleted': _id})


# ── Enquiries ─────────────────────────────────────────────────────────────────

_ENQUIRY_SELECT = """
    SELECT e.id, e.enquiry_date, e.technical_id, t.technical_name, t.brand_name,
           e.supplier_id, s.contact_person_name, c.company_name,
           s.contact_person_name || ' - ' || c.company_name AS supplier_display,
           e.rate, e.created_at, e.updated_at
    FROM procurement.enquiries e
    JOIN procurement.technicals t ON t.id = e.technical_id
    JOIN procurement.suppliers s ON s.id = e.supplier_id
    JOIN procurement.supplier_companies c ON c.id = s.company_id
"""


def _enquiries_list():
    return _response(200, _query(_ENQUIRY_SELECT + ' ORDER BY e.enquiry_date DESC, e.id DESC'))


def _enquiries_get_one(_id):
    rows = _query(_ENQUIRY_SELECT + ' WHERE e.id = %s', (_id,))
    return rows[0] if rows else None


def _enquiries_create(event):
    b = _json_body(event)
    dt = _s(b.get('enquiry_date'))
    technical_id = _int(b.get('technical_id'))
    supplier_id = _int(b.get('supplier_id'))
    rate = _num(b.get('rate'))
    if not dt or not technical_id or not supplier_id or rate is None:
        raise auth.AuthError('enquiry_date, technical_id, supplier_id and rate are required', 400)
    row = _write(
        'INSERT INTO procurement.enquiries (enquiry_date, technical_id, supplier_id, rate) '
        'VALUES (%s, %s, %s, %s) RETURNING id',
        (dt, technical_id, supplier_id, rate),
    )
    return _response(201, _enquiries_get_one(row['id']))


def _enquiries_update(event, _id):
    b = _json_body(event)
    dt = _s(b.get('enquiry_date'))
    technical_id = _int(b.get('technical_id'))
    supplier_id = _int(b.get('supplier_id'))
    rate = _num(b.get('rate'))
    if not dt or not technical_id or not supplier_id or rate is None:
        raise auth.AuthError('enquiry_date, technical_id, supplier_id and rate are required', 400)
    row = _write(
        'UPDATE procurement.enquiries SET enquiry_date=%s, technical_id=%s, supplier_id=%s, rate=%s '
        'WHERE id=%s RETURNING id',
        (dt, technical_id, supplier_id, rate, _id),
    )
    if row is None:
        return _response(404, {'error': 'Enquiry not found'})
    return _response(200, _enquiries_get_one(_id))


def _enquiries_delete(_id):
    if _delete('DELETE FROM procurement.enquiries WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'Enquiry not found'})
    return _response(200, {'deleted': _id})


# ── PDC (post-dated cheques) ──────────────────────────────────────────────────

_PDC_SELECT = """
    SELECT p.id, p.po_no, p.po_date, p.supplier_company_id, c.company_name,
           p.technical_id, t.technical_name, p.brand, p.credit_days, p.qty, p.rate,
           p.gross, p.gst, p.amount, p.disc, p.adv, p.bal, p.pdc_amt, p.pdc_date,
           p.created_at, p.updated_at
    FROM procurement.pdc p
    LEFT JOIN procurement.supplier_companies c ON c.id = p.supplier_company_id
    LEFT JOIN procurement.technicals t ON t.id = p.technical_id
"""


def _pdc_list():
    return _response(200, _query(_PDC_SELECT + ' ORDER BY p.po_date DESC NULLS LAST, p.id DESC'))


def _pdc_get_one(_id):
    rows = _query(_PDC_SELECT + ' WHERE p.id = %s', (_id,))
    return rows[0] if rows else None


def _pdc_params(b):
    return (
        _s(b.get('po_no')),
        _s(b.get('po_date')),
        _int(b.get('supplier_company_id')),
        _int(b.get('technical_id')),
        _s(b.get('brand')),
        _int(b.get('credit_days')),
        _num(b.get('qty')),
        _num(b.get('rate')),
        _num(b.get('gross')),
        _num(b.get('gst')),
        _num(b.get('amount')),
        _num(b.get('disc')),
        _num(b.get('adv')),
        _num(b.get('bal')),
        _num(b.get('pdc_amt')),
        _s(b.get('pdc_date')),
    )


_PDC_COLS = ('po_no, po_date, supplier_company_id, technical_id, brand, credit_days, '
             'qty, rate, gross, gst, amount, disc, adv, bal, pdc_amt, pdc_date')


def _pdc_create(event):
    b = _json_body(event)
    row = _write(
        f'INSERT INTO procurement.pdc ({_PDC_COLS}) '
        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
        _pdc_params(b),
    )
    return _response(201, _pdc_get_one(row['id']))


def _pdc_update(event, _id):
    b = _json_body(event)
    row = _write(
        'UPDATE procurement.pdc SET '
        'po_no=%s, po_date=%s, supplier_company_id=%s, technical_id=%s, brand=%s, credit_days=%s, '
        'qty=%s, rate=%s, gross=%s, gst=%s, amount=%s, disc=%s, adv=%s, bal=%s, pdc_amt=%s, pdc_date=%s '
        'WHERE id=%s RETURNING id',
        (*_pdc_params(b), _id),
    )
    if row is None:
        return _response(404, {'error': 'PDC not found'})
    return _response(200, _pdc_get_one(_id))


def _pdc_delete(_id):
    if _delete('DELETE FROM procurement.pdc WHERE id=%s', (_id,)) == 0:
        return _response(404, {'error': 'PDC not found'})
    return _response(200, {'deleted': _id})
