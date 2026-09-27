from flask import Flask, request, jsonify, render_template, send_from_directory, url_for
from flask_cors import CORS
from flask_pymongo import PyMongo
from flask_mail import Mail, Message
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import jwt
import datetime
import os
import re
from bson.objectid import ObjectId
from bson.errors import InvalidId
from functools import wraps
from app.config import build_config
from app.security import validate_password, safe_upload_name

from utils.ai_matching import (
    build_candidate_profile,
    build_job_profile,
    calculate_keyword_match,
    extract_resume_text,
)

load_dotenv(override=True)

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config.update(build_config())
app.config['PROFILE_IMAGE_FOLDER'] = os.getenv('PROFILE_IMAGE_FOLDER', os.path.join(app.root_path, 'static', 'profile-images'))

# Application thresholds
MIN_MATCH_PERCENTAGE_TO_APPLY = int(os.getenv('MIN_MATCH_PERCENTAGE_TO_APPLY', 50))

# Email configuration
def env_value(name, default=None):
    value = os.getenv(name, default)
    if not isinstance(value, str):
        return value
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def env_bool(name, default='False'):
    return str(env_value(name, default)).lower() in {'1', 'true', 'yes', 'on'}


app.config['MAIL_SERVER'] = env_value('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(env_value('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = env_bool('MAIL_USE_TLS', 'True')
app.config['MAIL_USE_SSL'] = env_bool('MAIL_USE_SSL', 'False')
app.config['MAIL_USERNAME'] = env_value('MAIL_USERNAME')
mail_password = env_value('MAIL_PASSWORD')
app.config['MAIL_PASSWORD'] = mail_password.replace(' ', '') if mail_password else None
app.config['MAIL_DEFAULT_SENDER'] = env_value('MAIL_DEFAULT_SENDER') or app.config['MAIL_USERNAME']

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['PROFILE_IMAGE_FOLDER'], exist_ok=True)

mongo = PyMongo(app)
mail = Mail(app)
limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])
allowed_origins = [origin.strip() for origin in app.config.get('CORS_ORIGINS', '').split(',') if origin.strip()]
CORS(app, resources={r'/api/*': {'origins': allowed_origins or []}})

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
            'created_at': datetime.datetime.utcnow()
        })
    except Exception:
        pass


def serialize_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat() + 'Z'
    if isinstance(value, list):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(val) for key, val in value.items()}
    return value


def sanitize_user_doc(user_doc):
    cleaned = serialize_value(dict(user_doc))
    cleaned.pop('password', None)
    cleaned.pop('resume_text', None)
    return cleaned


def parse_object_id(value):
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def normalize_email(email):
    return (email or '').strip().lower()


def find_user_by_email(email):
    normalized = normalize_email(email)
    if not normalized:
        return None
    user = mongo.db.users.find_one({'email': normalized})
    if user:
        return user
    return mongo.db.users.find_one({
        'email': {'$regex': f'^{re.escape(normalized)}$', '$options': 'i'}
    })


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


def build_reset_link(reset_token):
    public_base_url = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    if public_base_url:
        return f'{public_base_url}/reset-password.html?token={reset_token}'
    return url_for('reset_password_page', token=reset_token, _external=True)


def create_password_reset_token(user_id):
    return jwt.encode({
        'user_id': str(user_id),
        'type': 'password_reset',
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)
    }, app.config['SECRET_KEY'], algorithm='HS256')


