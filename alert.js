let correctId = null;
let correctPassword = null;

function registerUser() {
  const newId = document.getElementById("newUserId").value;
  const newPassword = document.getElementById("newPassword").value;

  if (newId === "" || newPassword === "") {
    alert("Please enter both ID and Password to register.");
    return;
  }

  correctId = newId;
  correctPassword = newPassword;
  alert("Registration successful!");
}
function checkLogin() {
  const enteredId = document.getElementById("userId").value;
  const enteredPassword = document.getElementById("password").value;

  if (correctId === null || correctPassword === null) {
    alert("No account registered yet. Please register first.");
    return;
  }

  if (enteredId === "" || enteredPassword === "") {
    alert("Please enter both ID and Password.");
    return;
  }

  if (enteredId !== correctId || enteredPassword !== correctPassword) {
    alert("Wrong ID or Password! Please try again.");
  } else {
    alert("Login successful!");
  }
}