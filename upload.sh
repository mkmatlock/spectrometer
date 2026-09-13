#!/bin/bash
source .env

sftp -oPort=$PORT $USER@$HOST <<EOF
put -r spectrometer
put spectrometer.service
put setup.sh
EOF

ssh -oPort=$PORT $USER@$HOST