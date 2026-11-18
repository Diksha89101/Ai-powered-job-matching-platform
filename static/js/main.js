function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[character]));
}

const API_URL = '/api';

function getToken() {
    return localStorage.getItem('token');
}

function getUser() {
    const user = localStorage.getItem('user');
    return user ? JSON.parse(user) : null;
}

function isLoggedIn() {
    return !!getToken();
}

function logout() {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    window.location.href = '/login';
}

async function fetchWithAuth(url, options = {}) {
    const token = localStorage.getItem("token");
    const headers = { ...(options.headers || {}) };

    if (!(options.body instanceof FormData) && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
    }
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}${url}`, {
        ...options,
        headers
    });

    if (response.status === 401) {
        logout();
        throw new Error('Session expired');
    }
    return response;
}

(function addBackButton() {
    if (document.body.classList.contains('landing-page') || document.body.classList.contains('jobseeker-home-page')) return;
    if (document.getElementById('globalBackBtn')) return;

    const sidebar = document.querySelector('.sidebar');
    const navbar = document.querySelector('.navbar');

    const backButton = document.createElement('button');
    backButton.id = 'globalBackBtn';
    backButton.type = 'button';
    backButton.setAttribute('aria-label', 'Go back');
    backButton.title = 'Back';
    backButton.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" width="28" height="28" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M19 12H5m0 0 7 7m-7-7 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    backButton.style.cssText = 'display:flex;align-items:center;justify-content:center;width:40px;height:40px;padding:0;border:0;background:transparent;color:inherit;cursor:pointer;transition:transform .2s ease,color .2s ease;z-index:9999;';
    backButton.onmouseenter = () => {
        backButton.style.transform = 'translateX(-3px)';
        backButton.style.color = '#a78bfa';
    };
    backButton.onmouseleave = () => {
        backButton.style.transform = 'translateX(0)';
        backButton.style.color = '';
    };
    backButton.onclick = () => {
        if (window.history.length > 1) {
            window.history.back();
        } else {
            window.location.href = '/';
        }
    };

    if (sidebar) {
        backButton.style.position = 'fixed';
        backButton.style.top = '16px';
        backButton.style.left = '16px';
        document.body.appendChild(backButton);
        return;
    }

    backButton.style.position = 'fixed';
    if (navbar) {
        backButton.style.top = '76px';
        backButton.style.left = '12px';
    } else {
        backButton.style.top = '16px';
        backButton.style.left = '16px';
    }
    backButton.style.color = 'white';
    document.body.appendChild(backButton);
})();

(function installSlidingMenu() {
    if (document.body.classList.contains('landing-page') || document.body.classList.contains('jobseeker-home-page')) return;
    const menu = document.querySelector('.sidebar, .navbar, body > header');
    if (!menu || document.querySelector('.slide-menu-toggle')) return;

    menu.classList.add('slide-menu');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'slide-menu-toggle';
    toggle.setAttribute('aria-label', 'Open navigation menu');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

    document.body.appendChild(toggle);

    toggle.addEventListener('click', () => {
        const isOpen = menu.classList.toggle('menu-open');
        toggle.setAttribute('aria-expanded', String(isOpen));
        toggle.setAttribute('aria-label', isOpen ? 'Close navigation menu' : 'Open navigation menu');
    });
})();

// Keep the login role tabs working even when inline click handlers are blocked
// or replaced by browser extensions/CSP tooling.
document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const tab = target && target.closest('.toggle .tab');
    if (!tab) return;

    const tabs = Array.from(document.querySelectorAll('.toggle .tab'));
    const role = ['job', 'recruiter', 'admin'][tabs.indexOf(tab)];
    const forms = {
        job: document.getElementById('jobForm'),
        recruiter: document.getElementById('recruiterForm'),
        admin: document.getElementById('adminForm')
    };
    if (!role || !forms[role]) return;

    tabs.forEach((item) => item.classList.toggle('active', item === tab));
    Object.entries(forms).forEach(([formRole, form]) => {
        form.classList.toggle('active', formRole === role);
    });
});

function setCircleProgress(canvasId, percentage) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const size = 80;
    canvas.width = size;
    canvas.height = size;
    const centerX = size/2, centerY = size/2;
    const radius = 30;
    const startAngle = -Math.PI/2;
    const endAngle = startAngle + (percentage / 100) * 2 * Math.PI;
    
    ctx.clearRect(0, 0, size, size);
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, 0, 2 * Math.PI);
    ctx.strokeStyle = 'rgba(255,255,255,0.1)';
    ctx.lineWidth = 6;
    ctx.stroke();
    
    ctx.beginPath();
    ctx.arc(centerX, centerY, radius, startAngle, endAngle);
    ctx.strokeStyle = '#6366f1';
    ctx.stroke();
    
    ctx.fillStyle = 'white';
    ctx.font = 'bold 14px Segoe UI';
    ctx.fillText(`${percentage}%`, centerX-15, centerY+5);
}
