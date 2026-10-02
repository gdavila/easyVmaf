#!/bin/bash

main() {
    local link_name="easyVmafPlus"
    local paths=("/usr/local/bin" "/usr/bin" "$HOME/bin" "$HOME/.local/bin")
    local system_path
    local found_path=""
    local response

    # Also check the PATH directories install_symlink.sh falls back to
    IFS=: read -ra system_path <<< "$PATH"
    for path in "${system_path[@]}"; do
        if [[ "$path" != "/bin" && "$path" != "/sbin" && "$path" != "/usr/bin" && "$path" != "/usr/sbin" ]]; then
            paths+=("$path")
        fi
    done

    # Check each path for the existence of the symlink and prompt for its removal
    for path in "${paths[@]}"; do
        if [[ -L "$path/$link_name" ]]; then
            printf "Found symlink in '%s'. Do you want to remove it? [y/N]: " "$path"
            read -r response
            if [[ "$response" =~ ^[Yy] ]]; then
                if rm "$path/$link_name"; then
                    printf "Symlink removed successfully from '%s'.\n" "$path"
                    return 0
                else
                    printf "Failed to remove the symlink from '%s'.\n" "$path" >&2
                    return 1
                fi
            fi
            found_path="$path"
            break
        fi
    done

    if [[ -z "$found_path" ]]; then
        printf "No symlink named '%s' found in the checked directories.\n" "$link_name"
        return 1
    fi
}

main "$@"
