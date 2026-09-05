# Lokale To-do-App

Eine lokale, keyboard-first To-do-Anwendung für einen Benutzer. Sie verwendet ausschließlich Python 3, SQLite und Vanilla HTML/CSS/JavaScript. Mit Docker ist keine lokale Python-Installation nötig.

## Start mit Docker

Im Projektordner:

```console
docker compose up
```

Beim ersten Start wird das offizielle Python-Basisimage geladen und das kleine lokale Image gebaut. Danach im Browser [http://127.0.0.1:8765](http://127.0.0.1:8765) öffnen. Der veröffentlichte Port ist ausschließlich an `127.0.0.1` gebunden.

Beenden mit `Ctrl+C`. Im Hintergrund starten und später beenden:

```console
docker compose up -d
docker compose down
```

## Start ohne Docker

Falls Python 3 vorhanden ist:

```console
python app.py
```

## Bedienung

In der Tabelle navigieren Pfeiltasten und Tab zwischen Zellen. Enter oder F2 startet die Bearbeitung; Enter speichert und springt in derselben Spalte nach unten. Esc verwirft die laufende Zellbearbeitung. `Alt+N` oder `Ctrl+Shift+N` öffnet die Schnelleingabe, `Ctrl+Shift+P` die Command Palette und `Ctrl+Enter` erzeugt direkt einen leeren Task. Unter macOS stehen zusätzlich die browserfreundlichen Kombinationen `Ctrl+Option+N` für die Schnelleingabe und `Ctrl+Option+P` für die Command Palette bereit. `Ctrl+Leertaste` schaltet den aktuellen Task um. `Ctrl+Delete` beziehungsweise auf dem Mac `Ctrl+Backspace` oder `Cmd+Backspace` verschiebt ihn in den Papierkorb. `?` zeigt alle wichtigen Kürzel.

Die Schnelleingabe versteht beispielsweise `Angebot prüfen @Müller Q2 2027 #Kunde !Netzausbau %Vertrag ^127+2w https://example.com`. Mehrwort-Werte werden wie `@"Max Mustermann"` in Anführungszeichen gesetzt. Dependency-Offsets sind mit `d`, `w`, `m` und `y` möglich, etwa `^127+3d` oder `^127+1m`.

Terminfelder verstehen exakte und ungefähre Angaben: `03.10.2026`, `15.10.`, `heute`, `+3`, `+1w`, `KW 33`, `KW33/2027`, `September 2027`, `09/2027`, `Q2 2027` und `2027`. Die Tabelle erhält diese Genauigkeit; die Timeline zeigt ungefähre Termine als dezente Zeitfenster.

Ein Dependency-Offset erzeugt nur eine dynamische Empfehlung. Ein eigener Termin wird dadurch weder gesetzt noch verschoben. Beispiel: Aus `September 2026` und `^127+2w` wird der empfohlene Bereich `15.09.2026–14.10.2026`, ohne ihn als Fälligkeit zu speichern.

## Tests

```console
python -m unittest discover -s tests -v
```

## Daten und Backup

Alle Fachdaten, Einstellungen und die vollständige Historie liegen immer in `data/todo.sqlite` – unabhängig davon, ob die Anwendung mit Docker oder direkt mit Python gestartet wird. Der Ordner und die Datei werden bei Bedarf automatisch angelegt.

„Backup erstellen“ lädt eine konsistente, über die SQLite-Backup-API erzeugte Kopie herunter. Zum Wiederherstellen:

1. Anwendung beenden.
2. Die aktuelle `data/todo.sqlite` sichern oder umbenennen.
3. Das gewünschte Backup nach `data/todo.sqlite` kopieren.
4. Anwendung erneut starten.

Es werden keine externen Requests, Cloud-Dienste, Telemetrie oder Zusatzdateien für den normalen Datenbankbetrieb benötigt.
