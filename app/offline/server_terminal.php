<?php
session_start();
if (!isset($_SESSION['user_id'])) {
    header('Location: login.php');
    exit;
}
$projectId = $_GET['project_id'] ?? '';
require 'templates/header.php';
?>
<h1>Live Terminal für Projekt <?php echo htmlspecialchars($projectId); ?></h1>
<div id="log" style="background:#000;color:#0f0;font-family:monospace;height:300px;overflow-y:scroll;padding:5px;"></div>
<form id="cmdForm">
    <input type="text" id="cmdInput" autocomplete="off" style="width:80%;">
    <button type="submit">Senden</button>
</form>
<p><a href="server_control.php?project_id=<?php echo urlencode($projectId); ?>">Zurück</a></p>
<script>
function fetchLog() {
    fetch('http://localhost:5000/projects/<?php echo $projectId; ?>/log')
        .then(r => r.json())
        .then(data => {
            const logElem = document.getElementById('log');
            logElem.textContent = data.log;
            logElem.scrollTop = logElem.scrollHeight;
        });
}
setInterval(fetchLog, 2000);
fetchLog();

document.getElementById('cmdForm').addEventListener('submit', function(e) {
    e.preventDefault();
    const cmd = document.getElementById('cmdInput').value;
    fetch('http://localhost:5000/projects/<?php echo $projectId; ?>/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command: cmd})
    }).then(() => { document.getElementById('cmdInput').value = ''; });
});
</script>
<?php require 'templates/footer.php'; ?>
