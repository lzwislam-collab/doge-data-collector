#!/bin/sh
# Explicit future invocation only; never installs or fetches anything.
set -eu
set -C
if [ "$#" -ne 3 ]; then
    printf '%s\n' 'usage: sh launch.sh /absolute/python /absolute/repository /absolute/new-output-directory' >&2
    exit 2
fi
python=$1
repo=$2
output=$3
for value in "$python" "$repo" "$output"; do
    case "$value" in /*) ;; *) printf '%s\n' 'all three paths must be absolute' >&2; exit 2 ;; esac
done
console=${output}.launcher.log
status=${output}.exit-code.txt
for path in "$output" "$console" "$status"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
        printf 'refusing existing output/evidence: %s\n' "$path" >&2
        exit 2
    fi
done
# Noclobber protects both launcher evidence files; runner protects its own directory.
if (
    cd "$repo" || exit "$?"
    sha256sum -c offline_acceptance/pr75_linux/SHA256SUMS || exit "$?"
    exec "$python" -I -B -X utf8 offline_acceptance/pr75_linux/run.py --repo "$repo" --output-dir "$output"
) >"$console" 2>&1; then
    result=0
else
    result=$?
fi
printf '%s\n' "$result" >"$status"
cat "$console"
exit "$result"
