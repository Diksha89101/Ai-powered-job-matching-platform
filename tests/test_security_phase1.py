import importlib.util
import io
import os
import secrets
from datetime import datetime, timedelta, timezone
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

import jwt
import pytest
from bson import ObjectId

from app.config import positive_int, required_secret
from app.security import (
    valid_csrf_token,
    valid_email,
    valid_http_url,
    valid_text_fields,
    validate_upload_file,
)
from app.services.auth import (
    consume_refresh_token,
    decode_access_token,
    issue_access_token,
    issue_refresh_token,
    revoke_refresh_token,
)
from app.services.storage import InvalidUploadError, save_private_upload


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.inserted = []
        self.updates = []

    def find_one(self, query, _projection=None):
        for document in self.documents:
            matches = True
            for key, value in query.items():
                actual = document.get(key)
                if isinstance(value, dict) and '$in' in value:
                    matches = actual in value['$in']
                elif isinstance(value, dict) and '$regex' in value:
                    matches = bool(re.match(value['$regex'], str(actual or ''), re.I if 'i' in value.get('$options', '') else 0))
                else:
                    matches = actual == value
                if not matches:
                    break
            if not matches:
                continue
            return dict(document)
        return None

    def insert_one(self, document):
        stored = dict(document)
        stored.setdefault('_id', ObjectId())
        self.documents.append(stored)
        self.inserted.append(stored)
        return SimpleNamespace(inserted_id=stored['_id'])

    def find_one_and_update(self, query, update, return_document=None):
        for document in self.documents:
            if any(document.get(key) != value for key, value in query.items() if key != 'expires_at'):
                continue
            expires_query = query.get('expires_at', {})
            if document.get('expires_at') <= expires_query.get('$gt', datetime.min.replace(tzinfo=timezone.utc)):
                continue
            before = dict(document)
            document.update(update.get('$set', {}))
            return before
        return None

    def update_one(self, query, update):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                if '_id' in query and 'token_digest' not in query:
                    for key, value in update.get('$set', {}).items():
                        document[key] = value
                    for key, value in update.get('$inc', {}).items():
                        document[key] = document.get(key, 0) + value
                    return SimpleNamespace(modified_count=1, matched_count=1)
                document.update(update.get('$set', {}))
                for key, value in update.get('$inc', {}).items():
                    document[key] = document.get(key, 0) + value
                return SimpleNamespace(modified_count=1, matched_count=1)
        return SimpleNamespace(modified_count=0)

    def update_many(self, query, update):
        return SimpleNamespace(modified_count=0)

    def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if all(document.get(key) == value for key, value in query.items()):
                del self.documents[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query):
        before = len(self.documents)
        self.documents[:] = [
            document for document in self.documents
            if not all(document.get(key) == value for key, value in query.items())
        ]
        return SimpleNamespace(deleted_count=before - len(self.documents))

    def count_documents(self, query, limit=0):
        return sum(all(document.get(key) == value for key, value in query.items()) for document in self.documents)

    def find(self, query=None, _projection=None):
        query = query or {}
        return FakeCursor([
            dict(document)
            for document in self.documents
            if all(
                (document.get(key) in value.get('$in', []))
                if isinstance(value, dict) and '$in' in value
                else document.get(key) == value
                for key, value in query.items()
            )
        ])


class FakeCursor(list):
    def sort(self, *_args, **_kwargs):
        return self