def send_password_reset_email(user, reset_link):
    email_config_error = get_email_config_error()
    if email_config_error:
        raise RuntimeError(email_config_error)

    recipient = normalize_email(user.get('email'))
    if not recipient:
        raise RuntimeError('User account does not have a valid email address.')

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
                <p>Hi {user.get('name', 'User')},</p>
                <p>You requested a password reset for your job platform account.</p>
                <p>Click the link below to reset your password:</p>
                <p><a href="{reset_link}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Reset Password</a></p>
                <p>Or copy and paste this link in your browser:</p>
                <p>{reset_link}</p>
                <p>This link will expire in 1 hour.</p>
                <p>If you did not request this password reset, please ignore this email.</p>
                <p>Best regards,<br>Job Platform Team</p>
            </body>
        </html>
        '''
    )
    mail.send(msg)



def get_seed_admin_config():
    admin_name = os.getenv('ADMIN_NAME', 'Platform Admin')
    admin_email = os.getenv('ADMIN_EMAIL')
    admin_password = os.getenv('ADMIN_PASSWORD')
    environment = os.getenv('FLASK_ENV', '').lower()

    if admin_email and admin_password:
        return {
            'name': admin_name,
            'email': admin_email,
            'password': admin_password,
            'seed_source': 'environment'
        }

    if environment == 'development':
        return {
            'name': admin_name,
            'email': 'admin@jobplatform.local',
            'password': 'Admin@12345',
            'seed_source': 'development defaults'
        }

    return None


def ensure_admin_account():
    if mongo.db.users.count_documents({'role': 'admin'}, limit=1):
        return

    seed_admin = get_seed_admin_config()
    if not seed_admin:
        return

    existing_user = mongo.db.users.find_one({'email': seed_admin['email']})
    admin_payload = {
        'name': seed_admin['name'],
        'email': seed_admin['email'],
        'password': generate_password_hash(seed_admin['password']),
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
        'created_at': datetime.datetime.utcnow()
    }

    if existing_user:
        mongo.db.users.update_one(
            {'_id': existing_user['_id']},
            {'$set': {
                'name': seed_admin['name'],
                'password': admin_payload['password'],
                'role': 'admin',
                'is_active': True
            }}
        )
    else:
        mongo.db.users.insert_one(admin_payload)


def get_saved_resume_text(user_doc):
    resume_url = user_doc.get('resume', '')
    filename = os.path.basename(resume_url.rstrip('/'))
    if not filename or filename != secure_filename(filename):
        return ''

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.isfile(file_path):
        return ''

    return extract_resume_text(file_path)


def build_live_candidate_profile(user_doc, skills_text=None, resume_text=None):
    active_resume_text = user_doc.get('resume_text', '') if resume_text is None else resume_text
    if resume_text is None and not active_resume_text:
        active_resume_text = get_saved_resume_text(user_doc)

    profile = build_candidate_profile(
        user_doc.get('skills', '') if skills_text is None else skills_text,
        active_resume_text,
    )

    # Fall back to stored resume-only AI fields for users created before this logic was added.
    if not profile['resume_skills'] and isinstance(user_doc.get('resume_skills'), list):
        profile['resume_skills'] = user_doc['resume_skills']
    if not profile['profile_keywords']:
        profile['profile_keywords'] = profile['resume_skills']

    return profile

# JWT decorator
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            token = token.split(' ')[1]  # Bearer <token>
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = mongo.db.users.find_one({'_id': ObjectId(data['user_id'])})
            if not current_user:
                return jsonify({'error': 'User not found'}), 401
            request.current_user = current_user
        except Exception as e:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    @token_required
    def decorated(*args, **kwargs):
        if request.current_user.get('role') != 'admin':
            return jsonify({'error': 'Admin access required'}), 403
        if request.current_user.get('is_active', True) is False:
            return jsonify({'error': 'Account disabled'}), 403
        return f(*args, **kwargs)
    return decorated

# ------------------- AUTH ROUTES -------------------
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password', '')
    role = data.get('role')
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    if role not in {'jobseeker', 'recruiter'}:
        return jsonify({'error': 'Invalid role'}), 400
    valid_password, password_error = validate_password(password, app.config.get('PASSWORD_MIN_LENGTH', 8))
    if not valid_password:
        return jsonify({'error': password_error}), 400
    if find_user_by_email(email):
        return jsonify({'error': 'Email already exists'}), 400
    
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
        'resume': '',
        'resume_text': '',
        'resume_skills': [],
        'profile_keywords': [],
        'is_active': True,
        'created_at': datetime.datetime.utcnow()
    }
    result = mongo.db.users.insert_one(user)
    log_activity('user_registered', 'user', result.inserted_id, {'name': user['name'], 'role': user['role']})
    token = jwt.encode({
        'user_id': str(result.inserted_id),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, app.config['SECRET_KEY'], algorithm='HS256')
    return jsonify({
        'token': token,
        'user': {
            'id': str(result.inserted_id),
            'name': user['name'],
            'email': user['email'],
            'role': user['role']
        }
    })

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit('10 per minute')
def login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    password = data.get('password')
    role = data.get('role')
    
    user = find_user_by_email(email)
    if not user or not check_password_hash(user['password'], password):
        return jsonify({'error': 'Invalid credentials'}), 401
    if user.get('is_active', True) is False:
        return jsonify({'error': 'Account disabled. Please contact the administrator.'}), 403
    if user['role'] != role:
        return jsonify({'error': f'Not a {role} account'}), 401
    
    token = jwt.encode({
        'user_id': str(user['_id']),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, app.config['SECRET_KEY'], algorithm='HS256')
    log_activity('user_login', 'user', user['_id'], {'role': user['role'], 'email': user['email']})
    return jsonify({
        'token': token,
        'user': {
            'id': str(user['_id']),
            'name': user['name'],
            'email': user['email'],
            'role': user['role']
        }
    })

@app.route('/api/auth/me', methods=['GET'])
@token_required
def get_me():
    user = serialize_doc(request.current_user)
    if user.get('role') == 'jobseeker':
        candidate_profile = build_live_candidate_profile(request.current_user)
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
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get('email'))
    
    if not email:
        return jsonify({'error': 'Email is required'}), 400
    
    user = find_user_by_email(email)
    
    # Always return the same message for security (don't reveal if email exists)
    response_message = {
        'message': 'If an account exists with this email, a password reset link has been sent. Please check your email.'
    }
    
    if not user:
        # Don't reveal if email exists or not for security
        return jsonify(response_message), 200
    
    reset_link = None
    try:
        # Generate reset token (expires in 1 hour)
        reset_token = create_password_reset_token(user['_id'])
        reset_link = build_reset_link(reset_token)

        email_config_error = get_email_config_error()
        if email_config_error:
            raise RuntimeError(email_config_error)
        
        # Send email with reset link
        msg = Message(
            subject='Password Reset Request',
            sender=app.config.get('MAIL_DEFAULT_SENDER'),
            recipients=[normalize_email(user.get('email'))],
            html=f'''
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Password Reset Request</h2>
                    <p>Hi {user.get('name', 'User')},</p>
                    <p>You requested a password reset for your job platform account.</p>
                    <p>Click the link below to reset your password:</p>
                    <p><a href="{reset_link}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">Reset Password</a></p>
                    <p>Or copy and paste this link in your browser:</p>
                    <p>{reset_link}</p>
                    <p>This link will expire in 1 hour.</p>
                    <p>If you did not request this password reset, please ignore this email.</p>
                    <p>Best regards,<br>Job Platform Team</p>
                </body>
            </html>
            '''
        )
        mail.send(msg)
        print(f"Password reset email sent successfully to {email}")
    except Exception as e:
        print(f"Error sending password reset email to {email}: {str(e)}")
        # Always show the dev link when email fails (for development/demo purposes)
        if reset_link and os.getenv('FLASK_ENV', '').lower() == 'development':
            response_message['dev_link'] = reset_link
        response_message['message'] = 'Email was not sent because SMTP is not configured correctly.'
    
    return jsonify(response_message), 200

@app.route('/api/auth/verify-reset-token', methods=['POST'])
def verify_reset_token():
    data = request.get_json(silent=True) or {}
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

        user = mongo.db.users.find_one({'_id': user_id})
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
    data = request.get_json(silent=True) or {}
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

        result = mongo.db.users.update_one(
            {'_id': user_id},
            {'$set': {'password': generate_password_hash(password)}}
        )
        if result.matched_count == 0:
            return jsonify({'error': 'User not found'}), 404

        log_activity('password_reset', 'user', user_id)
        return jsonify({'message': 'Password reset successfully'})
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token has expired'}), 400
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 400


@app.route('/api/test-email', methods=['POST'])
def test_email():
    """Test email configuration - shows what would be sent"""
    data = request.get_json(silent=True) or {}
    test_email = data.get('email', 'test@example.com')

    # Generate a test reset token
    reset_token = create_password_reset_token('test-user-id')
    reset_link = build_reset_link(reset_token)

    # Try to send email
    try:
        email_config_error = get_email_config_error()
        if email_config_error:
            raise RuntimeError(email_config_error)

        msg = Message(
            subject='Test Email - Job Platform',
            recipients=[test_email],
            html=f'''
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Test Email Successful! 🎉</h2>
                    <p>This is a test from your Job Platform.</p>
                    <p>If you received this, email is working!</p>
                    <p>Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </body>
            </html>
            '''
        )
        mail.send(msg)
        return jsonify({
            'success': True,
            'message': f'Test email sent to {test_email}',
            'reset_link': reset_link
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Email not configured, but here\'s what a reset link would look like:',
            'reset_link': reset_link,
            'note': 'Click the link above to test the password reset flow!'
        }), 200


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
        'totalUsers': mongo.db.users.count_documents({}),
        'activeUsers': mongo.db.users.count_documents({'is_active': {'$ne': False}}),
        'jobseekers': mongo.db.users.count_documents({'role': 'jobseeker'}),
        'recruiters': mongo.db.users.count_documents({'role': 'recruiter'}),
        'admins': mongo.db.users.count_documents({'role': 'admin'}),
        'jobs': mongo.db.jobs.count_documents({}),
        'applications': mongo.db.applications.count_documents({}),
        'pipeline': pipeline_stats,
        'recentUsers': [
            sanitize_user_doc(user)
            for user in mongo.db.users.find({}, {'password': 0, 'resume_text': 0}).sort('created_at', -1).limit(5)
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
    for user in mongo.db.users.find({}, {'password': 0, 'resume_text': 0}).sort('created_at', -1):
        users.append(sanitize_user_doc(user))
    return jsonify(users)


@app.route('/api/admin/users/<user_id>/status', methods=['PUT'])
@admin_required
def admin_update_user_status(user_id):
    target_id = parse_object_id(user_id)
    if not target_id:
        return jsonify({'error': 'Invalid user id'}), 400

    if target_id == request.current_user['_id']:
        return jsonify({'error': 'You cannot change your own admin status from the admin panel'}), 400

    payload = request.get_json(silent=True) or {}
    is_active = payload.get('is_active')
    if not isinstance(is_active, bool):
        return jsonify({'error': 'is_active must be a boolean'}), 400

    target_user = mongo.db.users.find_one({'_id': target_id})
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    if target_user.get('role') == 'admin' and is_active is False:
        active_admins = mongo.db.users.count_documents({'role': 'admin', 'is_active': {'$ne': False}})
        if active_admins <= 1:
            return jsonify({'error': 'At least one active admin account is required'}), 400

    mongo.db.users.update_one({'_id': target_id}, {'$set': {'is_active': is_active}})
    log_activity('user_status_changed', 'user', target_id, {'is_active': is_active, 'changed_by': str(request.current_user['_id'])}, request.current_user['_id'])
    return jsonify({'message': 'User status updated'})


@app.route('/api/admin/jobs', methods=['GET'])
@admin_required
def admin_jobs():
    jobs = []
    for job in mongo.db.jobs.find().sort('created_at', -1):
        recruiter = mongo.db.users.find_one({'_id': job.get('recruiter_id')}) if job.get('recruiter_id') else None
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
    log_activity('job_deleted', 'job', target_id, {'deleted_by': str(request.current_user['_id'])}, request.current_user['_id'])
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
        {'$lookup': {
            'from': 'users',
            'localField': 'seeker_id',
            'foreignField': '_id',
            'as': 'seeker'
        }},
        {'$unwind': {'path': '$job', 'preserveNullAndEmptyArrays': True}},
        {'$unwind': {'path': '$seeker', 'preserveNullAndEmptyArrays': True}},
        {'$sort': {'applied_at': -1}}
    ]

    applications = []
    for application in mongo.db.applications.aggregate(pipeline):
        formatted_application = serialize_value(application)
        formatted_application['job_title'] = application.get('job', {}).get('title', 'Removed job')
        formatted_application['applicant_name'] = application.get('seeker', {}).get('name', 'Removed user')
        if 'seeker' in formatted_application and isinstance(formatted_application['seeker'], dict):
            formatted_application['seeker'].pop('password', None)
            formatted_application['seeker'].pop('resume_text', None)
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
    mongo.db.applications.update_one({'_id': target_id}, {'$set': {'status': new_status, 'updated_at': datetime.datetime.utcnow()}})
    log_activity('application_status_changed', 'application', target_id, {'old_status': old_status, 'new_status': new_status, 'changed_by': str(request.current_user['_id'])}, request.current_user['_id'])
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
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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
                'users': mongo.db.users.count_documents({'created_at': {'$gte': today}}),
                'jobs': mongo.db.jobs.count_documents({'created_at': {'$gte': today}}),
                'applications': mongo.db.applications.count_documents({'applied_at': {'$gte': today}})
            },
            'thisWeek': {
                'users': mongo.db.users.count_documents({'created_at': {'$gte': week_ago}}),
                'jobs': mongo.db.jobs.count_documents({'created_at': {'$gte': week_ago}}),
                'applications': mongo.db.applications.count_documents({'applied_at': {'$gte': week_ago}})
            },
            'thisMonth': {
                'users': mongo.db.users.count_documents({'created_at': {'$gte': month_ago}}),
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
        recruiter = mongo.db.users.find_one({'_id': item['_id']})
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
    
    # Handle profile image upload
    if 'profile_image' in request.files:
        file = request.files['profile_image']
        if file and file.filename:
            safe_name = safe_upload_name(file.filename, {'.jpg', '.jpeg', '.png', '.gif'})
            if not safe_name:
                return jsonify({'error': 'Only JPG, JPEG, PNG, and GIF files are allowed for profile photos'}), 400
            filename = f"{datetime.datetime.utcnow().timestamp()}_{safe_name}"
            file.save(os.path.join(app.config['PROFILE_IMAGE_FOLDER'], filename))
            update_fields['profile_image'] = f'/static/profile-images/{filename}'
    
    # Handle resume upload
    if 'resume' in request.files:
        file = request.files['resume']
        if file and file.filename:
            safe_name = safe_upload_name(file.filename, {'.pdf', '.doc', '.docx'})
            if not safe_name:
                return jsonify({'error': 'Only PDF, DOC, and DOCX files are allowed for resumes'}), 400
            filename = f"{datetime.datetime.utcnow().timestamp()}_{safe_name}"
            saved_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(saved_path)
            update_fields['resume'] = f'/api/users/{request.current_user["_id"]}/resume/{filename}'
            if request.current_user.get('role') == 'jobseeker':
                update_fields['resume_text'] = extract_resume_text(saved_path)

    ai_payload = {}
    if request.current_user.get('role') == 'jobseeker':
        active_skills = update_fields.get('skills', request.current_user.get('skills', ''))
        active_resume_text = update_fields['resume_text'] if 'resume_text' in update_fields else None
        candidate_profile = build_live_candidate_profile(
            request.current_user,
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
    
    mongo.db.users.update_one({'_id': request.current_user['_id']}, {'$set': update_fields})
    log_activity('profile_updated', 'user', request.current_user['_id'], {'fields': list(update_fields.keys())}, request.current_user['_id'])
    return jsonify({'message': 'Profile updated', **ai_payload})



@app.route('/api/users/<user_id>/resume/<path:filename>', methods=['GET'])
@token_required
def download_resume(user_id, filename):
    if str(request.current_user['_id']) != user_id and request.current_user.get('role') != 'recruiter' and request.current_user.get('role') != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    safe_name = os.path.basename(filename)
    if safe_name != secure_filename(safe_name):
        return jsonify({'error': 'Invalid file name'}), 400
    user = mongo.db.users.find_one({'_id': parse_object_id(user_id)})
    if not user or user.get('resume', '').rstrip('/').split('/')[-1] != safe_name:
        return jsonify({'error': 'Resume not found'}), 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name, as_attachment=True)


@app.route('/api/users/<user_id>/profile-image/<path:filename>', methods=['GET'])
@token_required
def download_profile_image(user_id, filename):
    if str(request.current_user['_id']) != user_id and request.current_user.get('role') != 'admin':
        return jsonify({'error': 'Access denied'}), 403
    safe_name = os.path.basename(filename)
    if safe_name != secure_filename(safe_name):
        return jsonify({'error': 'Invalid file name'}), 400
    user = mongo.db.users.find_one({'_id': parse_object_id(user_id)})
    if not user or user.get('profile_image', '').rstrip('/').split('/')[-1] != safe_name:
        return jsonify({'error': 'Profile image not found'}), 404
    return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name)

# ------------------- JOBS ROUTES -------------------
@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    search = request.args.get('search', '')
    location = request.args.get('location', '')
    query = {}
    if search:
        query['$or'] = [
            {'title': {'$regex': search, '$options': 'i'}},
            {'description': {'$regex': search, '$options': 'i'}}
        ]
    if location and location != '':
        query['location'] = location
    
    jobs = list(mongo.db.jobs.find(query).sort('created_at', -1))
    for job in jobs:
        recruiter_id = job.get('recruiter_id')
        job['_id'] = str(job['_id'])
        recruiter = mongo.db.users.find_one({'_id': recruiter_id}) if recruiter_id else None
        if isinstance(recruiter_id, ObjectId):
            job['recruiter_id'] = str(recruiter_id)
        job['recruiter_name'] = recruiter['name'] if recruiter else ''
    return jsonify(jobs)

@app.route('/api/jobs', methods=['POST'])
@token_required
def post_job():
    if request.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Only recruiters can post jobs'}), 403
    
    data = request.get_json(silent=True) or {}
    if not data.get('title') or not data.get('company') or not data.get('location'):
        return jsonify({'error': 'Title, company, and location are required'}), 400
    job = {
        'title': data.get('title'),
        'company': data.get('company'),
        'location': data.get('location'),
        'description': data.get('description', ''),
        'skills': data.get('skills', ''),
        'recruiter_id': request.current_user['_id'],
        'created_at': datetime.datetime.utcnow()
    }
    result = mongo.db.jobs.insert_one(job)
    log_activity('job_posted', 'job', result.inserted_id, {'title': job['title'], 'company': job['company']}, request.current_user['_id'])
    return jsonify({'id': str(result.inserted_id), 'message': 'Job posted'})

@app.route('/api/recruiter/jobs', methods=['GET'])
@token_required
def get_recruiter_jobs():
    if request.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403
    jobs = list(mongo.db.jobs.find({'recruiter_id': request.current_user['_id']}).sort('created_at', -1))
    for job in jobs:
        job['_id'] = str(job['_id'])
        if isinstance(job.get('recruiter_id'), ObjectId):
            job['recruiter_id'] = str(job['recruiter_id'])
    return jsonify(jobs)

@app.route('/api/jobs/<job_id>/apply', methods=['POST'])
@token_required
def apply_job(job_id):
    if request.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Only job seekers can apply'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400

    job = mongo.db.jobs.find_one({'_id': target_id})
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    existing = mongo.db.applications.find_one({
        'job_id': target_id,
        'seeker_id': request.current_user['_id']
    })
    if existing:
        return jsonify({'error': 'Already applied'}), 400

    candidate_profile = build_live_candidate_profile(request.current_user)
    job_profile = build_job_profile(
        skills_text=job.get('skills', ''),
        title=job.get('title', ''),
        description=job.get('description', ''),
    )
    match = calculate_keyword_match(
        candidate_profile['profile_keywords'],
                job_profile['match_keywords'],
                job_profile['inferred_keywords'],
                candidate_profile.get('semantic_text', ''),
                job_profile.get('semantic_text', ''),
    )
    if match.get('matchPercentage', 0) < MIN_MATCH_PERCENTAGE_TO_APPLY:
        return jsonify({'error': f'Cannot apply: job match must be at least {MIN_MATCH_PERCENTAGE_TO_APPLY}%.'}), 400
    
    application = {
        'job_id': target_id,
        'seeker_id': request.current_user['_id'],
        'status': 'applied',
        'applied_at': datetime.datetime.utcnow()
    }
    mongo.db.applications.insert_one(application)
    return jsonify({'message': 'Application submitted'})

@app.route('/api/jobs/<job_id>/unapply', methods=['DELETE'])
@token_required
def unapply_job(job_id):
    if request.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Only job seekers can withdraw applications'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400
    
    result = mongo.db.applications.delete_one({
        'job_id': target_id,
        'seeker_id': request.current_user['_id']
    })
    
    if result.deleted_count == 0:
        return jsonify({'error': 'Application not found'}), 404
    
    return jsonify({'message': 'Application withdrawn'})

@app.route('/api/jobs/applied', methods=['GET'])
@token_required
def get_applied_jobs():
    if request.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Access denied'}), 403
    
    pipeline = [
        {'$match': {'seeker_id': request.current_user['_id']}},
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
    if request.current_user['role'] != 'jobseeker':
        return jsonify({'error': 'Access denied'}), 403
    
    candidate_profile = build_live_candidate_profile(request.current_user)
    applied_job_ids = {
        str(application['job_id'])
        for application in mongo.db.applications.find(
            {'seeker_id': request.current_user['_id']},
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
        match = calculate_keyword_match(
            candidate_profile['profile_keywords'],
                job_profile['match_keywords'],
                job_profile['inferred_keywords'],
                candidate_profile.get('semantic_text', ''),
                job_profile.get('semantic_text', ''),
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
    if request.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403

    target_id = parse_object_id(job_id)
    if not target_id:
        return jsonify({'error': 'Invalid job id'}), 400
    
    job = mongo.db.jobs.find_one({'_id': target_id, 'recruiter_id': request.current_user['_id']})
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    job_profile = build_job_profile(
        skills_text=job.get('skills', ''),
        title=job.get('title', ''),
        description=job.get('description', ''),
    )
    pipeline = [
        {'$match': {'job_id': target_id}},
        {'$lookup': {
            'from': 'users',
            'localField': 'seeker_id',
            'foreignField': '_id',
            'as': 'seeker'
        }},
        {'$unwind': '$seeker'}
    ]
    applicants = list(mongo.db.applications.aggregate(pipeline))
    for app in applicants:
        app['_id'] = str(app['_id'])
        app['job_id'] = str(app['job_id'])
        app['seeker_id'] = str(app['seeker_id'])
        app['seeker']['_id'] = str(app['seeker']['_id'])
        candidate_profile = build_live_candidate_profile(app['seeker'])
        app.update(
            calculate_keyword_match(
                candidate_profile['profile_keywords'],
                job_profile['match_keywords'],
                job_profile['inferred_keywords'],
                candidate_profile.get('semantic_text', ''),
                job_profile.get('semantic_text', ''),
            )
        )
        if 'resume_text' in app['seeker']:
            del app['seeker']['resume_text']
    return jsonify(applicants)

@app.route('/api/recruiter/applicants', methods=['GET'])
@token_required
def get_recruiter_applicants():
    if request.current_user['role'] != 'recruiter':
        return jsonify({'error': 'Access denied'}), 403

    # Get all jobs for this recruiter
    recruiter_jobs = list(mongo.db.jobs.find({'recruiter_id': request.current_user['_id']}, {'_id': 1, 'title': 1, 'skills': 1, 'description': 1}))
    
    all_applicants = []
    for job in recruiter_jobs:
        job_profile = build_job_profile(
            skills_text=job.get('skills', ''),
            title=job.get('title', ''),
            description=job.get('description', ''),
        )
        pipeline = [
            {'$match': {'job_id': job['_id']}},
            {'$lookup': {
                'from': 'users',
                'localField': 'seeker_id',
                'foreignField': '_id',
                'as': 'seeker'
            }},
            {'$unwind': '$seeker'}
        ]
        applicants = list(mongo.db.applications.aggregate(pipeline))
        for app in applicants:
            app['_id'] = str(app['_id'])
            app['job_id'] = str(app['job_id'])
            app['seeker_id'] = str(app['seeker_id'])
            app['seeker']['_id'] = str(app['seeker']['_id'])
            app['job_title'] = job['title']
            candidate_profile = build_live_candidate_profile(app['seeker'])
            match = calculate_keyword_match(
                candidate_profile['profile_keywords'],
                job_profile['match_keywords'],
                job_profile['inferred_keywords'],
                candidate_profile.get('semantic_text', ''),
                job_profile.get('semantic_text', ''),
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
    if request.current_user['role'] != 'recruiter':
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
            'created_at': datetime.datetime.utcnow()
        })
    
    try:
        result = mongo.db.notifications.insert_many(notifications)
        log_activity('send_notification', 'notification', None, {
            'notification_ids': [str(id) for id in result.inserted_ids],
            'recipient_count': len(notifications)
        }, user_id=request.current_user['_id'])
        return jsonify({
            'message': 'Notifications sent successfully',
            'count': len(notifications)
        })
    except Exception as e:
        log_activity('send_notification_error', 'notification', None, {
            'error': str(e),
            'recipient_count': len(notifications)
        }, user_id=request.current_user['_id'])
        return jsonify({'error': f'Failed to send notifications: {str(e)}'}), 500

@app.route('/api/notifications', methods=['GET'])
@token_required
def get_notifications():
    try:
        notifs = list(mongo.db.notifications.find({'user_id': request.current_user['_id']}).sort('created_at', -1))
        for n in notifs:
            n['_id'] = str(n['_id'])
            n['user_id'] = str(n['user_id'])
        return jsonify(notifs)
    except Exception as e:
        log_activity('get_notifications_error', 'notification', None, {
            'error': str(e)
        }, user_id=request.current_user['_id'])
        return jsonify({'error': f'Failed to fetch notifications: {str(e)}'}), 500

# ------------------- SERVE HTML PAGES -------------------
@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/landing.html')
def landing_page():
    return render_template('landing.html')

@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/register_jobseeker.html')
def register_jobseeker_page():
    return render_template('register_jobseeker.html')

@app.route('/register_recruiter.html')
def register_recruiter_page():
    return render_template('register_recruiter.html')

@app.route('/jobseeker_home.html')
def jobseeker_home():
    return render_template('jobseeker_home.html')

@app.route('/jobseeker_profile.html')
def jobseeker_profile():
    return render_template('jobseeker_profile.html')

@app.route('/recruiter_home.html')
def recruiter_home():
    return render_template('recruiter_home.html')

@app.route('/recruiter_profile.html')
def recruiter_profile():
    return render_template('recruiter_profile.html')

@app.route('/browse_jobs.html')
def browse_jobs():
    return render_template('browse_jobs.html')

@app.route('/job_apply.html')
def job_apply():
    return render_template('job_apply.html')

@app.route('/seeker_dashboard.html')
def seeker_dashboard():
    return render_template('seeker_dashboard.html')

@app.route('/recruiter_dashboard.html')
def recruiter_dashboard():
    return render_template('recruiter_dashboard.html')

@app.route('/post_job.html')
def post_job_page():
    return render_template('post_job.html')

@app.route('/manage_jobs.html')
def manage_jobs_page():
    return render_template('manage_jobs.html')

@app.route('/applicants.html')
def applicants_page():
    return render_template('applicants.html')

@app.route('/send_notifications.html')
def send_notifications_page():
    return render_template('send_notifications.html')

@app.route('/notify_recruiter.html')
def notify_recruiter_page():
    return render_template('notify_recruiter.html')

@app.route('/notifications.html')
def notifications_page():
    return render_template('notifications.html')

@app.route('/applied_jobs.html')
def applied_jobs_page():
    return render_template('applied_jobs.html')

@app.route('/admin_dashboard.html')
def admin_dashboard_page():
    return render_template('admin_dashboard.html')

@app.route("/forgot-password.html")
def forgot_password_page():
    return render_template("forgot-password.html")

@app.route("/reset-password.html")
def reset_password_page():
    return render_template("reset-password.html")


if __name__ == '__main__':
    ensure_admin_account()
    app.run(debug=os.getenv('FLASK_ENV', '').lower() == 'development', port=5000)
