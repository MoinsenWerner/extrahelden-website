<?php
session_start();
if (!isset($_SESSION['user_id'])) {
    header('Location: login.php');
    exit;
}
require 'templates/header.php';

$projectId = $_GET['project_id'] ?? '';
$message = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    $payload = json_encode(['action' => $action]);
    $options = [
        'http' => [
            'header'  => "Content-Type: application/json\r\n",
            'method'  => 'POST',
            'content' => $payload,
        ],
    ];
    $context = stream_context_create($options);
    $api_url = "http://localhost:5000/projects/" . urlencode($projectId) . "/action";
    $result = file_get_contents($api_url, false, $context);
    if ($result !== FALSE) {
        $resp = json_decode($result, true);
        $message = $resp['message'] ?? '';
    } else {
        $message = 'Fehler beim Aufruf der API';
    }
}
?>
<h1>Server Kontrolle für Projekt <?php echo htmlspecialchars($projectId); ?></h1>
<?php if ($message) echo '<p>' . htmlspecialchars($message) . '</p>'; ?>
<form method="post">
    <button name="action" value="start">Start</button>
    <button name="action" value="stop">Stop</button>
    <button name="action" value="restart">Restart</button>
</form>
<p>
    <a href="file_manager.php?project_id=<?php echo urlencode($projectId); ?>">Dateimanager</a> |
    <a href="server_terminal.php?project_id=<?php echo urlencode($projectId); ?>">Terminal</a>
</p>
<?php require 'templates/footer.php'; ?>
