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
# Validate before creating even launcher logs: no writes inside the checkout.
for value in "$repo" "$output"; do
    case "$value" in
        /|*/|*//*|*/./*|*/.|*/../*|*/..)
            printf '%s\n' 'repository/output paths must be canonical absolute paths' >&2
            exit 2 ;;
    esac
done
physical_repo=$(cd -P "$repo" && pwd -P) || exit "$?"
parent=${output%/*}
if [ -z "$parent" ]; then parent=/; fi
physical_parent=$(cd -P "$parent" && pwd -P) || exit "$?"
if [ "$physical_repo" != "$repo" ] || [ "$physical_parent" != "$parent" ]; then
    printf '%s\n' 'repository/output ancestors must not contain symbolic links' >&2
    exit 2
fi
case "$output" in "$repo"|"$repo"/*) printf '%s\n' 'output must be outside repository' >&2; exit 2 ;; esac
case "$repo" in "$output"/*) printf '%s\n' 'output must not contain repository' >&2; exit 2 ;; esac
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
