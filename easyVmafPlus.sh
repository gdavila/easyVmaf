#!/bin/bash

main() {
    local python_cmd
    local script_dir

    # Check for Python3 installation and retrieve the command path
    if ! python_cmd=$(command -v python3 || command -v python); then
        printf "Python is not installed on this system.\n" >&2
        return 1
    fi

    # Determine the directory where the Python script is located
    script_dir=$(dirname "$(readlink -f "$0")")

    # Execute the Python script from the caller's directory, forwarding all arguments
    "$python_cmd" "$script_dir/easyVmafPlus.py" "$@"
}

main "$@"
