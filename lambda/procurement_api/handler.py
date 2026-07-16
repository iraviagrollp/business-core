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

import json
import logging
import os
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

    # Collection roots and their {id} item routes.
    routes = {
        '/technicals': (_technicals_list, _technicals_create),
        '/packaging-meta': (_packaging_meta_list, _packaging_meta_create),
        '/packagings': (_packagings_list, _packagings_create),
        '/supplier-companies': (_companies_list, _companies_create),
        '/suppliers': (_suppliers_list, _suppliers_create),
        '/enquiries': (_enquiries_list, _enquiries_create),
        '/pdc': (_pdc_list, _pdc_create),
    }
    item_routes = {
        '/technicals/': (_technicals_update, _technicals_delete),
        '/packaging-meta/': (_packaging_meta_update, _packaging_meta_delete),
        '/packagings/': (_packagings_update, _packagings_delete),
        '/supplier-companies/': (_companies_update, _companies_delete),
        '/suppliers/': (_suppliers_update, _suppliers_delete),
        '/enquiries/': (_enquiries_update, _enquiries_delete),
        '/pdc/': (_pdc_update, _pdc_delete),
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
