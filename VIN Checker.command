#!/usr/bin/env bash
# Double-click this in Finder to run the VIN checker in a Terminal window.
cd "$(dirname "$0")"
./vincheck
echo
read -n 1 -s -r -p "Press any key to close this window..."
