from typing import Any
from html import escape as escape_html
import secrets

from flask import Flask, request, jsonify, render_template, send_from_directory, url_for, g, Response
from flask_cors import CORS
from flask_pymongo import PyMongo
from flask_mail import Mail, Message
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import jwt
import datetime
import os
import re
from bson.objectid import ObjectId
from bson.errors import InvalidId
from bson.binary import Binary
from functools import wraps
from app.config import build_config
from app.security import (
    valid_csrf_token,
    valid_email,
    valid_http_url,
    valid_text_fields,
    validate_upload_file,
    validate_password,
)
from app.services.auth import (
    consume_refresh_token,
    decode_access_token,
    decode_refresh_token,
    issue_access_token,
    issue_refresh_token,
    revoke_refresh_token,
)
from app.services.storage import InvalidUploadError, save_private_upload

from app.services.matching import (
    build_candidate_profile,
    build_job_profile,
    calculate_keyword_match,
    normalize_skill_list,
)
from app.services.resume_parser import extract_resume_text


PROFILE_FIELD_LIMITS = {
    'name': 120,
    'skills': 2000,
    'experience': 5000,
    'company': 200,
    'company_type': 120,
    'location': 200,
    'website': 2048,
    'hiring_roles': 1000,
    'email': 254,
}
JOB_FIELD_LIMITS = {
    'title': 200,
    'company': 200,
    'location': 200,
    'description': 10000,
    'skills': 2000,
}

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config.update(build_config())

# Application thresholds
MIN_MATCH_PERCENTAGE_TO_APPLY = int(os.getenv('MIN_MATCH_PERCENTAGE_TO_APPLY', 50))

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROFILE_IMAGE_FOLDER'], exist_ok=True)

mongo: Any = PyMongo(app)
mail = Mail(app)
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=app.config['RATELIMIT_STORAGE_URI'],
)
allowed_origins = [origin.strip() for origin in app.config.get('CORS_ORIGINS', '').split(',') if origin.strip()]
CORS(app, resources={r'/api/*': {'origins': allowed_origins or []}})


@app.after_request
def set_security_headers(response):
    if not app.config.get('SECURITY_HEADERS_ENABLED', True):
        return response

    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Permissions-Policy',
        'camera=(), microphone=(), geolocation=()',
    )
    response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "connect-src 'self'"
    )
    csp_header = (
        'Content-Security-Policy-Report-Only'
        if app.config.get('CSP_REPORT_ONLY', True)
        else 'Content-Security-Policy'
    )
    response.headers.setdefault(csp_header, csp)
    if app.config.get('HSTS_ENABLED', False):
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
    return response


