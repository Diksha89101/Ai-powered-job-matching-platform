function validateRegister(event) {
    event.preventDefault();

    const errors = ['nameError', 'emailError', 'passwordError', 'confirmError', 'roleError'];
    errors.forEach(id => {
        const element = document.getElementById(id);
        if (element) element.textContent = '';
    });

    const name = document.getElementById('name').value.trim();
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const confirm = document.getElementById('confirmPassword').value;
    const role = document.getElementById('role').value;

    let isValid = true;
    const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

    if (!name) {
        document.getElementById('nameError').textContent = 'Name is required';
        isValid = false;
    }

    if (!email) {
        document.getElementById('emailError').textContent = 'Email is required';
        isValid = false;
    } else if (!emailPattern.test(email)) {
        document.getElementById('emailError').textContent = 'Enter a valid email';
        isValid = false;
    }

    if (password.length < 8 || !/[A-Z]/.test(password) || !/[a-z]/.test(password) || !/\d/.test(password)) {
        document.getElementById('passwordError').textContent = 'Use at least 8 characters with uppercase, lowercase, and a number';
        isValid = false;
    }

    if (password !== confirm) {
        document.getElementById('confirmError').textContent = 'Passwords do not match';
        isValid = false;
    }

    if (!role) {
        document.getElementById('roleError').textContent = 'Please select a role';
        isValid = false;
    }

    return isValid;
}