def load_flask_entrypoint(monkeypatch):
    monkeypatch.setenv('SECRET_KEY', secrets.token_urlsafe(48))
    monkeypatch.setenv('FLASK_ENV', 'testing')
    source = Path(__file__).resolve().parents[1] / 'app.py'
    spec = importlib.util.spec_from_file_location('job_platform_test_entrypoint', source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _make_test_pdf(text):
    import fitz
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    return document.tobytes()


def test_secret_and_configuration_values_are_strict(monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'A' * 48)
    with pytest.raises(RuntimeError):
        required_secret('SECRET_KEY')

    monkeypatch.setenv('SECRET_KEY', secrets.token_urlsafe(48))
    assert len(required_secret('SECRET_KEY')) >= 32

    monkeypatch.setenv('ACCESS_TOKEN_TTL_MINUTES', '0')
    with pytest.raises(RuntimeError):
        positive_int('ACCESS_TOKEN_TTL_MINUTES', 15)


def test_security_input_validators_reject_wrong_types_and_unsafe_urls():
    assert valid_email('candidate@example.com')
    assert not valid_email(['candidate@example.com'])
    assert valid_http_url('https://example.com/profile')
    assert not valid_http_url('javascript:alert(1)')
    assert not valid_http_url('https://user:password@example.com')
    assert valid_text_fields({'name': 'Ada'}, {'name': 120})
    assert not valid_text_fields({'name': 123}, {'name': 120})
    assert not valid_text_fields({'name': 'x' * 121}, {'name': 120})
    assert valid_csrf_token('same-token', 'same-token')
    assert not valid_csrf_token('cookie-token', 'header-token')


def test_upload_validation_checks_signature_size_and_opaque_storage(tmp_path):
    valid_pdf = FileStorage(
        stream=io.BytesIO(b'%PDF-1.7\nsynthetic test document'),
        filename='../../resume.pdf',
    )
    assert validate_upload_file(valid_pdf, {'.pdf'}, 1024) == 'resume.pdf'

    invalid_pdf = FileStorage(
        stream=io.BytesIO(b'not a PDF'),
        filename='resume.pdf',
    )
    assert validate_upload_file(invalid_pdf, {'.pdf'}, 1024) is None

    too_large = FileStorage(
        stream=io.BytesIO(b'%PDF-' + b'x' * 1024),
        filename='resume.pdf',
    )
    assert validate_upload_file(too_large, {'.pdf'}, 1024) is None

    upload = FileStorage(
        stream=io.BytesIO(b'%PDF-1.7\nsynthetic test document'),
        filename='../../private resume.pdf',
    )
    filename, path = save_private_upload(upload, tmp_path, {'.pdf'}, 1024)
    assert filename.endswith('.pdf')
    assert 'private' not in filename
    assert Path(path).read_bytes().startswith(b'%PDF-')

    invalid = FileStorage(stream=io.BytesIO(b'bad'), filename='payload.pdf')
    with pytest.raises(InvalidUploadError):
        save_private_upload(invalid, tmp_path, {'.pdf'}, 1024)


class FakeRefreshSessions:
    def __init__(self):
        self.documents = []

    def insert_one(self, document):
        self.documents.append(dict(document))

    def find_one_and_update(self, query, update, return_document=None):
        now = query['expires_at']['$gt']
        for document in self.documents:
            if (
                document['token_digest'] == query['token_digest']
                and document['user_id'] == query['user_id']
                and document['revoked_at'] is None
                and document['expires_at'] > now
            ):
                before = dict(document)
                document.update(update['$set'])
                return before
        return None

    def update_one(self, query, update):
        for document in self.documents:
            if (
                document['token_digest'] == query['token_digest']
                and document['revoked_at'] is None
                and document['expires_at'] > query['expires_at']['$gt']
            ):
                document.update(update['$set'])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def test_access_token_accepts_legacy_tokens_but_checks_expiry_and_type():
    secret = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    legacy = jwt.encode(
        {'user_id': str(ObjectId()), 'exp': now + timedelta(minutes=5)},
        secret,
        algorithm='HS256',
    )
    assert decode_access_token(legacy, secret)['user_id']

    expired = jwt.encode(
        {'user_id': str(ObjectId()), 'exp': now - timedelta(minutes=1)},
        secret,
        algorithm='HS256',
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired, secret)

    refresh = jwt.encode(
        {
            'user_id': str(ObjectId()),
            'type': 'password_reset',
            'exp': now + timedelta(minutes=5),
        },
        secret,
        algorithm='HS256',
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(refresh, secret)


def test_refresh_tokens_are_stored_as_digests_and_single_use():
    secret = secrets.token_urlsafe(48)
    sessions = FakeRefreshSessions()
    user_id = str(ObjectId())
    old_refresh = issue_refresh_token(user_id, secret, 7, sessions)
    record = sessions.documents[0]
    assert record['token_digest'] != old_refresh

    old_claims = consume_refresh_token(old_refresh, secret, sessions)
    assert old_claims and old_claims['sub'] == user_id
    assert consume_refresh_token(old_refresh, secret, sessions) is None

    next_refresh = issue_refresh_token(user_id, secret, 7, sessions)
    assert revoke_refresh_token(next_refresh, secret, sessions)
    assert consume_refresh_token(next_refresh, secret, sessions) is None


def test_auth_routes_keep_bearer_contract_and_rotate_refresh_cookie(monkeypatch):
    module = load_flask_entrypoint(monkeypatch)
    user_id = ObjectId()
    user = {
        '_id': user_id,
        'name': 'Recruiter',
        'email': 'recruiter@example.com',
        'role': 'recruiter',
        'password': generate_password_hash('StrongPassword1'),
        'is_active': True,
        'token_version': 0,
    }
    users = FakeCollection([user])
    sessions = FakeRefreshSessions()
    fake_db = SimpleNamespace(
        users=users,
        recruiters=users,
        jobseekers=FakeCollection(),
        admins=FakeCollection(),
        refresh_sessions=sessions,
        activity_logs=FakeCollection(),
        jobs=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    client = module.app.test_client()

    response = client.post(
        '/api/auth/login',
        json={
            'email': user['email'],
            'password': 'StrongPassword1',
            'role': 'recruiter',
        },
    )
    assert response.status_code == 200
    assert set(response.json) == {'token', 'user'}
    assert response.json['user']['id'] == str(user_id)
    assert 'HttpOnly' in response.headers['Set-Cookie']
    first_access_token = response.json['token']
    first_refresh_cookie = next(
        item.split(';', 1)[0].split('=', 1)[1]
        for item in response.headers.getlist('Set-Cookie')
        if item.startswith('jobplatform_refresh=')
    )
    csrf_token = next(
        item.split(';', 1)[0].split('=', 1)[1]
        for item in response.headers.getlist('Set-Cookie')
        if item.startswith('jobplatform_refresh_csrf=')
    )

    me = client.get(
        '/api/auth/me',
        headers={'Authorization': f'Bearer {first_access_token}'},
    )
    assert me.status_code == 200
    assert 'password' not in me.json
    assert me.headers['X-Content-Type-Options'] == 'nosniff'
    assert me.headers['X-Frame-Options'] == 'DENY'

    rotated = client.post(
        '/api/auth/refresh',
        headers={'X-CSRF-Token': csrf_token},
    )
    assert rotated.status_code == 200
    rotated_refresh_cookie = next(
        item.split(';', 1)[0].split('=', 1)[1]
        for item in rotated.headers.getlist('Set-Cookie')
        if item.startswith('jobplatform_refresh=')
    )
    assert rotated_refresh_cookie != first_refresh_cookie

    bad_csrf = client.post(
        '/api/auth/refresh',
        headers={'X-CSRF-Token': 'incorrect'},
    )
    assert bad_csrf.status_code == 403


def test_role_accounts_are_separate_and_same_email_can_login_to_each(monkeypatch):
    module = load_flask_entrypoint(monkeypatch)
    shared_email = 'shared@example.com'
    password_hash = generate_password_hash('StrongPassword1')
    seeker = {
        '_id': ObjectId(), 'name': 'Seeker', 'email': shared_email,
        'role': 'jobseeker', 'password': password_hash, 'is_active': True,
    }
    recruiter = {
        '_id': ObjectId(), 'name': 'Recruiter', 'email': shared_email,
        'role': 'recruiter', 'password': password_hash, 'is_active': True,
    }
    fake_db = SimpleNamespace(
        users=FakeCollection(),
        jobseekers=FakeCollection([seeker]),
        recruiters=FakeCollection([recruiter]),
        admins=FakeCollection(),
        refresh_sessions=FakeRefreshSessions(),
        activity_logs=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    client = module.app.test_client()

    seeker_login = client.post('/api/auth/login', json={
        'email': shared_email, 'password': 'StrongPassword1', 'role': 'jobseeker',
    })
    recruiter_login = client.post('/api/auth/login', json={
        'email': shared_email, 'password': 'StrongPassword1', 'role': 'recruiter',
    })

    assert seeker_login.status_code == 200
    assert seeker_login.json['user']['role'] == 'jobseeker'
    assert recruiter_login.status_code == 200
    assert recruiter_login.json['user']['role'] == 'recruiter'


def test_login_does_not_authenticate_another_role(monkeypatch):
    module = load_flask_entrypoint(monkeypatch)
    recruiter = {
        '_id': ObjectId(), 'name': 'Recruiter', 'email': 'shared@example.com',
        'role': 'recruiter', 'password': generate_password_hash('StrongPassword1'),
        'is_active': True,
    }
    fake_db = SimpleNamespace(
        users=FakeCollection(), jobseekers=FakeCollection(),
        recruiters=FakeCollection([recruiter]), admins=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    response = module.app.test_client().post('/api/auth/login', json={
        'email': 'shared@example.com', 'password': 'StrongPassword1', 'role': 'jobseeker',
    })
    assert response.status_code == 401
    assert response.json['error'] == 'Invalid credentials'


def test_api_workflow_register_login_upload_post_recommend_apply(monkeypatch, tmp_path):
    module = load_flask_entrypoint(monkeypatch)
    monkeypatch.setattr(module, 'extract_resume_text', lambda _path: 'Python Django engineer')
    from utils import ai_matching
    monkeypatch.setattr(ai_matching, 'get_embedding_model', lambda: None)
    jobs = FakeCollection()
    applications = FakeCollection()
    sessions = FakeRefreshSessions()
    fake_db = SimpleNamespace(
        users=FakeCollection(), jobseekers=FakeCollection(),
        recruiters=FakeCollection(), admins=FakeCollection(),
        jobs=jobs, applications=applications, refresh_sessions=sessions,
        activity_logs=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=str(tmp_path / 'resumes'),
        PROFILE_IMAGE_FOLDER=str(tmp_path / 'images'),
    )
    os.makedirs(module.app.config['UPLOAD_FOLDER'], exist_ok=True)
    client = module.app.test_client()

    seeker_registration = client.post('/api/auth/register', json={
        'name': 'Candidate', 'email': 'candidate@example.com',
        'password': 'StrongPassword1', 'role': 'jobseeker', 'skills': 'Python, Django',
    })
    recruiter_registration = client.post('/api/auth/register', json={
        'name': 'Hiring Team', 'email': 'recruiter@example.com',
        'password': 'StrongPassword1', 'role': 'recruiter',
    })
    assert seeker_registration.status_code == 200
    assert recruiter_registration.status_code == 200

    seeker_login = client.post('/api/auth/login', json={
        'email': 'candidate@example.com', 'password': 'StrongPassword1', 'role': 'jobseeker',
    })
    recruiter_login = client.post('/api/auth/login', json={
        'email': 'recruiter@example.com', 'password': 'StrongPassword1', 'role': 'recruiter',
    })
    assert seeker_login.status_code == recruiter_login.status_code == 200
    seeker_token = seeker_login.json['token']
    recruiter_token = recruiter_login.json['token']

    upload = client.put(
        '/api/users/profile',
        headers={'Authorization': f'Bearer {seeker_token}'},
        data={'resume': (io.BytesIO(_make_test_pdf('Python Django engineer')), 'resume.pdf')},
        content_type='multipart/form-data',
    )
    assert upload.status_code == 200
    assert upload.json['resume_parsed'] is True

    posted = client.post('/api/jobs', headers={
        'Authorization': f'Bearer {recruiter_token}', 'Content-Type': 'application/json',
    }, json={
        'title': 'Backend Developer', 'company': 'Example Co', 'location': 'Remote',
        'description': 'Build services with Python and Django.', 'skills': 'Python, Django',
    })
    assert posted.status_code == 200
    job_id = posted.json['id']

    recommendations = client.get(
        '/api/jobs/recommended',
        headers={'Authorization': f'Bearer {seeker_token}'},
    )
    assert recommendations.status_code == 200
    assert recommendations.json[0]['matchPercentage'] == 100
    assert set(recommendations.json[0]['matchedSkills']) == {'python', 'django'}

    applied = client.post(
        f'/api/jobs/{job_id}/apply',
        headers={'Authorization': f'Bearer {seeker_token}'},
    )
    assert applied.status_code == 200
    assert applications.documents[0]['seeker_id'] == ObjectId(seeker_login.json['user']['id'])


def test_protected_routes_reject_inactive_users_and_bad_auth_schemes(monkeypatch):
    module = load_flask_entrypoint(monkeypatch)
    user_id = ObjectId()
    users = FakeCollection([{
        '_id': user_id,
        'name': 'Disabled',
        'email': 'disabled@example.com',
        'role': 'recruiter',
        'is_active': False,
        'token_version': 0,
    }])
    fake_db = SimpleNamespace(users=users)
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    token = issue_access_token(
        str(user_id),
        module.app.config['SECRET_KEY'],
        15,
    )
    client = module.app.test_client()
    denied = client.get(
        '/api/auth/me',
        headers={'Authorization': f'Bearer {token}'},
    )
    assert denied.status_code == 403

    malformed = client.get(
        '/api/auth/me',
        headers={'Authorization': f'Token {token}'},
    )
    assert malformed.status_code == 401


def test_recruiter_can_download_resume_only_for_own_applicant(
    monkeypatch,
    tmp_path,
):
    module = load_flask_entrypoint(monkeypatch)
    recruiter_id = ObjectId()
    seeker_id = ObjectId()
    job_id = ObjectId()
    recruiter = {
        '_id': recruiter_id,
        'name': 'Recruiter',
        'email': 'recruiter@example.com',
        'role': 'recruiter',
        'is_active': True,
    }
    seeker = {
        '_id': seeker_id,
        'name': 'Candidate',
        'email': 'candidate@example.com',
        'role': 'jobseeker',
        'resume': f'/api/users/{seeker_id}/resume/private.pdf',
        'is_active': True,
    }
    file_path = tmp_path / 'private.pdf'
    file_path.write_bytes(b'%PDF-1.7\nsynthetic resume')
    fake_db = SimpleNamespace(
        users=FakeCollection([recruiter, seeker]),
        jobs=FakeCollection([{'_id': job_id, 'recruiter_id': recruiter_id}]),
        applications=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=str(tmp_path),
    )
    token = issue_access_token(
        str(recruiter_id),
        module.app.config['SECRET_KEY'],
        15,
    )
    client = module.app.test_client()
    headers = {'Authorization': f'Bearer {token}'}
    path = f'/api/users/{seeker_id}/resume/private.pdf'

    denied = client.get(path, headers=headers)
    assert denied.status_code == 403

    fake_db.applications.documents.append({
        'seeker_id': seeker_id,
        'job_id': job_id,
    })
    allowed = client.get(path, headers=headers)
    assert allowed.status_code == 200
    assert allowed.data.startswith(b'%PDF-')
    assert allowed.headers['Cache-Control'] == 'private, no-store'


def test_jobs_list_keeps_array_response_when_empty(monkeypatch):
    module = load_flask_entrypoint(monkeypatch)
    fake_db = SimpleNamespace(
        jobs=FakeCollection(),
        users=FakeCollection(),
    )
    monkeypatch.setattr(module.mongo, 'db', fake_db, raising=False)
    module.app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    response = module.app.test_client().get('/api/jobs')
    assert response.status_code == 200
    assert response.json == []