@app.errorhandler(413)
def request_too_large(_error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Request exceeds the maximum allowed size'}), 413
    return 'Request exceeds the maximum allowed size', 413


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# Helper: convert ObjectId to string
def serialize_doc(doc):
    if doc is None:
        return None
    doc['_id'] = str(doc['_id'])
    return doc


def log_activity(action, entity_type, entity_id=None, details=None, user_id=None):
    """Log system activities for admin process monitoring."""
    try:
        mongo.db.activity_logs.insert_one({
            'action': action,
            'entity_type': entity_type,
            'entity_id': str(entity_id) if entity_id else None,
            'details': details or {},
            'user_id': str(user_id) if user_id else None,
            'created_at': utc_now()
        })
    except Exception:
        pass


def serialize_value(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat() + 'Z'
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(val) for key, val in value.items()}
    return value


def sanitize_user_doc(user_doc: dict[str, Any] | None) -> dict[str, Any]:
    if not user_doc:
        return {}
    cleaned: dict[str, Any] = serialize_value(dict(user_doc))
    if not isinstance(cleaned, dict):
        return {}
    cleaned.pop('password', None)
    cleaned.pop('resume_text', None)
    has_profile_photo = isinstance(
        cleaned.pop('profile_photo', None),
        (bytes, bytearray, Binary),
    )
    cleaned.pop('profile_photo_content_type', None)
    if has_profile_photo and cleaned.get('_id'):
        cleaned['profile_image'] = url_for(
            'download_profile_image',
            user_id=cleaned['_id'],
        )
    return cleaned


def parse_object_id(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def normalize_email(email: Any) -> str:
    return email.strip().lower() if isinstance(email, str) else ''


ROLE_COLLECTIONS = {
    'jobseeker': 'jobseekers',
    'recruiter': 'recruiters',
    'admin': 'admins',
}


def account_collection(role: str):
    """Return the collection that owns account data for a role."""
    collection_name = ROLE_COLLECTIONS.get(role, 'users')
    return getattr(mongo.db, collection_name, mongo.db.users)


def find_user_by_email(email: Any, role: str | None = None) -> dict[str, Any] | None:
    normalized = normalize_email(email)
    if not normalized:
        return None
    role_to_search = role if role in ROLE_COLLECTIONS else None
    collections = [account_collection(role_to_search)] if role_to_search else [
        account_collection(name) for name in ('jobseeker', 'recruiter', 'admin')
    ]
    collections = list(dict.fromkeys(collections))
    for collection in collections:
        user = collection.find_one({'email': normalized}) or collection.find_one({
            'email': {'$regex': f'^{re.escape(normalized)}$', '$options': 'i'}
        })
        if user:
            return user
    # Keep access to accounts created before role collections were introduced.
    legacy_query = {'email': normalized}
    if role in ROLE_COLLECTIONS:
        legacy_query['role'] = role
    user = mongo.db.users.find_one(legacy_query)
    if user:
        return user
    legacy_query['email'] = {
        '$regex': f'^{re.escape(normalized)}$', '$options': 'i'
    }
    return mongo.db.users.find_one(legacy_query)


def find_user_by_id(user_id: ObjectId) -> dict[str, Any] | None:
    seen = set()
    for role in ('jobseeker', 'recruiter', 'admin'):
        collection = account_collection(role)
        if id(collection) in seen:
            continue
        seen.add(id(collection))
        user = collection.find_one({'_id': user_id})
        if user:
            return user
    return mongo.db.users.find_one({'_id': user_id})


def find_account_collection_by_id(user_id: ObjectId):
    seen = set()
    for role in ('jobseeker', 'recruiter', 'admin'):
        collection = account_collection(role)
        if id(collection) in seen:
            continue
        seen.add(id(collection))
        if collection.find_one({'_id': user_id}, {'_id': 1}):
            return collection
    return mongo.db.users


def update_user_by_id(user_id: ObjectId, update: dict[str, Any]):
    return find_account_collection_by_id(user_id).update_one({'_id': user_id}, update)


def all_account_users(query=None, projection=None):
    """Read role separated accounts, with legacy records during migration."""
    users = []
    known_ids = set()
    for role in ('jobseeker', 'recruiter', 'admin'):
        for user in account_collection(role).find(query or {}, projection):
            users.append(user)
            known_ids.add(user.get('_id'))
    legacy_query = dict(query or {})
    legacy_query['role'] = {'$in': list(ROLE_COLLECTIONS)}
    for user in mongo.db.users.find(legacy_query, projection):
        if user.get('_id') not in known_ids:
            users.append(user)
    return users


def get_email_config_error():
    required_config = {
        'MAIL_SERVER': app.config.get('MAIL_SERVER'),
        'MAIL_PORT': app.config.get('MAIL_PORT'),
        'MAIL_USERNAME': app.config.get('MAIL_USERNAME'),
        'MAIL_PASSWORD': app.config.get('MAIL_PASSWORD'),
        'MAIL_DEFAULT_SENDER': app.config.get('MAIL_DEFAULT_SENDER'),
    }
    missing = [key for key, value in required_config.items() if not value]
    if missing:
        return f'Missing email settings: {", ".join(missing)}.'

    placeholder_values = {
        'your-real-gmail@gmail.com',
        'your-email@gmail.com',
        'your-gmail-address@gmail.com',
        'your_16_character_gmail_app_password',
        'your16characterapppassword',
        'abcd-efgh-ijkl-mnop',
    }
    placeholders = [
        key for key, value in required_config.items()
        if isinstance(value, str) and value.strip().lower() in placeholder_values
    ]
    if placeholders:
        return f'Email settings still contain placeholder values: {", ".join(placeholders)}.'

    username = str(required_config['MAIL_USERNAME']).strip().lower()
    sender = str(required_config['MAIL_DEFAULT_SENDER']).strip().lower()
    if username.endswith('@gmail.com') and '-' in str(required_config['MAIL_PASSWORD']):
        return 'Gmail App Password should be the 16-character app password, usually pasted without hyphens.'
    if sender != username:
        return 'MAIL_DEFAULT_SENDER should match MAIL_USERNAME for Gmail SMTP.'

    return None


def is_email_configured():
    return get_email_config_error() is None


def build_reset_link(reset_token: str) -> str:
    public_base_url = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if public_base_url:
        return f'{public_base_url}/reset-password?token={reset_token}'
    return url_for('reset_password_page', token=reset_token, _external=True)


def create_password_reset_token(user_id):
    return jwt.encode({
        'user_id': str(user_id),
        'type': 'password_reset',
        'exp': utc_now() + datetime.timedelta(
            hours=app.config['PASSWORD_RESET_TTL_HOURS']
        )
    }, app.config['SECRET_KEY'], algorithm='HS256')


def send_password_reset_email(user, reset_link):
    email_config_error = get_email_config_error()
    if email_config_error:
        raise RuntimeError(email_config_error)

    recipient = normalize_email(user.get('email'))
    if not recipient:
        raise RuntimeError('User account does not have a valid email address.')
    safe_name = escape_html(str(user.get('name', 'User')), quote=True)
    safe_link = escape_html(reset_link, quote=True)

    msg = Message(
        subject='Password Reset Request',
        sender=app.config.get('MAIL_DEFAULT_SENDER'),
        recipients=[recipient],
        body=(
            f"Hi {user.get('name', 'User')},\n\n"
            "You requested a password reset for your job platform account.\n\n"
            f"Reset your password using this link:\n{reset_link}\n\n"
            "This link will expire in 1 hour. If you did not request this, you can ignore this email.\n\n"
            "Job Platform Team"
        ),
        html=f'''
        <html>
            <body style="font-family: Arial, sans-serif;">
                <h2>Password Reset Request</h2>
                <p>Hi {safe_name},</p>
                <p>You requested a password reset for your job platform account.</p>
                <p>Click the link below to reset your password:</p>
                <p><a href="{safe_link}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Reset Password</a></p>
                <p>Or copy and paste this link in your browser:</p>
                <p>{safe_link}</p>
                <p>This link will expire in 1 hour.</p>
                <p>If you did not request this password reset, please ignore this email.</p>
                <p>Best regards,<br>Job Platform Team</p>
            </body>
        </html>
        '''
    )
    mail.send(msg)



@app.cli.command('seed-admin')
def seed_admin_command() -> None:
    """Create the first administrator from explicitly supplied environment values."""
    email = normalize_email(os.getenv('ADMIN_EMAIL'))
    password = os.getenv('ADMIN_PASSWORD', '')
    name = os.getenv('ADMIN_NAME', 'Platform Admin').strip()

    if not valid_email(email):
        raise RuntimeError('Set ADMIN_EMAIL to a valid address before seeding an administrator.')
    password_valid, password_error = validate_password(
        password,
        app.config['PASSWORD_MIN_LENGTH'],
    )
    if not password_valid:
        raise RuntimeError(f'ADMIN_PASSWORD is invalid: {password_error}')
    if account_collection('admin').count_documents({}, limit=1) or mongo.db.users.count_documents({'role': 'admin'}, limit=1):
        raise RuntimeError('An administrator already exists; refusing to replace credentials.')

    admin = {
            'name': name[:120],
            'email': email,
            'password': generate_password_hash(password),
            'role': 'admin',
            'skills': '',
            'experience': '',
            'company': '',
            'company_type': '',
            'location': '',
            'website': '',
            'hiring_roles': '',
            'profile_image': '',
            'resume': '',
            'resume_text': '',
            'resume_skills': [],
            'profile_keywords': [],
            'is_active': True,
            'created_at': utc_now(),
        }
    account_collection('admin').insert_one(admin)
    app.logger.info('Initial administrator provisioned from explicit CLI command.')


def get_saved_resume_text(user_doc):
    resume_url = user_doc.get('resume', '')
    filename = os.path.basename(resume_url.rstrip('/'))
    if not filename or filename != secure_filename(filename):
        return ''

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(file_path):
        return ''

    return extract_resume_text(file_path)


def coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def build_live_candidate_profile(user_doc, skills_text=None, resume_text=None):
    active_resume_text = user_doc.get('resume_text', '') if resume_text is None else resume_text
    if resume_text is None and not active_resume_text:
        active_resume_text = get_saved_resume_text(user_doc)

    profile = build_candidate_profile(
        user_doc.get('skills', '') if skills_text is None else skills_text,
        active_resume_text,
    )

    # Fall back to stored resume-only AI fields for users created before this logic was added.
    profile['manual_skills'] = profile.get('manual_skills') or normalize_skill_list(user_doc.get('skills', ''))
    if not profile['resume_skills'] and isinstance(user_doc.get('resume_skills'), list):
        profile['resume_skills'] = user_doc['resume_skills']
    profile['profile_keywords'] = coerce_string_list(profile.get('profile_keywords'))
    if not profile['profile_keywords']:
        profile['profile_keywords'] = coerce_string_list(user_doc.get('profile_keywords')) or profile['resume_skills']
    profile['matching_keywords'] = list(dict.fromkeys(
        profile['manual_skills'] + profile['profile_keywords']
    ))

    return profile

# JWT decorator
def _legacy_token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            token = token.split(' ')[1]  # Bearer <token>
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = find_user_by_id(ObjectId(data['user_id']))
            if not current_user:
                return jsonify({'error': 'User not found'}), 401
            g.current_user = current_user
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        authorization = request.headers.get('Authorization', '')
        scheme, separator, token = authorization.partition(' ')
        if not authorization:
            return jsonify({'error': 'Token missing'}), 401
        if not separator or scheme.lower() != 'bearer' or not token.strip():
            return jsonify({'error': 'Invalid token'}), 401
        try:
            data = decode_access_token(token.strip(), app.config['SECRET_KEY'])
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401

        user_id = parse_object_id(data.get('user_id'))
        if not user_id:
            return jsonify({'error': 'Invalid token'}), 401
        current_user = find_user_by_id(user_id)
        if not current_user:
            return jsonify({'error': 'User not found'}), 401
        if current_user.get('is_active', True) is False:
            return jsonify({'error': 'Account disabled'}), 403
        if (
            'token_version' in data
            and data['token_version'] != current_user.get('token_version', 0)
        ):
            return jsonify({'error': 'Invalid token'}), 401
        g.current_user = current_user
        return f(*args, **kwargs)
    return decorated


def build_auth_response(user):
    user_id = str(user['_id'])
    token = issue_access_token(
        user_id,
        app.config['SECRET_KEY'],
        app.config['ACCESS_TOKEN_TTL_MINUTES'],
        int(user.get('token_version', 0)),
    )
    refresh_token = issue_refresh_token(
        user_id,
        app.config['SECRET_KEY'],
        app.config['REFRESH_TOKEN_TTL_DAYS'],
        mongo.db.refresh_sessions,
    )
    csrf_token = secrets.token_urlsafe(32)
    response = jsonify({
        'token': token,
        'user': {
            'id': user_id,
            'name': user.get('name'),
            'email': user.get('email'),
            'role': user.get('role'),
        },
    })
    response.set_cookie(
        app.config['REFRESH_COOKIE_NAME'],
        refresh_token,
        max_age=app.config['REFRESH_TOKEN_TTL_DAYS'] * 24 * 60 * 60,
        httponly=True,
        secure=app.config['REFRESH_COOKIE_SECURE'],
        samesite='Strict',
        path=app.config['REFRESH_COOKIE_PATH'],
    )
    response.set_cookie(
        app.config['REFRESH_CSRF_COOKIE_NAME'],
        csrf_token,
        max_age=app.config['REFRESH_TOKEN_TTL_DAYS'] * 24 * 60 * 60,
        httponly=False,
        secure=app.config['REFRESH_COOKIE_SECURE'],
        samesite='Strict',
        path='/',
    )
    response.headers['Cache-Control'] = 'no-store'
    return response


def clear_refresh_cookies(response):
    response.delete_cookie(
        app.config['REFRESH_COOKIE_NAME'],
        path=app.config['REFRESH_COOKIE_PATH'],
        secure=app.config['REFRESH_COOKIE_SECURE'],
        httponly=True,
        samesite='Strict',
    )
    response.delete_cookie(
        app.config['REFRESH_CSRF_COOKIE_NAME'],
        path='/',
        secure=app.config['REFRESH_COOKIE_SECURE'],
        httponly=False,
        samesite='Strict',
    )
    return response


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if g.current_user.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        if g.current_user.get('is_active', True) is False:
            return jsonify({'error': 'Account disabled'}), 403
        return f(*args, **kwargs)
    return decorated

# ------------------- AUTH ROUTES -------------------
@app.route('/api/auth/register', methods=['POST'])
@limiter.limit('5 per hour')
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    email = normalize_email(data.get('email'))
    password = data.get('password', '')
    role = data.get('role')
    fields = {
        key: value
        for key, value in data.items()
        if key in PROFILE_FIELD_LIMITS and key != 'email'
    }
    if not email or not valid_email(email):
        return jsonify({'error': 'A valid email is required'}), 400
    if not valid_text_fields(fields, PROFILE_FIELD_LIMITS):
        return jsonify({'error': 'One or more profile fields are invalid or too long'}), 400
    if not isinstance(data.get('name'), str) or not data['name'].strip():
        return jsonify({'error': 'Name is required'}), 400
    if not isinstance(role, str) or role not in {'jobseeker', 'recruiter'}:
        return jsonify({'error': 'Invalid role'}), 400
    valid_password, password_error = validate_password(password, app.config.get('PASSWORD_MIN_LENGTH', 8))
    if not valid_password:
        return jsonify({'error': password_error}), 400
    if find_user_by_email(email, role):
        return jsonify({'error': 'Email already exists for this account type'}), 400
    
    hashed_pw = generate_password_hash(password)
    user = {
        'name': data.get('name'),
        'email': email,
        'password': hashed_pw,
        'role': role,  # 'jobseeker' or 'recruiter'
        'skills': data.get('skills', ''),
        'experience': data.get('experience', ''),
        'company': data.get('company', ''),
        'company_type': data.get('company_type', ''),
        'location': data.get('location', ''),
        'website': data.get('website', ''),
        'hiring_roles': data.get('hiring_roles', ''),
        'profile_image': '',
        'profile_photo': None,
        'profile_photo_content_type': '',
        'resume': '',
        'resume_text': '',
        'resume_skills': [],
        'profile_keywords': [],
        'is_active': True,
        'token_version': 0,
        'created_at': utc_now()
    }
    result = account_collection(role).insert_one(user)
    user['_id'] = result.inserted_id
    log_activity('user_registered', 'user', result.inserted_id, {'name': user['name'], 'role': user['role']})
    return build_auth_response(user)

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit('10 per minute')
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    email = normalize_email(data.get('email'))
    password = data.get('password')
    role = data.get('role')
    
    if role not in ROLE_COLLECTIONS:
        return jsonify({'error': 'Invalid account type'}), 400
    user = find_user_by_email(email, role)
    stored_password = str(user.get('password') or '') if user else ''
    if not user or not isinstance(password, str) or not check_password_hash(stored_password, password):
        return jsonify({'error': 'Invalid credentials'}), 401
    if user.get('is_active', True) is False:
        return jsonify({'error': 'Account disabled. Please contact the administrator.'}), 403
    if user['role'] != role:
        return jsonify({'error': f'Not a {role} account'}), 401
    
    log_activity('user_login', 'user', user['_id'], {'role': user['role'], 'email': user['email']})
    return build_auth_response(user)


@app.route('/api/auth/refresh', methods=['POST'])
@limiter.limit('20 per hour')
def refresh_auth():
    if not valid_csrf_token(
        request.cookies.get(app.config['REFRESH_CSRF_COOKIE_NAME']),
        request.headers.get('X-CSRF-Token'),
    ):
        return jsonify({'error': 'Invalid CSRF token'}), 403

    refresh_token = request.cookies.get(app.config['REFRESH_COOKIE_NAME'])
    if not refresh_token:
        return jsonify({'error': 'Refresh token missing'}), 401
    try:
        payload = decode_refresh_token(refresh_token, app.config['SECRET_KEY'])
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid refresh token'}), 401

    user_id = parse_object_id(payload.get('sub'))
    if not user_id:
        return jsonify({'error': 'Invalid refresh token'}), 401
    user = find_user_by_id(user_id)
    if not user or user.get('is_active', True) is False:
        revoke_refresh_token(
            refresh_token,
            app.config['SECRET_KEY'],
            mongo.db.refresh_sessions,
        )
        return jsonify({'error': 'Invalid refresh token'}), 401

    consumed = consume_refresh_token(
        refresh_token,
        app.config['SECRET_KEY'],
        mongo.db.refresh_sessions,
    )
    if not consumed:
        return jsonify({'error': 'Refresh token expired or already used'}), 401
    return build_auth_response(user)


@app.route('/api/auth/logout', methods=['POST'])
@limiter.limit('20 per hour')
def logout_auth():
    refresh_token = request.cookies.get(app.config['REFRESH_COOKIE_NAME'])
    if refresh_token and not valid_csrf_token(
        request.cookies.get(app.config['REFRESH_CSRF_COOKIE_NAME']),
        request.headers.get('X-CSRF-Token'),
    ):
        return jsonify({'error': 'Invalid CSRF token'}), 403
    if refresh_token:
        revoke_refresh_token(
            refresh_token,
            app.config['SECRET_KEY'],
            mongo.db.refresh_sessions,
        )
    return clear_refresh_cookies(jsonify({'message': 'Logged out'}))


@app.route('/api/auth/me', methods=['GET'])
@token_required
def get_me():
    user = serialize_doc(g.current_user)
    if not user:
        return jsonify({'error': 'User not found'}), 401
    if isinstance(user.get('profile_photo'), (bytes, bytearray, Binary)):
        user['profile_image'] = url_for(
            'download_profile_image',
            user_id=str(g.current_user['_id']),
        )
        user.pop('profile_photo', None)
        user.pop('profile_photo_content_type', None)
    profile_image = user.get('profile_image', '')
    normalized_profile_image = (
        profile_image.replace('\\', '/')
        if isinstance(profile_image, str)
        else ''
    )
    legacy_image_prefixes = (
        '/static/profile-images/',
        'static/profile-images/',
        '/static/uploads/',
        'static/uploads/',
    )
    is_legacy_image_path = normalized_profile_image.startswith(legacy_image_prefixes)
    is_legacy_image_filename = (
        normalized_profile_image == os.path.basename(normalized_profile_image)
        and normalized_profile_image == secure_filename(normalized_profile_image)
    )
    if is_legacy_image_path or is_legacy_image_filename:
        filename = os.path.basename(normalized_profile_image)
        if filename and filename == secure_filename(filename):
            user['profile_image'] = url_for(
                'download_profile_image',
                user_id=str(g.current_user['_id']),
                filename=filename,
            )
    if user.get('role') == 'jobseeker':
        candidate_profile = build_live_candidate_profile(g.current_user)
        user['resume_skills'] = candidate_profile['resume_skills']
        user['profile_keywords'] = candidate_profile['profile_keywords']
    if 'password' in user:
        del user['password']
    if 'resume_text' in user:
        del user['resume_text']
    return jsonify(user)

@app.route('/api/auth/forgot-password', methods=['POST'])
@limiter.limit('5 per hour')
def forgot_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    email = normalize_email(data.get('email'))
    
    if not valid_email(email):
        return jsonify({'error': 'A valid email is required'}), 400

    email_config_error = get_email_config_error()
    if email_config_error:
        app.logger.error('Password reset email is unavailable: %s', email_config_error)
        if app.config['ALLOW_DEV_RESET_LINK']:
            user = find_user_by_email(email)
            response_message = {
                'message': 'If an account exists with this email, use the development reset link below.'
            }
            if user:
                reset_token = create_password_reset_token(user['_id'])
                response_message['dev_link'] = build_reset_link(reset_token)
            return jsonify(response_message), 200

        return jsonify({
            'error': 'Password reset is temporarily unavailable. Please try again later.'
        }), 503
    
    user = find_user_by_email(email)
    
    # Always return the same message for security (don't reveal if email exists)
    response_message = {
        'message': 'If an account exists with this email, a password reset link has been sent. Please check your email.'
    }
    
    if not user:
        # Don't reveal if email exists or not for security
        return jsonify(response_message), 200
    
    reset_token = None
    try:
        reset_token = create_password_reset_token(user['_id'])
        send_password_reset_email(user, build_reset_link(reset_token))
    except Exception:
        app.logger.exception('Failed to send password reset email.')
        if reset_token and app.config['ALLOW_DEV_RESET_LINK']:
            response_message['dev_link'] = build_reset_link(reset_token)
    
    return jsonify(response_message), 200

@app.route('/api/auth/verify-reset-token', methods=['POST'])
@limiter.limit('10 per hour')
def verify_reset_token():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    token = data.get('token')
    
    if not token:
        return jsonify({'error': 'Token is required'}), 400
    
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        if payload.get('type') != 'password_reset':
            raise jwt.InvalidTokenError('Invalid token type')
        
        user_id = parse_object_id(payload.get('user_id'))
        if not user_id:
            return jsonify({'error': 'Invalid token'}), 400

        user = find_user_by_id(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        return jsonify({'valid': True, 'email': user['email']})
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 400
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 400


@app.route('/api/auth/reset-password', methods=['POST'])
@limiter.limit('10 per hour')
def reset_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    token = data.get('token')
    password = data.get('password')

    if not token:
        return jsonify({'error': 'Token is required'}), 400
    if not password:
        return jsonify({'error': 'Password is required'}), 400
    valid_password, password_error = validate_password(password, app.config.get('PASSWORD_MIN_LENGTH', 8))
    if not valid_password:
        return jsonify({'error': password_error}), 400

    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        if payload.get('type') != 'password_reset':
            raise jwt.InvalidTokenError('Invalid token type')

        user_id = parse_object_id(payload.get('user_id'))
        if not user_id:
            return jsonify({'error': 'Invalid token'}), 400

        result = update_user_by_id(
            user_id,
            {
                '$set': {'password': generate_password_hash(password)},
                '$inc': {'token_version': 1},
            },
        )
        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404
        mongo.db.refresh_sessions.update_many(
            {'user_id': str(user_id), 'revoked_at': None},
            {'$set': {'revoked_at': utc_now()}},
        )

        log_activity('password_reset', 'user', user_id)
        return jsonify({'message': 'Password reset successfully'})
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 400
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 400


@app.route('/api/test-email', methods=['POST'])
@limiter.limit('5 per hour')
@admin_required
def test_email():
    """Send an operator-authorized test message without creating reset tokens."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    recipient = normalize_email(data.get('email'))
    if not valid_email(recipient):
        return jsonify({'error': 'A valid email is required'}), 400

    try:
        email_config_error = get_email_config_error()
        if email_config_error:
            app.logger.error('Test email is unavailable: %s', email_config_error)
            return jsonify({'error': 'Email service is not configured'}), 503

        msg = Message(
            subject='Test Email - Job Platform',
            sender=app.config['MAIL_DEFAULT_SENDER'],
            recipients=[recipient],
            html=f'''
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Test Email Successful! 🎉</h2>
                    <p>This is a test from your Job Platform.</p>
                    <p>If you received this, email is working!</p>
                    <p>Time: {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
                </body>
            </html>
            '''
        )
        mail.send(msg)
        return jsonify({
            'success': True,
            'message': 'Test email sent',
        })
    except Exception:
        app.logger.exception('Test email delivery failed.')
        return jsonify({
            'success': False,
            'error': 'Email delivery failed',
        }), 502


# ------------------- ADMIN ROUTES -------------------
@app.route('/api/admin/summary', methods=['GET'])
@admin_required
def admin_summary():
    pipeline_stats = {
        'applied': mongo.db.applications.count_documents({'status': 'applied'}),
        'reviewed': mongo.db.applications.count_documents({'status': 'reviewed'}),
        'shortlisted': mongo.db.applications.count_documents({'status': 'shortlisted'}),
        'interviewed': mongo.db.applications.count_documents({'status': 'interviewed'}),
        'offered': mongo.db.applications.count_documents({'status': 'offered'}),
        'hired': mongo.db.applications.count_documents({'status': 'hired'}),
        'rejected': mongo.db.applications.count_documents({'status': 'rejected'})
    }
    summary = {
        'totalUsers': len(all_account_users()),
        'activeUsers': sum(1 for user in all_account_users() if user.get('is_active', True) is not False),
        'jobseekers': len(all_account_users({'role': 'jobseeker'})),
        'recruiters': len(all_account_users({'role': 'recruiter'})),
        'admins': len(all_account_users({'role': 'admin'})),
        'jobs': mongo.db.jobs.count_documents({}),
        'applications': mongo.db.applications.count_documents({}),
        'pipeline': pipeline_stats,
        'recentUsers': [
            sanitize_user_doc(user)
            for user in sorted(all_account_users(), key=lambda item: item.get('created_at') or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True)[:5]
        ],
        'recentJobs': [
            serialize_value(job)
            for job in mongo.db.jobs.find().sort('created_at', -1).limit(5)
        ]
    }
    return jsonify(summary)


@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_users():
    users = []
    for user in sorted(all_account_users(), key=lambda item: item.get('created_at') or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc), reverse=True):
        users.append(sanitize_user_doc(user))
    return jsonify(users)


@app.route('/api/admin/users/<user_id>/status', methods=['PUT'])
@admin_required
def admin_update_user_status(user_id):
    target_id = parse_object_id(user_id)
    if not target_id:
        return jsonify({'error': 'Invalid user id'}), 400

    if target_id == g.current_user['_id']:
        return jsonify({'error': 'You cannot change your own admin status from the admin panel'}), 400

    payload = request.get_json(silent=True) or {}
    is_active = payload.get('is_active')
    if not isinstance(is_active, bool):
        return jsonify({'error': 'is_active must be a boolean'}), 400

    target_user = find_user_by_id(target_id)
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    if target_user.get('role') == 'admin' and is_active is False:
        active_admins = sum(1 for user in all_account_users({'role': 'admin'}) if user.get('is_active', True) is not False)
        if active_admins <= 1:
            return jsonify({'error': 'At least one active admin account is required'}), 400

    update_user_by_id(target_id, {'$set': {'is_active': is_active}})
    log_activity('user_status_changed', 'user', target_id, {'is_active': is_active, 'changed_by': str(g.current_user['_id'])}, g.current_user['_id'])
    return jsonify({'message': 'User status updated'})


@app.route('/api/admin/jobs', methods=['GET'])
@admin_required
def admin_jobs():
    jobs = []
    for job in mongo.db.jobs.find().sort('created_at', -1):
        recruiter = find_user_by_id(job.get('recruiter_id')) if job.get('recruiter_id') else None
        formatted_job = serialize_value(job)
        formatted_job['recruiter_name'] = recruiter['name'] if recruiter else 'Unknown recruiter'
        jobs.append(formatted_job)
    return jsonify(jobs)


@app.route('/api/admin/jobs/<job_id>', methods=['DELETE'])
@admin_required
def admin_delete_job(job_id):
    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400

    result = mongo.db.jobs.delete_one({'_id': target_id})
    if result.deleted_count == 0:
        return jsonify({'error': 'Job not found'}), 404

    mongo.db.applications.delete_many({'job_id': target_id})
    log_activity('job_deleted', 'job', target_id, {'deleted_by': str(g.current_user['_id'])}, g.current_user['_id'])
    return jsonify({'message': 'Job removed successfully'})


@app.route('/api/admin/applications', methods=['GET'])
@admin_required
def admin_applications():
    pipeline = [
        {'$lookup': {
            'from': 'jobs',
            'localField': 'job_id',
            'foreignField': '_id',
            'as': 'job'
        }},
        {'$unwind': {'path': '$job', 'preserveNullAndEmptyArrays': True}},
        {'$sort': {'applied_at': -1}}
    ]

    applications = []
    for application in mongo.db.applications.aggregate(pipeline):
        seeker = find_user_by_id(application.get('seeker_id')) if application.get('seeker_id') else None
        formatted_application = serialize_value(application)
        formatted_application['job_title'] = application.get('job', {}).get('title', 'Removed job')
        formatted_application['applicant_name'] = seeker.get('name', 'Removed user') if seeker else 'Removed user'
        formatted_application['seeker'] = sanitize_user_doc(seeker) if seeker else {}
        applications.append(formatted_application)
    return jsonify(applications)

@app.route('/api/admin/applications/<application_id>/status', methods=['PUT'])
@admin_required
def admin_update_application_status(application_id):
    target_id = parse_object_id(application_id)
    if not target_id:
        return jsonify({'error': 'Invalid application id'}), 400
    payload = request.get_json(silent=True) or {}
    new_status = payload.get('status')
    valid_statuses = ['applied', 'reviewed', 'shortlisted', 'interviewed', 'offered', 'hired', 'rejected']
    if new_status not in valid_statuses:
        return jsonify({'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}), 400
    application = mongo.db.applications.find_one({'_id': target_id})
    if not application:
        return jsonify({'error': 'Application not found'}), 404
    old_status = application.get('status', 'applied')
    mongo.db.applications.update_one({'_id': target_id}, {'$set': {'status': new_status, 'updated_at': utc_now()}})
    log_activity('application_status_changed', 'application', target_id, {'old_status': old_status, 'new_status': new_status, 'changed_by': str(g.current_user['_id'])}, g.current_user['_id'])
    return jsonify({'message': f'Application status updated to {new_status}'})


@app.route('/api/admin/activity-logs', methods=['GET'])
@admin_required
def admin_activity_logs():
    limit = request.args.get('limit', 50, type=int)
    entity_type = request.args.get('entity_type')
    query = {}
    if entity_type:
        query['entity_type'] = entity_type
    logs = list(mongo.db.activity_logs.find(query).sort('created_at', -1).limit(limit))
    for log in logs:
        log['_id'] = str(log['_id'])
    return jsonify(logs)


@app.route('/api/admin/process', methods=['GET'])
@admin_required
def admin_process():
    today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - datetime.timedelta(days=7)
    month_ago = today - datetime.timedelta(days=30)
    process_data = {
        'pipeline': {
            'applied': mongo.db.applications.count_documents({'status': 'applied'}),
            'reviewed': mongo.db.applications.count_documents({'status': 'reviewed'}),
            'shortlisted': mongo.db.applications.count_documents({'status': 'shortlisted'}),
            'interviewed': mongo.db.applications.count_documents({'status': 'interviewed'}),
            'offered': mongo.db.applications.count_documents({'status': 'offered'}),
            'hired': mongo.db.applications.count_documents({'status': 'hired'}),
            'rejected': mongo.db.applications.count_documents({'status': 'rejected'})
        },
        'timeSeries': {
            'today': {
                'users': sum(1 for user in all_account_users() if user.get('created_at') and user['created_at'] >= today),
                'jobs': mongo.db.jobs.count_documents({'created_at': {'$gte': today}}),
                'applications': mongo.db.applications.count_documents({'applied_at': {'$gte': today}})
            },
            'thisWeek': {
                'users': sum(1 for user in all_account_users() if user.get('created_at') and user['created_at'] >= week_ago),
                'jobs': mongo.db.jobs.count_documents({'created_at': {'$gte': week_ago}}),
                'applications': mongo.db.applications.count_documents({'applied_at': {'$gte': week_ago}})
            },
            'thisMonth': {
                'users': sum(1 for user in all_account_users() if user.get('created_at') and user['created_at'] >= month_ago),
                'jobs': mongo.db.jobs.count_documents({'created_at': {'$gte': month_ago}}),
                'applications': mongo.db.applications.count_documents({'applied_at': {'$gte': month_ago}})
            }
        },
        'topJobs': [],
        'topRecruiters': []
    }
    top_jobs_pipeline = [{'$group': {'_id': '$job_id', 'count': {'$sum': 1}}}, {'$sort': {'count': -1}}, {'$limit': 5}]
    for item in mongo.db.applications.aggregate(top_jobs_pipeline):
        job = mongo.db.jobs.find_one({'_id': item['_id']})
        process_data['topJobs'].append({'job_id': str(item['_id']), 'title': job['title'] if job else 'Removed job', 'application_count': item['count']})
    top_recruiters_pipeline = [{'$group': {'_id': '$recruiter_id', 'count': {'$sum': 1}}}, {'$sort': {'count': -1}}, {'$limit': 5}]
    for item in mongo.db.jobs.aggregate(top_recruiters_pipeline):
        recruiter = find_user_by_id(item['_id'])
        process_data['topRecruiters'].append({'recruiter_id': str(item['_id']), 'name': recruiter['name'] if recruiter else 'Unknown', 'job_count': item['count']})
    return jsonify(process_data)

# ------------------- PROFILE ROUTES -------------------
@app.route('/api/users/profile', methods=['PUT'])
@token_required
def update_profile():
    data = request.form
    update_fields = {}
    allowed = ['name', 'skills', 'experience', 'company', 'company_type', 'location', 'website', 'hiring_roles', 'email']
    for field in allowed:
        if field in data:
            update_fields[field] = data[field]
    if not valid_text_fields(update_fields, PROFILE_FIELD_LIMITS):
        return jsonify({'error': 'One or more profile fields are invalid or too long'}), 400
    if 'email' in update_fields:
        email = normalize_email(update_fields['email'])
        if not valid_email(email):
            return jsonify({'error': 'A valid email is required'}), 400
        existing_user = find_user_by_email(email, g.current_user.get('role'))
        if existing_user and existing_user['_id'] != g.current_user['_id']:
            return jsonify({'error': 'Email already exists'}), 400
        update_fields['email'] = email
    if 'name' in update_fields and not update_fields['name'].strip():
        return jsonify({'error': 'Name cannot be empty'}), 400
    if update_fields.get('website') and not valid_http_url(update_fields['website']):
        return jsonify({'error': 'Website must be a valid HTTP or HTTPS URL'}), 400
    
    # Handle profile image upload
    if 'profile_image' in request.files:
        file = request.files['profile_image']
        if file and file.filename:
            extension = validate_upload_file(
                file,
                {'.jpg', '.jpeg', '.png', '.gif'},
                app.config['MAX_PROFILE_IMAGE_SIZE'],
            )
            if not extension:
                return jsonify({
                    'error': 'Only valid JPG, JPEG, PNG, and GIF files up to 2 MB are allowed for profile photos'
                }), 400
            photo_bytes = file.read()
            content_type = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
            }[os.path.splitext(extension)[1].lower()]
            update_fields['profile_photo'] = Binary(photo_bytes)
            update_fields['profile_photo_content_type'] = content_type
            update_fields['profile_image'] = url_for(
                'download_profile_image',
                user_id=str(g.current_user['_id']),
            )
    
    # Handle resume upload
    if 'resume' in request.files:
        file = request.files['resume']
        if file and file.filename:
            try:
                filename, saved_path = save_private_upload(
                    file,
                    app.config['UPLOAD_FOLDER'],
                    {'.pdf', '.doc', '.docx'},
                    app.config['MAX_RESUME_SIZE'],
                )
            except InvalidUploadError:
                return jsonify({
                    'error': 'Only valid PDF, DOC, and DOCX files up to 10 MB are allowed for resumes'
                }), 400
            update_fields['resume'] = url_for(
                'download_resume',
                user_id=str(g.current_user['_id']),
                filename=filename,
            )
            if g.current_user.get('role') == 'jobseeker':
                update_fields['resume_text'] = extract_resume_text(saved_path)

    ai_payload = {}
    if g.current_user.get('role') == 'jobseeker':
        active_skills = update_fields.get('skills', g.current_user.get('skills', ''))
        active_resume_text = update_fields['resume_text'] if 'resume_text' in update_fields else None
        candidate_profile = build_live_candidate_profile(
            g.current_user,
            skills_text=active_skills,
            resume_text=active_resume_text,
        )
        if 'resume_text' in update_fields and not update_fields['resume_text']:
            candidate_profile['resume_skills'] = []
            candidate_profile['profile_keywords'] = []
        update_fields['resume_skills'] = candidate_profile['resume_skills']
        update_fields['profile_keywords'] = candidate_profile['profile_keywords']
        ai_payload = {
            'manual_skills': candidate_profile['manual_skills'],
            'resume_skills': candidate_profile['resume_skills'],
            'profile_keywords': candidate_profile['profile_keywords'],
            'resume_parsed': bool(update_fields.get('resume_text')),
            'resume_parse_message': (
                'Resume NLP/OCR skill extraction completed successfully.'
                if 'resume_text' in update_fields and update_fields['resume_text']
                else 'Resume uploaded, but NLP/OCR could not extract readable text from it.'
                if 'resume' in request.files and 'resume_text' in update_fields
                else 'Profile updated. AI matching now uses resume-extracted skills only.'
            ),
        }
    
    update_user_by_id(g.current_user['_id'], {'$set': update_fields})
    log_activity('profile_updated', 'user', g.current_user['_id'], {'fields': list(update_fields.keys())}, g.current_user['_id'])
    return jsonify({
        'message': 'Profile updated',
        'profile_image': update_fields.get(
            'profile_image',
            g.current_user.get('profile_image', ''),
        ),
        **ai_payload,
    })



@app.route('/api/users/<user_id>/resume/<path:filename>', methods=['GET'])
@token_required
def download_resume(user_id, filename):
    current_user_id = str(g.current_user['_id'])
    role = g.current_user.get('role')
    if current_user_id != user_id and role not in {'recruiter', 'admin'}:
        return jsonify({'error': 'Access denied'}), 403
    safe_name = os.path.basename(filename)
    if safe_name != secure_filename(safe_name):
        return jsonify({'error': 'Invalid file name'}), 400
    if os.path.splitext(safe_name)[1].lower() not in {'.pdf', '.doc', '.docx'}:
        return jsonify({'error': 'Invalid resume file type'}), 400
    target_user_id = parse_object_id(user_id)
    if not target_user_id:
        return jsonify({'error': 'Resume not found'}), 404
    user = find_user_by_id(target_user_id)
    if not user or user.get('role') != 'jobseeker' or user.get('resume', '').rstrip('/').split('/')[-1] != safe_name:
        return jsonify({'error': 'Resume not found'}), 404
    if role == 'recruiter' and current_user_id != user_id:
        recruiter_job_ids = [
            job['_id']
            for job in mongo.db.jobs.find(
                {'recruiter_id': g.current_user['_id']},
                {'_id': 1},
            )
        ]
        if not recruiter_job_ids or not mongo.db.applications.find_one({
            'seeker_id': target_user_id,
            'job_id': {'$in': recruiter_job_ids},
        }):
            return jsonify({'error': 'Access denied'}), 403
    if role == 'admin' and g.current_user.get('is_active', True) is False:
        return jsonify({'error': 'Access denied'}), 403
    response = send_from_directory(
        app.config['UPLOAD_FOLDER'],
        safe_name,
        as_attachment=True,
    )
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@app.route('/api/users/<user_id>/profile-image', defaults={'filename': None}, methods=['GET'])
@app.route('/api/users/<user_id>/profile-image/<path:filename>', methods=['GET'])
@token_required
def download_profile_image(user_id, filename):
    if str(g.current_user['_id']) != user_id and g.current_user.get('role') != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    safe_name = os.path.basename(filename)
    if safe_name != secure_filename(safe_name):
        return jsonify({'error': 'Invalid file name'}), 400
    user = find_user_by_id(parse_object_id(user_id))
    stored_image = user.get('profile_image', '') if user else ''
    stored_photo = user.get('profile_photo') if user else None
    if isinstance(stored_photo, (bytes, bytearray, Binary)):
        if filename is not None:
            return jsonify({'error': 'Profile image not found'}), 404
        content_type = user.get('profile_photo_content_type', 'image/jpeg')
        if content_type not in {'image/jpeg', 'image/png', 'image/gif'}:
            content_type = 'image/jpeg'
        response = Response(bytes(stored_photo), mimetype=content_type)
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response
    if filename is None:
        return jsonify({'error': 'Profile image not found'}), 404
    stored_filename = (
        stored_image.replace('\\', '/').rstrip('/').split('/')[-1]
        if isinstance(stored_image, str)
        else ''
    )
    if not user or stored_filename != safe_name:
        return jsonify({'error': 'Profile image not found'}), 404

    normalized_image = stored_image.replace('\\', '/').lstrip('/')
    legacy_upload = normalized_image.startswith('static/uploads/')
    image_folder = (
        app.config['UPLOAD_FOLDER']
        if legacy_upload
        else app.config['PROFILE_IMAGE_FOLDER']
    )
    private_path = os.path.join(image_folder, safe_name)
    if not os.path.isfile(private_path) and not legacy_upload:
        legacy_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
        if os.path.isfile(legacy_path):
            image_folder = app.config['UPLOAD_FOLDER']
            private_path = legacy_path
    if not os.path.isfile(private_path):
        return jsonify({'error': 'Profile image not found'}), 404
    response = send_from_directory(image_folder, safe_name)
    response.headers['Cache-Control'] = 'private, no-store'
    return response

# ------------------- JOBS ROUTES -------------------
@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    search = request.args.get('search', '')
    location = request.args.get('location', '')
    if len(search) > 100 or len(location) > 200:
        return jsonify({'error': 'Search and location filters are too long'}), 400
    query = {}
    if search:
        query['$or'] = [
            {'title': {'$regex': re.escape(search), '$options': 'i'}},
            {'description': {'$regex': re.escape(search), '$options': 'i'}}
        ]
    if location and location != '':
        query['location'] = location
    
    jobs = list(mongo.db.jobs.find(query).sort('created_at', -1))
    for job in jobs:
        recruiter_id = job.get('recruiter_id')
        job['_id'] = str(job['_id'])
        recruiter = find_user_by_id(recruiter_id) if recruiter_id else None
        if isinstance(recruiter_id, ObjectId):
            job['recruiter_id'] = str(recruiter_id)
        job['recruiter_name'] = recruiter['name'] if recruiter else ''
    return jsonify(jobs)

@app.route('/api/jobs', methods=['POST'])
@token_required
def post_job():
    if g.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Only recruiters can post jobs'}), 403
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    if not valid_text_fields(data, JOB_FIELD_LIMITS):
        return jsonify({'error': 'One or more job fields are invalid or too long'}), 400
    if any(
        not isinstance(data.get(field), str) or not data[field].strip()
        for field in ('title', 'company', 'location')
    ):
        return jsonify({'error': 'Title, company, and location are required'}), 400
    job = {
        'title': data.get('title'),
        'company': data.get('company'),
        'location': data.get('location'),
        'description': data.get('description', ''),
        'skills': data.get('skills', ''),
        'recruiter_id': g.current_user['_id'],
        'created_at': utc_now()
    }
    result = mongo.db.jobs.insert_one(job)
    log_activity('job_posted', 'job', result.inserted_id, {'title': job['title'], 'company': job['company']}, g.current_user['_id'])
    return jsonify({'id': str(result.inserted_id), 'message': 'Job posted'})

@app.route('/api/recruiter/jobs', methods=['GET'])
@token_required
def get_recruiter_jobs():
    if g.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403
    jobs = list(mongo.db.jobs.find({'recruiter_id': g.current_user['_id']}).sort('created_at', -1))
    for job in jobs:
        job['_id'] = str(job['_id'])
        if isinstance(job.get('recruiter_id'), ObjectId):
            job['recruiter_id'] = str(job['recruiter_id'])
    return jsonify(jobs)


@app.route('/api/recruiter/jobs/<job_id>', methods=['PUT', 'DELETE'])
@token_required
def manage_recruiter_job(job_id):
    if g.current_user.get('role') != 'recruiter':
        return jsonify({'error': 'Recruiter access required'}), 403
    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400
    job_filter = {'_id': target_id, 'recruiter_id': g.current_user['_id']}
    job = mongo.db.jobs.find_one(job_filter)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    if request.method == 'DELETE':
        mongo.db.applications.delete_many({'job_id': target_id})
        mongo.db.jobs.delete_one(job_filter)
        log_activity('job_deleted', 'job', target_id, {'deleted_by': str(g.current_user['_id'])}, g.current_user['_id'])
        return jsonify({'message': 'Job deleted'})

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON object is required'}), 400
    allowed_fields = {'title', 'company', 'location', 'description', 'skills'}
    updates = {key: value for key, value in data.items() if key in allowed_fields}
    if not updates:
        return jsonify({'error': 'At least one editable job field is required'}), 400
    if not valid_text_fields(updates, JOB_FIELD_LIMITS):
        return jsonify({'error': 'One or more job fields are invalid or too long'}), 400
    if any(
        key in updates and (not isinstance(updates[key], str) or not updates[key].strip())
        for key in ('title', 'company', 'location')
    ):
        return jsonify({'error': 'Title, company, and location cannot be empty'}), 400
    updates['updated_at'] = utc_now()
    mongo.db.jobs.update_one(job_filter, {'$set': updates})
    log_activity('job_updated', 'job', target_id, {'fields': list(updates)}, g.current_user['_id'])
    return jsonify({'message': 'Job updated'})

@app.route('/api/jobs/<job_id>/apply', methods=['POST'])
@token_required
def apply_job(job_id):
    if g.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Only job seekers can apply'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400

    job = mongo.db.jobs.find_one({'_id': target_id})
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    existing = mongo.db.applications.find_one({
        'job_id': target_id,
        'seeker_id': g.current_user['_id']
    })
    if existing:
        return jsonify({'error': 'Already applied'}), 400

    candidate_profile = build_live_candidate_profile(g.current_user)
    job_profile = build_job_profile(
        skills_text=job.get('skills', ''),
        title=job.get('title', ''),
        description=job.get('description', ''),
    )
    candidate_keywords = coerce_string_list(candidate_profile.get('matching_keywords'))
    match_keywords = coerce_string_list(job_profile.get('match_keywords'))
    inferred_keywords = coerce_string_list(job_profile.get('inferred_keywords'))
    candidate_text = str(candidate_profile.get('semantic_text') or '')
    job_text = str(job_profile.get('semantic_text') or '')
    match = calculate_keyword_match(
        candidate_keywords,
        match_keywords,
        inferred_keywords,
        candidate_text,
        job_text,
    )
    if match.get('matchPercentage', 0) < MIN_MATCH_PERCENTAGE_TO_APPLY:
        return jsonify({'error': f'Cannot apply: job match must be at least {MIN_MATCH_PERCENTAGE_TO_APPLY}%.'}), 400
    
    application = {
        'job_id': target_id,
        'seeker_id': g.current_user['_id'],
        'status': 'applied',
        'applied_at': utc_now()
    }
    mongo.db.applications.insert_one(application)
    return jsonify({'message': 'Application submitted'})

@app.route('/api/jobs/<job_id>/unapply', methods=['DELETE'])
@token_required
def unapply_job(job_id):
    if g.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Only job seekers can withdraw applications'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400
    
    result = mongo.db.applications.delete_one({
        'job_id': target_id,
        'seeker_id': g.current_user['_id']
    })
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Application not found'}), 404
    
    return jsonify({'message': 'Application withdrawn'})

@app.route('/api/jobs/applied', methods=['GET'])
@token_required
def get_applied_jobs():
    if g.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Access denied'}), 403
    
    pipeline = [
        {'$match': {'seeker_id': g.current_user['_id']}},
        {'$lookup': {
            'from': 'jobs',
            'localField': 'job_id',
            'foreignField': '_id',
            'as': 'job'
        }},
        {'$unwind': '$job'},
        {'$sort': {'applied_at': -1}}
    ]
    results = list(mongo.db.applications.aggregate(pipeline))
    for r in results:
        r['_id'] = str(r['_id'])
        r['job']['_id'] = str(r['job']['_id'])
        if isinstance(r['job'].get('recruiter_id'), ObjectId):
            r['job']['recruiter_id'] = str(r['job']['recruiter_id'])
        r['job_id'] = str(r['job_id'])
        r['seeker_id'] = str(r['seeker_id'])
    return jsonify(results)

@app.route('/api/jobs/recommended', methods=['GET'])
@token_required
def recommended_jobs():
    if g.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Access denied'}), 403
    
    candidate_profile = build_live_candidate_profile(g.current_user)
    applied_job_ids = {
        str(application['job_id'])
        for application in mongo.db.applications.find(
            {'seeker_id': g.current_user['_id']},
            {'job_id': 1}
        )
        if application.get('job_id')
    }
    jobs = list(mongo.db.jobs.find())
    for job in jobs:
        job_profile = build_job_profile(
            skills_text=job.get('skills', ''),
            title=job.get('title', ''),
            description=job.get('description', ''),
        )
        candidate_keywords = coerce_string_list(candidate_profile.get('matching_keywords'))
        match_keywords = coerce_string_list(job_profile.get('match_keywords'))
        inferred_keywords = coerce_string_list(job_profile.get('inferred_keywords'))
        candidate_text = str(candidate_profile.get('semantic_text') or '')
        job_text = str(job_profile.get('semantic_text') or '')
        match = calculate_keyword_match(
            candidate_keywords,
            match_keywords,
            inferred_keywords,
            candidate_text,
            job_text,
        )
        job['_id'] = str(job['_id'])
        job.update(match)
        job['alreadyApplied'] = job['_id'] in applied_job_ids
        if isinstance(job.get('recruiter_id'), ObjectId):
            job['recruiter_id'] = str(job['recruiter_id'])
    jobs.sort(key=lambda x: x.get('matchPercentage', 0), reverse=True)
    return jsonify(jobs)

@app.route('/api/recruiter/jobs/<job_id>/applicants', methods=['GET'])
@token_required
def get_applicants(job_id):
    if g.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400
    
    job = mongo.db.jobs.find_one({'_id': target_id, 'recruiter_id': g.current_user['_id']})
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    job_profile = build_job_profile(
        skills_text=job.get('skills', ''),
        title=job.get('title', ''),
        description=job.get('description', ''),
    )
    applicants = []
    for application in mongo.db.applications.find({'job_id': target_id}).sort('applied_at', -1):
        seeker = find_user_by_id(application.get('seeker_id'))
        if seeker and seeker.get('role') == 'jobseeker':
            application['seeker'] = seeker
            applicants.append(application)
    for app in applicants:
        app['_id'] = str(app['_id'])
        app['job_id'] = str(app['job_id'])
        app['seeker_id'] = str(app['seeker_id'])
        app['seeker']['_id'] = str(app['seeker']['_id'])
        candidate_profile = build_live_candidate_profile(app['seeker'])
        candidate_keywords = coerce_string_list(candidate_profile.get('matching_keywords'))
        match_keywords = coerce_string_list(job_profile.get('match_keywords'))
        inferred_keywords = coerce_string_list(job_profile.get('inferred_keywords'))
        candidate_text = str(candidate_profile.get('semantic_text') or '')
        job_text = str(job_profile.get('semantic_text') or '')
        app.update(
            calculate_keyword_match(
                candidate_keywords,
                match_keywords,
                inferred_keywords,
                candidate_text,
                job_text,
            )
        )
        if 'resume_text' in app['seeker']:
            del app['seeker']['resume_text']
    return jsonify(applicants)

@app.route('/api/recruiter/applicants', methods=['GET'])
@token_required
def get_recruiter_applicants():
    if g.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403

    # Get all jobs for this recruiter
    recruiter_jobs = list(mongo.db.jobs.find({'recruiter_id': g.current_user['_id']}, {'_id': 1, 'title': 1, 'skills': 1, 'description': 1}))
    
    all_applicants = []
    for job in recruiter_jobs:
        job_profile = build_job_profile(
            skills_text=job.get('skills', ''),
            title=job.get('title', ''),
            description=job.get('description', ''),
        )
        applicants = []
        for application in mongo.db.applications.find({'job_id': job['_id']}).sort('applied_at', -1):
            seeker = find_user_by_id(application.get('seeker_id'))
            if seeker and seeker.get('role') == 'jobseeker':
                application['seeker'] = seeker
                applicants.append(application)
        for app in applicants:
            app['_id'] = str(app['_id'])
            app['job_id'] = str(app['job_id'])
            app['seeker_id'] = str(app['seeker_id'])
            app['seeker']['_id'] = str(app['seeker']['_id'])
            app['job_title'] = job['title']
            candidate_profile = build_live_candidate_profile(app['seeker'])
            candidate_keywords = coerce_string_list(candidate_profile.get('matching_keywords'))
            match_keywords = coerce_string_list(job_profile.get('match_keywords'))
            inferred_keywords = coerce_string_list(job_profile.get('inferred_keywords'))
            candidate_text = str(candidate_profile.get('semantic_text') or '')
            job_text = str(job_profile.get('semantic_text') or '')
            match = calculate_keyword_match(
                candidate_keywords,
                match_keywords,
                inferred_keywords,
                candidate_text,
                job_text,
            )
            app.update(match)
            if 'resume_text' in app['seeker']:
                del app['seeker']['resume_text']
            all_applicants.append(app)
    
    # Sort by match percentage descending
    all_applicants.sort(key=lambda x: x.get('matchPercentage', 0), reverse=True)
    return jsonify(all_applicants)

# ------------------- NOTIFICATIONS -------------------
@app.route('/api/notifications', methods=['POST'])
@token_required
def send_notification():
    if g.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Only recruiters can send notifications'}), 403
    
    data = request.get_json(silent=True) or {}
    user_ids = data.get('user_ids')
    message = data.get('message')
    job_title = data.get('job_title', '')
    status = data.get('status', 'update')
    
    if not user_ids or not message:
        return jsonify({'error': 'Missing fields'}), 400
    
    notifications = []
    for uid in user_ids:
        user_id = parse_object_id(uid)
        if not user_id:
            return jsonify({'error': 'Invalid user id'}), 400
        notifications.append({
            'user_id': user_id,
            'message': message,
            'job_title': job_title,
            'status': status,
            'is_read': False,
            'created_at': utc_now()
        })
    
    try:
        result = mongo.db.notifications.insert_many(notifications)
        log_activity('send_notification', 'notification', None, {
            'notification_ids': [str(id) for id in result.inserted_ids],
            'recipient_count': len(notifications)
        }, user_id=g.current_user['_id'])
        return jsonify({
            'message': 'Notifications sent successfully',
            'count': len(notifications)
        })
    except Exception as e:
        log_activity('send_notification_error', 'notification', None, {
            'error': str(e),
            'recipient_count': len(notifications)
        }, user_id=g.current_user['_id'])
        return jsonify({'error': f'Failed to send notifications: {str(e)}'}), 500

@app.route('/api/notifications', methods=['GET'])
@token_required
def get_notifications():
    try:
        notifs = list(mongo.db.notifications.find({'user_id': g.current_user['_id']}).sort('created_at', -1))
        for n in notifs:
            n['_id'] = str(n['_id'])
            n['user_id'] = str(n['user_id'])
        return jsonify(notifs)
    except Exception as e:
        log_activity('get_notifications_error', 'notification', None, {
            'error': str(e)
        }, user_id=g.current_user['_id'])
        return jsonify({'error': f'Failed to fetch notifications: {str(e)}'}), 500

# ------------------- SERVE HTML PAGES -------------------
@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/pawel-czerwinski-2CDRjyQw8aQ-unsplash.jpg')
def hero_background():
    return send_from_directory(app.root_path, 'pawel-czerwinski-2CDRjyQw8aQ-unsplash.jpg')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/register/seeker')
def register_jobseeker_page():
    return render_template('register_jobseeker.html')

@app.route('/register/recruiter')
def register_recruiter_page():
    return render_template('register_recruiter.html')

@app.route('/dashboard')
def jobseeker_home():
    return render_template('jobseeker_home.html')

@app.route('/profile')
def jobseeker_profile():
    return render_template('jobseeker_profile.html')

@app.route('/dashboard/recruiter')
def recruiter_home():
    return render_template('recruiter_home.html')

@app.route('/recruiter/profile')
def recruiter_profile():
    return render_template('recruiter_profile.html')

@app.route('/jobs')
def browse_jobs():
    return render_template(
        'browse_jobs.html',
        min_match_percentage=MIN_MATCH_PERCENTAGE_TO_APPLY,
    )

@app.route('/jobs/apply')
def job_apply():
    return render_template('job_apply.html')

@app.route('/dashboard/seeker')
def seeker_dashboard():
    return render_template('seeker_dashboard.html')

@app.route('/dashboard/recruiter/overview')
def recruiter_dashboard():
    return render_template('recruiter_dashboard.html')

@app.route('/recruiter/jobs/new')
def post_job_page():
    return render_template('post_job.html')

@app.route('/recruiter/jobs')
def manage_jobs_page():
    return render_template('manage_jobs.html')

@app.route('/recruiter/applicants')
def applicants_page():
    return render_template('applicants.html')

@app.route('/recruiter/notifications')
def send_notifications_page():
    return render_template('send_notifications.html')

@app.route('/notifications/recruiter')
def notify_recruiter_page():
    return render_template('notify_recruiter.html')

@app.route('/notifications')
def notifications_page():
    return render_template('notifications.html')

@app.route('/jobs/applied')
def applied_jobs_page():
    return render_template('applied_jobs.html')

@app.route('/dashboard/admin')
def admin_dashboard_page():
    return render_template('admin_dashboard.html')

@app.route("/forgot-password")
def forgot_password_page():
    return render_template("forgot-password.html")

@app.route("/reset-password")
def reset_password_page():
    return render_template("reset-password.html")


if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_ENV', '').lower() == 'development', port=5000)
