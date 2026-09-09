<?php
session_start();
require 'templates/header.php';
?>

    <div class="server-status">
        <h3>Serverstatus</h3>
        <p id="serverIP"></p>
        <p id="statusIP"></p>
        <p id="status">Lade Serverstatus...</p>
        <!-- <div class="player-list" id="player-list"></div>-->
    </div>

<?php require 'templates/footer.php'; ?>