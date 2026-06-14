#!/bin/bash
# Bumblebee admin bridge — invoked by the User Scripts entry "bumblebee_admin",
# which simply runs:  bash /mnt/user/appdata/bumblebee-docker/_admin.sh
#
# Current job: refresh the docker icons shown in the Unraid GUI. There are TWO
# copies and the GUI serves the second one:
#   1. /var/lib/docker/unraid/images/                          (download cache)
#   2. /usr/local/emhttp/state/plugins/dynamix.docker.manager/images/  (served)
# Unraid won't overwrite either when swapping an icon, so we write both directly
# from the orchestrator's static server. Cache filename = CONTAINER name.
set -u
DST1=/var/lib/docker/unraid/images
DST2=/usr/local/emhttp/state/plugins/dynamix.docker.manager/images
SRC=http://192.168.1.33:5005/files/icons
mkdir -p "$DST1" "$DST2"

fetch() {  # $1 = cache filename (container), $2 = source filename (image)
  local tmp; tmp=$(mktemp)
  if curl -fsS "$SRC/$2" -o "$tmp"; then
    cp -f "$tmp" "$DST1/$1"
    cp -f "$tmp" "$DST2/$1"
    echo "OK    $1  <- $2  ($(stat -c%s "$tmp") bytes)"
  else
    echo "FAIL  $1  <- $SRC/$2"
  fi
  rm -f "$tmp"
}

fetch f5-tts-icon.png                   bumblebee-f5-tts-icon.png
fetch parler-tts-icon.png               bumblebee-parler-tts-icon.png
fetch coqui-tts-icon.png                bumblebee-coqui-tts-icon.png
fetch chatterbox-icon.png               bumblebee-chatterbox-icon.png
fetch audio-converter-icon.png          bumblebee-audio-converter-icon.png
fetch xiaozhi-gateway-icon.png          bumblebee-xiaozhi-gateway-icon.png
fetch whisper-stt-icon.png              bumblebee-whisper-stt-icon.png
fetch bumblebee-orchestrator-icon.png   bumblebee-orchestrator-icon.png
fetch bumblebee-admin-console-icon.png  bumblebee-admin-console-icon.png

echo "DONE — hard-refresh the Docker page (Ctrl+F5) to see the new icons."
