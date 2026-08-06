#!/bin/bash
# Double-cliquez ce fichier pour ouvrir le contrôleur XML / XSD en local.
# (En ligne, le site fonctionne sans rien installer.)
cd "$(dirname "$0")" || exit 1
PORT=8000
echo "Contrôleur XML / XSD  →  http://127.0.0.1:$PORT/"
echo "Ctrl+C pour arrêter."
( sleep 1 && open "http://127.0.0.1:$PORT/" ) &
exec python3 -m http.server "$PORT" --bind 127.0.0.1
