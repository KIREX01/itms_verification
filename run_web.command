#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
# run_web.command redirects to master run.command
exec ./run.command
