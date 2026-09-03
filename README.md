# Lokale To-do-App

Eine vollständig offline laufende, keyboard-first To-do-Anwendung für einen Benutzer. Sie verwendet ausschließlich Python 3, SQLite und Vanilla HTML/CSS/JavaScript; es gibt keinen Installations- oder Build-Schritt.

## Start

Im Projektordner:

```console
python app.py
```

Unter Windows funktioniert je nach Python-Installation alternativ `py app.py`. Danach im Browser [http://127.0.0.1:8765](http://127.0.0.1:8765) öffnen. Der Server bindet ausschließlich an `127.0.0.1`.

## Bedienung

In der Tabelle navigieren Pfeiltasten und Tab zwischen Zellen. Enter oder F2 startet die Bearbeitung; Enter speichert und springt in derselben Spalte nach unten. Esc verwirft die laufende Zellbearbeitung. `Ctrl+N`/`Ctrl+Enter` erzeugt einen Task, `Ctrl+Leertaste` schaltet den aktuellen Task um und `Ctrl+Delete` verschiebt ihn in den Papierkorb. `?` zeigt alle wichtigen Kürzel.

Datumsfelder verstehen unter anderem `03.10.2026`, `03.10.26`, `15.10.`, `heute`, `morgen`, `+3`, `+15` und `+1w`.

## Tests

```console
python -m unittest discover -s tests -v
```

## Daten und Backup

Alle Fachdaten, Einstellungen und die vollständige Historie liegen in `todo.sqlite` direkt im Projektordner. Die Datei wird beim ersten Start automatisch angelegt.

„Backup erstellen“ lädt eine konsistente, über die SQLite-Backup-API erzeugte Kopie herunter. Zum Wiederherstellen:

1. Anwendung beenden.
2. Die aktuelle `todo.sqlite` sichern oder umbenennen.
3. Das gewünschte Backup in den Projektordner kopieren und in `todo.sqlite` umbenennen.
4. Anwendung erneut starten.

Es werden keine externen Requests, Cloud-Dienste, Telemetrie oder Zusatzdateien für den normalen Datenbankbetrieb benötigt.
