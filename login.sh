#!/bin/bash
source .env

ssh -oPort=$PORT $USER@$HOST 