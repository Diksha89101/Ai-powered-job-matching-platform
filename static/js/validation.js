// function validateRegister() {
//     let isValid = true;

//     document.getElementById("nameError").innerText = "";
//     document.getElementById("emailError").innerText = "";
//     document.getElementById("passwordError").innerText = "";
//     document.getElementById("confirmError").innerText = "";
//     document.getElementById("roleError").innerText = "";

//     const name = document.getElementById("name").value.trim();
//     const email = document.getElementById("email").value.trim();
//     const password = document.getElementById("password").value;
//     const confirm = document.getElementById("confirmPassword").value;
//     const role = document.getElementById("role").value;

//     if (name === "") {
//         document.getElementById("nameError").innerText = "Name is required";
//         isValid = false;
//     }

//     if (email === "") {
//         document.getElementById("emailError").innerText = "Email is required";
//         isValid = false;
//     } else if (!email.includes("@")) {
//         document.getElementById("emailError").innerText = "Enter a valid email";
//         isValid = false;
//     }

//     if (password === "") {
//         document.getElementById("passwordError").innerText = "Password is required";
//         isValid = false;
//     } else if (password.length < 6) {
//         document.getElementById("passwordError").innerText = "Minimum 6 characters";
//         isValid = false;
//     }

//     if (confirm === "") {
//         document.getElementById("confirmError").innerText = "Please confirm password";
//         isValid = false;
//     } else if (password !== confirm) {
//         document.getElementById("confirmError").innerText = "Passwords do not match";
//         isValid = false;
//     }

//     if (role === "") {
//         document.getElementById("roleError").innerText = "Please select a role";
//         isValid = false;
//     }

//     if (isValid) {
//         alert("Registration successful!");
//     }
// }

// function validateLogin() {
//     let isValid = true;

//     document.getElementById("loginEmailError").innerText = "";
//     document.getElementById("loginPasswordError").innerText = "";
//     document.getElementById("loginRoleError").innerText = "";

//     const email = document.getElementById("loginEmail").value.trim();
//     const password = document.getElementById("loginPassword").value;
//     const role = document.getElementById("loginRole").value;

//     if (email === "") {
//         document.getElementById("loginEmailError").innerText = "Email is required";
//         isValid = false;
//     }

//     if (password === "") {
//         document.getElementById("loginPasswordError").innerText = "Password is required";
//         isValid = false;
//     }

//     if (role === "") {
//         document.getElementById("loginRoleError").innerText = "Please select your role";
//         isValid = false;
//     }

//     if (isValid) {
//         alert("Login successful!");
//     }
// }
function validateRegister(event) {
    event.preventDefault();

    let isValid = true;

    document.getElementById("nameError").innerText = "";
    document.getElementById("emailError").innerText = "";
    document.getElementById("passwordError").innerText = "";
    document.getElementById("confirmError").innerText = "";
    document.getElementById("roleError").innerText = "";

    const name = document.getElementById("name").value.trim();
    const email = document.getElementById("email").value.trim();
    const password = document.getElementById("password").value;
    const confirm = document.getElementById("confirmPassword").value;
    const role = document.getElementById("role").value;

    const emailPattern = /^[^ ]+@[^ ]+\.[a-z]{2,3}$/;

    if (name === "") {
        document.getElementById("nameError").innerText = "Name is required";
        isValid = false;
    }

    if (email === "") {
        document.getElementById("emailError").innerText = "Email is required";
        isValid = false;
    } else if (!emailPattern.test(email)) {
        document.getElementById("emailError").innerText = "Enter a valid email";
        isValid = false;
    }

    if (password === "") {
        document.getElementById("passwordError").innerText = "Password is required";
        isValid = false;
    } else if (password.length < 6) {
        document.getElementById("passwordError").innerText = "Minimum 6 characters";
        isValid = false;
    }

    if (confirm === "") {
        document.getElementById("confirmError").innerText = "Please confirm password";
        isValid = false;
    } else if (password !== confirm) {
        document.getElementById("confirmError").innerText = "Passwords do not match";
        isValid = false;
    }

    if (role === "") {
        document.getElementById("roleError").innerText = "Please select a role";
        isValid = false;
    }

    if (isValid) {
        alert("Registration successful!");
    }

    return isValid;
}