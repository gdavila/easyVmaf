#!/bin/bash

main() {
    local target_script="$PWD/easyVmafPlus.sh"
    local link_name="easyVmafPlus"
    local link_path

    # Function to find a suitable bin directory
    find_bin_directory() {
        # Preferred paths list
        local paths=("/usr/local/bin" "/usr/bin" "$HOME/bin" "$HOME/.local/bin")

        # Check preferred paths first
        for path in "${paths[@]}"; do
            if [[ -d "$path" && -w "$path" ]]; then
                echo "$path"
                return
            fi
        done

        # Consider PATH directories as a fallback, filtering out system directories typically unwritable by users
        IFS=: read -ra system_path <<< "$PATH"
        for path in "${system_path[@]}"; do
            if [[ -d "$path" && -w "$path" && "$path" != "/bin" && "$path" != "/sbin" && "$path" != "/usr/bin" && "$path" != "/usr/sbin" ]]; then
                echo "$path"
                return
            fi
        done

        # No writable path found
        return 1
    }

    # Locate an appropriate directory to place the symlink
    if ! link_path=$(find_bin_directory); then
        printf "No suitable directory found in your PATH where a symlink can be created. Ensure you have write permissions.\n" >&2
        return 1
    fi

    # Full path for the new symlink
    local full_link_path="$link_path/$link_name"

    # Check if the symlink or a file with the same name already exists
    if [[ -e "$full_link_path" ]]; then
        printf "A file or symlink named '%s' already exists in '%s'.\n" "$link_name" "$link_path" >&2
        return 1
    fi

    # Create the symlink
    if ln -s "$target_script" "$full_link_path"; then
        printf "Symlink created successfully in '%s'.\n" "$full_link_path"
    else
        printf "Failed to create a symlink in '%s'.\n" "$link_path" >&2
        return 1
    fi
}

main "$@"
