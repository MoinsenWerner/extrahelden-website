// Login mit E-Mail/Passwort
function loginEmail() {
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;

    auth.signInWithEmailAndPassword(email, password)
        .then((userCredential) => {
            document.getElementById('login-message').textContent = 'Erfolgreich eingeloggt!';
            document.getElementById('login-message').style.color = 'green';
            window.location.href = 'index.html'; // Weiterleitung zur Startseite
        })
        .catch((error) => {
            document.getElementById('login-message').textContent = error.message;
            document.getElementById('login-message').style.color = 'red';
        });
}

// Registrierung mit E-Mail/Passwort
function registerEmail() {
    const email = document.getElementById('reg-email').value;
    const password = document.getElementById('reg-password').value;

    auth.createUserWithEmailAndPassword(email, password)
        .then((userCredential) => {
            document.getElementById('register-message').textContent = 'Erfolgreich registriert!';
            document.getElementById('register-message').style.color = 'green';
            window.location.href = 'login.html'; // Weiterleitung zur Login-Seite
        })
        .catch((error) => {
            document.getElementById('register-message').textContent = error.message;
            document.getElementById('register-message').style.color = 'red';
        });
}

// Login mit Google
function loginGoogle() {
    auth.signInWithPopup(providerGoogle)
        .then((result) => {
            window.location.href = 'index.html';
        })
        .catch((error) => {
            alert(error.message);
        });
}

// Login mit GitHub
function loginGitHub() {
    auth.signInWithPopup(providerGitHub)
        .then((result) => {
            window.location.href = 'index.html';
        })
        .catch((error) => {
            alert(error.message);
        });
}

// Serverstatus-Abfrage
async function fetchServerStatus() {
    const serverIP = 'cube-kingdom.de'; // Ersetze mit deiner Server-IP
    const response = await fetch(`https://api.mcsrvstat.us/2/${serverIP}`);
    const data = await response.json();
    const notnow ='kommt demnächst...';

    const serverIPElement = document.getElementById('serverIP');
    const statusElement = document.getElementById('status');
    const playerListElement = document.getElementById('player-list');
    const statusIPElement =document.getElementById('statusIP');

    if (data.online) {
        serverIPElement.innerHTML = `IP: ${serverIP}`;
        statusElement.innerHTML = `🟢 Server ist online! <br> Spieler: ${data.players.online}/${data.players.max}`;
        if (data.players.list) {
            playerListElement.innerHTML = '<strong>Spieler online:</strong><br>' + data.players.list.join('<br>');
        } else {
            playerListElement.innerHTML = 'Keine Spieler online.';
        }
    } else {
        statusIPElement.textContent = `IP: ${serverIP}`;
        statusElement.textContent = '🔴 Server ist aktuell offline.';
        playerListElement.innerHTML = '';
    }
}

// Serverstatus beim Laden der Seite abfragen
if (window.location.pathname.includes('status.php')) {
    fetchServerStatus();
    setInterval(fetchServerStatus, 10000); // Alle 10 Sekunden aktualisieren
}