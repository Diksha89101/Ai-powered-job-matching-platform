(function () {
    'use strict';

    const API_PREFIX = '/api';
    const TOKEN_KEY = 'token';
    const USER_KEY = 'user';
    const REFRESH_CSRF_COOKIE = 'jobplatform_refresh_csrf';
    const MATCHING_JOBS = new Map();
    const nativeFetch = window.fetch.bind(window);
    let redirectingForAuth = false;
    let refreshPromise = null;

    function readStorage(storage, key) {
        try {
            return storage.getItem(key);
        } catch (error) {
            console.error(`Unable to read ${key} from browser storage.`, error);
            return null;
        }
    }

    function writeStorage(storage, key, value) {
        try {
            storage.setItem(key, value);
        } catch (error) {
            console.error(`Unable to save ${key} in browser storage.`, error);
            throw new Error('Your browser could not save the sign-in session.');
        }
    }

    function removeStorage(storage, key) {
        try {
            storage.removeItem(key);
        } catch (error) {
            console.error(`Unable to clear ${key} from browser storage.`, error);
        }
    }

    function getToken() {
        return readStorage(window.sessionStorage, TOKEN_KEY) ||
            readStorage(window.localStorage, TOKEN_KEY);
    }

    function getUser() {
        const storedUser = readStorage(window.sessionStorage, USER_KEY) ||
            readStorage(window.localStorage, USER_KEY);
        if (!storedUser) return null;
        try {
            return JSON.parse(storedUser);
        } catch (error) {
            console.error('Stored user data is invalid JSON.', error);
            clearCredentials();
            return null;
        }
    }

    function saveAuth(payload) {
        if (!payload || typeof payload.token !== 'string' || !payload.token) {
            throw new Error('The server did not return a valid authentication token.');
        }
        writeStorage(window.sessionStorage, TOKEN_KEY, payload.token);
        if (payload.user) {
            writeStorage(window.sessionStorage, USER_KEY, JSON.stringify(payload.user));
        } else {
            removeStorage(window.sessionStorage, USER_KEY);
        }
        removeStorage(window.localStorage, TOKEN_KEY);
        removeStorage(window.localStorage, USER_KEY);
    }

    function clearCredentials() {
        removeStorage(window.sessionStorage, TOKEN_KEY);
        removeStorage(window.sessionStorage, USER_KEY);
        removeStorage(window.localStorage, TOKEN_KEY);
        removeStorage(window.localStorage, USER_KEY);
    }

    function readCookie(name) {
        const prefix = `${name}=`;
        const cookie = document.cookie.split('; ').find(item => item.startsWith(prefix));
        return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : '';
    }

    function isAuthEndpoint(url) {
        const pathname = new URL(url, window.location.origin).pathname;
        return /^\/api\/auth\/(login|register|forgot-password|verify-reset-token|reset-password|refresh|logout)$/.test(pathname);
    }

    function refreshAccessToken() {
        if (!refreshPromise) {
            const csrfToken = readCookie(REFRESH_CSRF_COOKIE);
            refreshPromise = nativeFetch('/api/auth/refresh', {
                method: 'POST',
                credentials: 'same-origin',
                headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {}
            }).then(async response => {
                if (!response.ok) return false;
                const payload = await response.json();
                if (!payload || typeof payload.token !== 'string' || !payload.token) return false;
                saveAuth(payload);
                return true;
            }).catch(error => {
                console.error('Unable to refresh the sign-in session.', error);
                return false;
            }).finally(() => {
                refreshPromise = null;
            });
        }
        return refreshPromise;
    }

    function isApiUrl(url) {
        try {
            const parsed = new URL(url, window.location.origin);
            return parsed.origin === window.location.origin &&
                (parsed.pathname === API_PREFIX || parsed.pathname.startsWith(`${API_PREFIX}/`));
        } catch (error) {
            return false;
        }
    }

    function normalizeApiUrl(input) {
        const inputUrl = input instanceof Request ? input.url : String(input);
        const parsed = new URL(inputUrl, window.location.origin);
        if (parsed.origin !== window.location.origin) return input;
        if (parsed.pathname.startsWith(`${API_PREFIX}/`) || parsed.pathname === API_PREFIX) {
            return parsed.href;
        }
        if (parsed.pathname.startsWith('/static/') || parsed.pathname.includes('.')) {
            return input;
        }
        parsed.pathname = `${API_PREFIX}${parsed.pathname.startsWith('/') ? '' : '/'}${parsed.pathname}`;
        return parsed.href;
    }

    function authRedirect(responseUrl) {
        if (redirectingForAuth) return;
        const pathname = new URL(responseUrl, window.location.origin).pathname;
        if (isAuthEndpoint(pathname)) {
            return;
        }
        redirectingForAuth = true;
        clearCredentials();
        if (window.location.pathname !== '/login') {
            window.location.assign('/login');
        }
    }

    function captureJsonResponse(response, requestUrl) {
        const isAuthUpdate = /\/api\/auth\/(login|register)(?:[/?#]|$)/.test(requestUrl);
        const isMatchingResponse = isMatchingRequest(requestUrl);
        if (!isAuthUpdate && !isMatchingResponse) return response;
        const originalJson = response.json.bind(response);
        let parsedJson;
        response.json = function () {
            if (!parsedJson) {
                parsedJson = originalJson().then(payload => {
                    if (response.ok && isAuthUpdate && payload && payload.token) saveAuth(payload);
                    if (response.ok && isMatchingResponse) collectMatchingJobs(payload);
                    return payload;
                });
            }
            return parsedJson;
        };
        return response;
    }

    function isMatchingRequest(url) {
        return /\/api\/jobs\/recommended(?:[/?#]|$)/.test(url);
    }

    function showJobSkeletons() {
        const container = document.getElementById('jobList');
        if (!container) return;
        container.innerHTML = '';
        for (let index = 0; index < 3; index += 1) {
            const skeleton = document.createElement('div');
            skeleton.className = 'app-skeleton-card';
            skeleton.setAttribute('aria-hidden', 'true');
            skeleton.innerHTML = '<span class="app-skeleton app-skeleton-circle"></span>' +
                '<span class="app-skeleton-copy"><span class="app-skeleton"></span>' +
                '<span class="app-skeleton app-skeleton-short"></span></span>';
            container.appendChild(skeleton);
        }
    }

    function collectMatchingJobs(payload) {
        if (!Array.isArray(payload)) return;
        payload.forEach(job => {
            if (job && job._id) MATCHING_JOBS.set(String(job._id), job);
        });
        enhanceJobCards();
    }

    function parseJobId(card) {
        if (card.dataset.jobId) return card.dataset.jobId;
        const button = card.querySelector('[onclick*="applyJob"], [onclick*="toggleApply"]');
        if (!button) return null;
        const match = button.getAttribute('onclick').match(/(?:applyJob|toggleApply)\(\s*['"]([^'"]+)['"]/);
        return match ? match[1] : null;
    }

    function matchColor(score) {
        if (score >= 75) return { stroke: '#16a34a', text: '#15803d', label: 'Strong match' };
        if (score >= 50) return { stroke: '#d97706', text: '#b45309', label: 'Good match' };
        return { stroke: '#dc2626', text: '#b91c1c', label: 'Low match' };
    }

    function createGauge(value, size) {
        const score = Math.max(0, Math.min(100, Number(value) || 0));
        const colors = matchColor(score);
        const dimension = size || 76;
        const radius = 30;
        const circumference = 2 * Math.PI * radius;
        const wrapper = document.createElement('div');
        wrapper.className = 'app-match-gauge';
        wrapper.setAttribute('role', 'img');
        wrapper.setAttribute('aria-label', `${score}% match: ${colors.label}`);
        wrapper.style.setProperty('--match-color', colors.stroke);
        wrapper.style.width = `${dimension}px`;
        wrapper.style.height = `${dimension}px`;

        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 76 76');
        svg.setAttribute('aria-hidden', 'true');
        svg.classList.add('app-match-gauge-svg');

        const track = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        track.setAttribute('cx', '38');
        track.setAttribute('cy', '38');
        track.setAttribute('r', String(radius));
        track.setAttribute('fill', 'none');
        track.setAttribute('stroke', '#e2e8f0');
        track.setAttribute('stroke-width', '7');

        const progress = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        progress.setAttribute('cx', '38');
        progress.setAttribute('cy', '38');
        progress.setAttribute('r', String(radius));
        progress.setAttribute('fill', 'none');
        progress.setAttribute('stroke', colors.stroke);
        progress.setAttribute('stroke-width', '7');
        progress.setAttribute('stroke-linecap', 'round');
        progress.setAttribute('stroke-dasharray', `${circumference}`);
        progress.setAttribute('stroke-dashoffset', `${circumference * (1 - score / 100)}`);
        progress.setAttribute('transform', 'rotate(-90 38 38)');
        svg.append(track, progress);

        const valueLabel = document.createElement('span');
        valueLabel.className = 'app-match-gauge-value';
        valueLabel.style.color = colors.text;
        valueLabel.textContent = `${score}%`;
        wrapper.append(svg, valueLabel);
        return wrapper;
    }

    function createSkillGroup(title, skills, matched) {
        const group = document.createElement('div');
        group.className = 'app-skill-group';
        const label = document.createElement('span');
        label.className = 'app-skill-label';
        label.textContent = title;
        group.appendChild(label);

        const badges = document.createElement('div');
        badges.className = 'app-skill-badges';
        (Array.isArray(skills) ? skills : []).forEach(skill => {
            const badge = document.createElement('span');
            badge.className = matched ? 'app-skill-badge app-skill-matched' : 'app-skill-badge app-skill-missing';
            badge.textContent = matched ? `✓ ${String(skill)}` : String(skill);
            badges.appendChild(badge);
        });
        if (!badges.childElementCount) {
            const empty = document.createElement('span');
            empty.className = 'app-skill-empty';
            empty.textContent = 'None';
            badges.appendChild(empty);
        }
        group.appendChild(badges);
        return group;
    }

    function enhanceCard(card, job) {
        if (card.dataset.metricsEnhanced === 'true') return;
        const score = Number(job.matchPercentage);
        if (!Number.isFinite(score)) return;

        card.querySelectorAll('.job-info p').forEach(node => {
            if (/^(?:Matched skills|Missing skills):/i.test(node.textContent.trim())) node.remove();
        });
        const gauge = createGauge(score, 76);
        const miniCircle = card.querySelector('.mini-circle');
        if (miniCircle) {
            miniCircle.replaceWith(gauge);
        } else {
            const matchRow = Array.from(card.querySelectorAll('.job-info p')).find(node =>
                /AI Match:/i.test(node.textContent));
            if (matchRow) matchRow.replaceWith(gauge);
            else card.querySelector('.job-info')?.prepend(gauge);
        }

        let skills = card.querySelector('.app-skill-matrix');
        if (!skills) {
            skills = document.createElement('div');
            skills.className = 'app-skill-matrix';
            const info = card.querySelector('.job-info') || card;
            info.appendChild(skills);
        }
        skills.append(
            createSkillGroup('Matched skills', job.matchedSkills, true),
            createSkillGroup('Skills to build', job.missingSkills, false)
        );
        if (typeof job.alreadyApplied === 'boolean') {
            const applyButton = card.querySelector('[onclick*="applyJob"], [onclick*="toggleApply"], .apply-btn');
            const belowThreshold = applyButton && /match must be at least/i.test(applyButton.title);
            if (applyButton && (job.alreadyApplied || !belowThreshold)) {
                updateApplyButton(applyButton, job.alreadyApplied);
            }
        }
        card.dataset.metricsEnhanced = 'true';
    }

    function enhanceJobCards() {
        document.querySelectorAll('.job-card').forEach(card => {
            const job = MATCHING_JOBS.get(String(parseJobId(card) || ''));
            if (job) enhanceCard(card, job);
        });
        enhanceJobModal();
    }

    function enhanceJobModal() {
        const button = document.getElementById('modalApplyBtn');
        const body = document.getElementById('modalBody');
        const jobId = button && button.dataset.jobId;
        const job = jobId && MATCHING_JOBS.get(String(jobId));
        if (!body || !job || body.querySelector('.app-modal-metrics')) return;

        body.querySelectorAll('p').forEach(node => {
            if (/^(?:Match Score|Matched Skills|Missing Skills):/i.test(node.textContent.trim())) node.remove();
        });
        const metrics = document.createElement('div');
        metrics.className = 'app-modal-metrics';
        metrics.append(
            createGauge(job.matchPercentage, 76),
            createSkillGroup('Matched skills', job.matchedSkills, true),
            createSkillGroup('Skills to build', job.missingSkills, false)
        );
        const separator = body.querySelector('hr');
        body.insertBefore(metrics, separator || body.firstChild);
    }

    function installStyles() {
        if (document.getElementById('app-engine-styles')) return;
        const style = document.createElement('style');
        style.id = 'app-engine-styles';
        style.textContent = `
            @keyframes app-pulse { 50% { opacity: .42; } }
            .app-skeleton-card { display:flex; align-items:center; gap:16px; padding:20px; margin:12px 0; border:1px solid #e2e8f0; border-radius:16px; background:#fff; }
            .app-skeleton { display:block; height:12px; width:100%; border-radius:999px; background:#e2e8f0; animation:app-pulse 1.2s ease-in-out infinite; }
            .app-skeleton-circle { width:64px; height:64px; border-radius:50%; flex:none; }
            .app-skeleton-copy { display:grid; width:min(360px,70%); gap:12px; }
            .app-skeleton-short { width:62%; }
            .app-match-gauge { position:relative; display:inline-grid; flex:none; place-items:center; vertical-align:middle; }
            .app-match-gauge-svg { width:100%; height:100%; transform:rotate(0); }
            .app-match-gauge-value { position:absolute; font-weight:700; font-size:15px; }
            .app-skill-matrix { display:grid; gap:10px; margin-top:14px; }
            .app-skill-group { display:grid; gap:6px; }
            .app-skill-label { color:#64748b; font-size:11px; font-weight:700; letter-spacing:.06em; text-transform:uppercase; }
            .app-skill-badges { display:flex; flex-wrap:wrap; gap:6px; }
            .app-skill-badge { border-radius:999px; padding:4px 10px; font-size:12px; line-height:1.5; }
            .app-skill-matched { color:#166534; background:#dcfce7; }
            .app-skill-missing { color:#64748b; background:#f8fafc; border:1px dashed #cbd5e1; }
            .app-skill-empty { color:#94a3b8; font-size:12px; }
            .app-toast-region { position:fixed; z-index:10000; top:20px; right:20px; display:grid; gap:10px; width:min(380px,calc(100vw - 32px)); }
            .app-toast { display:flex; align-items:flex-start; gap:12px; border:1px solid #e2e8f0; border-radius:14px; background:#fff; padding:14px 16px; color:#172033; box-shadow:0 16px 40px rgba(15,23,42,.16); animation:app-toast-in .2s ease-out; }
            .app-toast[data-kind="error"] { border-color:#fecaca; }
            .app-toast[data-kind="success"] { border-color:#bbf7d0; }
            .app-toast-message { flex:1; font-size:14px; line-height:1.5; }
            .app-toast-close { border:0; background:transparent; color:#64748b; cursor:pointer; font-size:18px; line-height:1; }
            @keyframes app-toast-in { from { opacity:0; transform:translateY(-8px); } to { opacity:1; transform:translateY(0); } }
            @media (max-width:520px) { .app-toast-region { top:12px; right:12px; } }
        `;
        document.head.appendChild(style);
    }

    function showToast(message, kind) {
        if (!document.body) return;
        let region = document.querySelector('.app-toast-region');
        if (!region) {
            region = document.createElement('div');
            region.className = 'app-toast-region';
            region.setAttribute('aria-live', 'polite');
            region.setAttribute('aria-relevant', 'additions');
            document.body.appendChild(region);
        }
        const toast = document.createElement('div');
        toast.className = 'app-toast';
        toast.dataset.kind = kind || 'info';
        const text = document.createElement('span');
        text.className = 'app-toast-message';
        text.textContent = String(message || 'Request completed.');
        const close = document.createElement('button');
        close.className = 'app-toast-close';
        close.type = 'button';
        close.setAttribute('aria-label', 'Dismiss notification');
        close.textContent = '×';
        close.addEventListener('click', () => toast.remove());
        toast.append(text, close);
        region.appendChild(toast);
        window.setTimeout(() => toast.remove(), 5000);
    }

    function endpointUrl(url) {
        return url.startsWith('/') && !url.startsWith(`${API_PREFIX}/`) && url !== API_PREFIX
            ? `${API_PREFIX}${url}`
            : url;
    }

    async function readPayload(response) {
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) return {};
        try {
            return await response.json();
        } catch (error) {
            if (!(error instanceof SyntaxError)) throw error;
            throw new Error('The server returned an unreadable response.');
        }
    }

    async function fetchWithAuth(url, options) {
        const requestOptions = Object.assign({}, options || {});
        return window.fetch(endpointUrl(String(url)), requestOptions);
    }

    async function loadProtectedImage(image, url) {
        if (!(image instanceof HTMLImageElement) || typeof url !== 'string' || !url) {
            return;
        }
        if (!url.startsWith('/api/')) {
            image.src = url;
            return;
        }
        const response = await fetchWithAuth(url, { method: 'GET' });
        if (!response.ok) {
            throw new Error('Unable to load the private profile image.');
        }
        const objectUrl = URL.createObjectURL(await response.blob());
        const previousUrl = image.dataset.objectUrl;
        image.src = objectUrl;
        image.dataset.objectUrl = objectUrl;
        if (previousUrl) URL.revokeObjectURL(previousUrl);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, character => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        })[character]);
    }

    function setBusy(form, busy, label) {
        const submit = form.querySelector('[type="submit"]');
        if (!submit) return;
        if (busy) {
            submit.dataset.appOriginalText = submit.textContent;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            submit.textContent = label || 'Working…';
        } else {
            submit.disabled = false;
            submit.removeAttribute('aria-busy');
            if (submit.dataset.appOriginalText) {
                submit.textContent = submit.dataset.appOriginalText;
                delete submit.dataset.appOriginalText;
            }
        }
    }

    function inputValue(form, ...names) {
        for (const name of names) {
            const field = form.querySelector(`[name="${name}"], #${name}`);
            if (field && field.type !== 'file') return field.value.trim();
        }
        return '';
    }

    function routeForRole(role, registration) {
        if (registration) return role === 'recruiter' ? '/recruiter/profile' : '/profile';
        if (role === 'recruiter') return '/dashboard/recruiter';
        if (role === 'admin') return '/dashboard/admin';
        return '/dashboard';
    }

    function loginRole(form) {
        if (form.id === 'recruiterForm') return 'recruiter';
        if (form.id === 'adminForm') return 'admin';
        return form.dataset.role || 'jobseeker';
    }

    function isLoginForm(form) {
        return form.matches('#loginForm, #jobForm, #recruiterForm, #adminForm') ||
            form.dataset.apiAction === 'login';
    }

    function isRegistrationForm(form) {
        return form.matches('#registerForm') || form.dataset.apiAction === 'register' ||
            /^\/register\/(?:seeker|recruiter)\/?$/.test(window.location.pathname);
    }

    function registrationRole(form) {
        return form.dataset.role ||
            (window.location.pathname.includes('recruiter') ? 'recruiter' : 'jobseeker');
    }

    function profileFormPayload(form) {
        const payload = new FormData();
        const fields = ['name', 'email', 'skills', 'experience', 'company', 'company_type', 'location', 'website', 'hiring_roles'];
        fields.forEach(field => {
            const input = form.querySelector(`[name="${field}"], #${field}`);
            if (input && input.type !== 'file') payload.append(field, input.value.trim());
        });
        form.querySelectorAll('input[type="file"]').forEach(input => {
            if (!input.files || !input.files[0]) return;
            let fieldName = input.name;
            if (!fieldName) {
                if (/resume/i.test(input.id)) fieldName = 'resume';
                else if (/photo|image|avatar/i.test(input.id)) fieldName = 'profile_image';
            }
            if (fieldName === 'resume' || fieldName === 'profile_image') {
                payload.append(fieldName, input.files[0]);
            }
        });
        return payload;
    }

    async function handleLogin(form) {
        const role = loginRole(form);
        const email = inputValue(form, 'email', `${role === 'jobseeker' ? 'job' : role === 'recruiter' ? 'rec' : 'admin'}Email`);
        const password = inputValue(form, 'password', `${role === 'jobseeker' ? 'job' : role === 'recruiter' ? 'rec' : 'admin'}Pass`);
        if (!email || !password) throw new Error('Enter your email and password.');
        const response = await fetchWithAuth('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, role })
        });
        const payload = await readPayload(response);
        if (!response.ok) throw new Error(payload.error || 'Unable to sign in.');
        saveAuth(payload);
        showToast('Signed in successfully.', 'success');
        window.setTimeout(() => window.location.assign(routeForRole(role, false)), 500);
    }

    async function handleRegistration(form) {
        const role = registrationRole(form);
        const payload = {
            name: inputValue(form, 'name'),
            email: inputValue(form, 'email'),
            password: inputValue(form, 'password'),
            role
        };
        if (role === 'recruiter') {
            payload.company = inputValue(form, 'company');
            payload.company_type = inputValue(form, 'company_type');
            payload.location = inputValue(form, 'location');
            payload.website = inputValue(form, 'website');
            payload.hiring_roles = inputValue(form, 'hiring_roles');
        } else {
            payload.skills = inputValue(form, 'skills');
            payload.experience = inputValue(form, 'experience');
        }
        const response = await fetchWithAuth('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await readPayload(response);
        if (!response.ok) throw new Error(result.error || 'Unable to create your account.');
        saveAuth(result);
        showToast('Account created successfully.', 'success');
        window.setTimeout(() => window.location.assign(routeForRole(role, true)), 500);
    }

    async function handleProfileUpdate(form) {
        const response = await fetchWithAuth('/users/profile', {
            method: 'PUT',
            body: profileFormPayload(form)
        });
        const payload = await readPayload(response);
        if (!response.ok) throw new Error(payload.error || 'Unable to save your profile.');
        if (payload.profile_image) {
            const profileImage = form.querySelector('#avatar, #profilePic');
            if (profileImage) await loadProtectedImage(profileImage, payload.profile_image);
        }
        showToast(payload.resume_parse_message || payload.message || 'Profile updated.', 'success');
        window.setTimeout(() => {
            window.location.assign(getUser()?.role === 'recruiter' ? '/dashboard/recruiter' : '/dashboard/seeker');
        }, 900);
    }

    async function handleJobPost(form) {
        const payload = {};
        ['title', 'company', 'location', 'description', 'skills'].forEach(field => {
            payload[field] = inputValue(form, field);
        });
        const response = await fetchWithAuth('/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await readPayload(response);
        if (!response.ok) throw new Error(result.error || 'Unable to post this job.');
        showToast(result.message || 'Job posted successfully.', 'success');
        window.setTimeout(() => window.location.assign('/recruiter/jobs'), 700);
    }

    function findFormAction(form) {
        if (isLoginForm(form)) return { label: 'Signing in…', run: () => handleLogin(form) };
        if (isRegistrationForm(form)) return { label: 'Creating account…', run: () => handleRegistration(form) };
        if (form.matches('#profileUpdateForm, #profileForm') || form.dataset.apiAction === 'profile') {
            return { label: 'Saving profile…', run: () => handleProfileUpdate(form) };
        }
        if (form.matches('#postJobForm') || form.dataset.apiAction === 'post-job') {
            return { label: 'Posting job…', run: () => handleJobPost(form) };
        }
        return null;
    }

    function handleFormSubmit(event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        const action = findFormAction(form);
        if (!action) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        setBusy(form, true, action.label);
        action.run().catch(error => {
            console.error('Form transaction failed.', error);
            showToast(error.message || 'The request could not be completed.', 'error');
        }).finally(() => setBusy(form, false));
    }

    function setAppliedState(jobId, applied, button) {
        const job = MATCHING_JOBS.get(String(jobId));
        if (job) job.alreadyApplied = applied;
        if (typeof window.setAppliedUI === 'function') window.setAppliedUI(String(jobId), applied);
        document.querySelectorAll(`.job-card[data-job-id="${CSS.escape(String(jobId))}"]`).forEach(card => {
            const applyButton = card.querySelector('[onclick*="applyJob"], [onclick*="toggleApply"], .apply-btn');
            if (applyButton) updateApplyButton(applyButton, applied);
        });
        if (button) updateApplyButton(button, applied);
        const modalButton = document.getElementById('modalApplyBtn');
        if (modalButton && modalButton.dataset.jobId === String(jobId)) updateApplyButton(modalButton, applied);
    }

    function updateApplyButton(button, applied) {
        button.textContent = applied ? '✓ Applied · Withdraw' : 'Apply';
        button.disabled = false;
        button.classList.toggle('btn-applied', applied);
        button.classList.toggle('btn-apply', !applied);
        button.setAttribute('aria-pressed', String(applied));
    }

    async function handleApplyClick(event) {
        if (!(event.target instanceof Element)) return;
        const button = event.target.closest('button');
        if (!button || !button.isConnected) return;
        const inline = button.getAttribute('onclick') || '';
        const match = inline.match(/(?:applyJob|toggleApply)\(\s*['"]([^'"]+)['"]/);
        const modalId = button.id === 'modalApplyBtn' ? button.dataset.jobId : null;
        const jobId = match ? match[1] : modalId;
        if (!jobId) return;
        if (!/applyJob|toggleApply/.test(inline) && !modalId) return;

        const currentJob = MATCHING_JOBS.get(String(jobId));
        const applied = currentJob ? Boolean(currentJob.alreadyApplied) :
            /applied/i.test(button.textContent.trim());
        if (button.disabled && !applied) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = applied ? 'Withdrawing…' : 'Applying…';
        try {
            const response = await fetchWithAuth(`/jobs/${encodeURIComponent(jobId)}/${applied ? 'unapply' : 'apply'}`, {
                method: applied ? 'DELETE' : 'POST'
            });
            const payload = await readPayload(response);
            if (!response.ok) throw new Error(payload.error || 'Unable to update this application.');
            setAppliedState(jobId, !applied, button);
            showToast(payload.message || (applied ? 'Application withdrawn.' : 'Application submitted.'), 'success');
        } catch (error) {
            button.disabled = false;
            button.textContent = originalText;
            console.error('Application update failed.', error);
            showToast(error.message || 'Unable to update this application.', 'error');
        }
    }

    function installFetchInterceptor() {
        window.fetch = async function (input, init) {
            const normalized = normalizeApiUrl(input);
            const requestUrl = normalized instanceof Request ? normalized.url : String(normalized);
            const fetchInput = input instanceof Request ? new Request(normalized, input) : normalized;
            const options = Object.assign({}, init || {});
            const headers = new Headers(fetchInput instanceof Request ? fetchInput.headers : undefined);
            new Headers(options.headers || {}).forEach((value, key) => headers.set(key, value));
            const retryInput = fetchInput instanceof Request ? fetchInput.clone() : fetchInput;

            if (isApiUrl(requestUrl)) {
                const token = getToken();
                if (token) headers.set('Authorization', `Bearer ${token}`);
                else headers.delete('Authorization');
                const body = options.body !== undefined ? options.body :
                    fetchInput instanceof Request ? fetchInput.body : undefined;
                if (body && !(body instanceof FormData) && !headers.has('Content-Type') &&
                    typeof body === 'string' && /^[\[{]/.test(body.trim())) {
                    headers.set('Content-Type', 'application/json');
                }
            }
            options.headers = headers;
            options.credentials = options.credentials || 'same-origin';

            if (isMatchingRequest(requestUrl)) showJobSkeletons();
            let response = await nativeFetch(fetchInput, options);
            if (
                isApiUrl(requestUrl)
                && response.status === 401
                && getToken()
                && !isAuthEndpoint(requestUrl)
                && await refreshAccessToken()
            ) {
                const retryHeaders = new Headers(headers);
                retryHeaders.set('Authorization', `Bearer ${getToken()}`);
                response = await nativeFetch(retryInput, {
                    ...options,
                    headers: retryHeaders
                });
            }
            if (isApiUrl(requestUrl) && response.status === 401) authRedirect(requestUrl);
            captureJsonResponse(response, requestUrl);
            return response;
        };
    }

    function installJobCardEnhancement() {
        const observer = new MutationObserver(enhanceJobCards);
        observer.observe(document.documentElement, { childList: true, subtree: true });
    }

    function install() {
        installStyles();
        installFetchInterceptor();
        document.addEventListener('submit', handleFormSubmit, true);
        document.addEventListener('click', handleApplyClick, true);
        installJobCardEnhancement();

        window.getToken = getToken;
        window.getUser = getUser;
        window.isLoggedIn = () => Boolean(getToken());
        window.logout = async function () {
            const csrfToken = readCookie(REFRESH_CSRF_COOKIE);
            try {
                await nativeFetch('/api/auth/logout', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : {}
                });
            } catch (error) {
                console.error('Unable to revoke the sign-in session.', error);
            } finally {
                clearCredentials();
                window.location.assign('/login');
            }
        };
        window.fetchWithAuth = fetchWithAuth;
        window.loadProtectedImage = loadProtectedImage;
        window.escapeHtml = escapeHtml;
        window.showAppToast = showToast;
        window.alert = function (message) {
            showToast(message, 'info');
        };
        window.withAppLoading = async function (containerOrSelector, operation, count) {
            const container = typeof containerOrSelector === 'string'
                ? document.querySelector(containerOrSelector)
                : containerOrSelector;
            if (!(container instanceof Element) || typeof operation !== 'function') {
                throw new TypeError('withAppLoading requires a container and an operation function.');
            }
            const original = container.innerHTML;
            container.innerHTML = '';
            for (let index = 0; index < (count || 3); index += 1) {
                const skeleton = document.createElement('div');
                skeleton.className = 'app-skeleton-card';
                skeleton.setAttribute('aria-hidden', 'true');
                skeleton.innerHTML = '<span class="app-skeleton app-skeleton-circle"></span>' +
                    '<span class="app-skeleton-copy"><span class="app-skeleton"></span>' +
                    '<span class="app-skeleton app-skeleton-short"></span></span>';
                container.appendChild(skeleton);
            }
            try {
                return await operation();
            } catch (error) {
                container.innerHTML = original;
                throw error;
            }
        };
        window.renderMatchGauge = createGauge;
        window.renderSkillBadges = createSkillGroup;
    }

    if (document.body) {
        install();
    } else {
        document.addEventListener('DOMContentLoaded', install, { once: true });
    }
})();
