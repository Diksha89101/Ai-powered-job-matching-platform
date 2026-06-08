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
    window.location.href = '/login.html';
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
    if (document.getElementById('globalBackBtn')) return;
    if (window.location.pathname === '/' || window.location.pathname === '/landing.html') return;

    const sidebar = document.querySelector('.sidebar');
    const navbar = document.querySelector('.navbar');

    if (sidebar) {
        const backLink = document.createElement('a');
        backLink.id = 'globalBackBtn';
        backLink.href = 'javascript:history.back()';
        backLink.textContent = '\u2B05 Back';
        backLink.style.cssText = 'display:inline-block;margin-top:4px;margin-bottom:20px;padding:6px 14px;border-radius:8px;background:#6366f1;color:white;text-decoration:none;font-size:13px;font-family:"Segoe UI",sans-serif;cursor:pointer;';
        const title = sidebar.querySelector('h2, h1');
        if (title) {
            sidebar.insertBefore(backLink, title);
        } else {
            sidebar.insertBefore(backLink, sidebar.firstChild);
        }
        return;
    }

    const btn = document.createElement('button');
    btn.id = 'globalBackBtn';
    btn.innerHTML = '\u2B05';
    btn.title = 'Back';
    btn.style.cssText = 'position:fixed;z-index:9999;width:36px;height:36px;border-radius:8px;background:#6366f1;border:none;color:white;cursor:pointer;font-size:18px;display:flex;align-items:center;justify-content:center;';
    btn.onclick = () => history.back();

    if (navbar) {
        btn.style.top = '84px';
        btn.style.left = '16px';
    } else {
        btn.style.top = '16px';
        btn.style.left = '16px';
    }

    document.body.appendChild(btn);
})();

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
