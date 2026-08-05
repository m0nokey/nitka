#!/usr/bin/env bash

mutate_ssh_proxy_access_keys_and_deploy() {
    local node="$1" action="$2" value="${3:-}" before after rollback_rc=0
    before="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-before.XXXXXX")"
    after="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-after.XXXXXX")"
    if ! read_vault_state "$before"; then
        rm -f "$before" "$after"
        return 1
    fi

    if ! python3 "$ROOT_DIR/scripts/state_cli.py" "$action" "$node" "$value" \
        <"$before" >"$after"; then
        rm -f "$before" "$after"
        return 1
    fi
    if ! run_node_playbook "$node" manage_access.yml "$after" "Updating SSH proxy access" ssh_proxy_access; then
        rm -f "$before" "$after"
        printf '%s\n' "SSH proxy access change failed. The existing Vault was not changed."
        return 1
    fi

    if ! vault_save "$after"; then
        run_node_playbook "$node" manage_access.yml "$before" "Rolling back SSH proxy access" ssh_proxy_access_rollback rollback_update || rollback_rc=1
        rm -f "$before" "$after"
        pipeline_abort
        if ((rollback_rc != 0)); then
            printf '%s\n' "SSH proxy access change failed and automatic rollback also failed."
            printf '%s\n' "Runtime state must be checked before retrying."
            return 2
        fi
        printf '%s\n' "SSH proxy access change was rolled back because the encrypted Vault could not be saved."
        printf '%s\n' "The existing Vault was not changed."
        return 1
    fi

    rm -f "$before" "$after"
    case "$action" in
        add-ssh-keys)
            if ((10#$value == 1)); then
                pipeline_complete "SSH proxy access key added successfully." 1
            else
                pipeline_complete "${value} SSH proxy access keys added successfully." 1
            fi
            ;;
        remove-ssh-key)
            pipeline_complete "SSH proxy access key deleted successfully." 1
            ;;
    esac
    return 0
}

add_ssh_proxy_access_keys_menu() {
    local node="$1" count
    while true; do
        clear_screen
        echo
        menu_heading "Add SSH proxy access keys:"
        echo
        printf '%s\n' "How many SSH proxy access keys do you want to add?"
        printf '%s\n' "Each key gets its own random username and Ed25519 key pair."
        printf '%s\n' "Enter a number from 1 to 50."
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice count '?: ' 'a number from 1 to 50, or b, m, i, x'; then
            continue
        fi
        case "$count" in
            b|B) return 0 ;;
            m|M) MAIN_MENU_REQUESTED=1; return 0 ;;
            i|I) show_info ssh_proxy ;;
            x|X) exit_tui ;;
            ''|*[!0-9]*) invalid_choice ;;
            *)
                if ((10#$count < 1 || 10#$count > 50)); then
                    invalid_choice
                    continue
                fi
                clear_screen
                if ! mutate_ssh_proxy_access_keys_and_deploy "$node" add-ssh-keys "$count"; then
                    show_result_screen "SSH proxy access change failed." \
                        "The existing Vault was not changed."
                fi
                return 0
                ;;
        esac
    done
}

remove_ssh_proxy_access_key_menu() {
    local node="$1" state key_list selection key_id username confirm selected_key_line
    local proxy_address proxy_port
    state="$(mktemp "$RUNTIME_TMP_DIR/.ssh-proxy-keys.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi

    read -r proxy_address proxy_port < <(python3 - "$node" "$state" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
node = state.get("nodes", {}).get(sys.argv[1], {})
transport = node.get("access", {}).get("ssh_proxy", {})
print(node.get("host", ""), transport.get("port", ""))
PY
    )

    while true; do
        clear_screen
        echo
        menu_heading "Delete access key:"
        echo
        key_list="$(python3 - "$node" "$state" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
node = state.get("nodes", {}).get(sys.argv[1])
transport = node.get("access", {}).get("ssh_proxy", {}) if node else {}
keys = transport.get("access_keys", [])
for index, key in enumerate(keys, 1):
    print(f"{index}\t{key.get('key_id', '')}\t{key.get('username', '')}")
PY
        )"
        if [[ -z "$key_list" ]]; then
            printf '%s\n' "No SSH proxy access keys configured."
            echo
            prompt_nav
            case "$REPLY" in
                b|B) rm -f "$state"; return 0 ;;
                m|M) rm -f "$state"; MAIN_MENU_REQUESTED=1; return 0 ;;
                i|I) show_info ssh_proxy ;;
                x|X) rm -f "$state"; exit_tui ;;
                *) invalid_choice ;;
            esac
            continue
        fi

        printf '%s\n' "SSH address: $proxy_address"
        printf '%s\n' "SSH port:    $proxy_port"
        echo
        while IFS=$'\t' read -r selection key_id username; do
            menu_option "$selection" "Username: $username"
        done <<<"$key_list"
        echo
        prompt_nav
        case "$REPLY" in
            b|B) rm -f "$state"; return 0 ;;
            m|M) rm -f "$state"; MAIN_MENU_REQUESTED=1; return 0 ;;
            i|I) show_info ssh_proxy ;;
            x|X) rm -f "$state"; exit_tui ;;
            ''|*[!0-9]*) invalid_choice ;;
            *)
                selected_key_line=''
                while IFS=$'\t' read -r selection key_id username; do
                    if [[ "$selection" == "$REPLY" ]]; then
                        selected_key_line="$selection"$'\t'"$key_id"$'\t'"$username"
                        break
                    fi
                done <<<"$key_list"
                if [[ -z "$selected_key_line" ]]; then
                    invalid_choice
                    continue
                fi
                IFS=$'\t' read -r selection key_id username <<<"$selected_key_line"
                clear_screen
                echo
                menu_heading "Delete access key:"
                echo
                printf '%s\n' "SSH address: $proxy_address"
                printf '%s\n' "SSH port:    $proxy_port"
                echo
                printf '%s\n' "Username: $username"
                echo
                printf '%s\n' "The private key for this user will be revoked."
                printf '%s\n' "The last access key cannot be deleted."
                echo
                printf '%s\n' "Are you sure you want to delete this access key? (y/n)"
                echo
                menu_control b back
                menu_control m main
                menu_control i info
                menu_control x exit
                echo
                read_required_choice confirm '?: ' 'y or n, or b, m, i, x' || continue
                case "$confirm" in
                    y|Y)
                        clear_screen
                        if ! mutate_ssh_proxy_access_keys_and_deploy "$node" remove-ssh-key "$key_id"; then
                            show_result_screen "SSH proxy access change failed." \
                                "The existing Vault was not changed."
                        fi
                        rm -f "$state"
                        return 0
                        ;;
                    n|N|b|B) ;;
                    m|M) rm -f "$state"; MAIN_MENU_REQUESTED=1; return 0 ;;
                    i|I) show_info ssh_proxy ;;
                    x|X) rm -f "$state"; exit_tui ;;
                    *) invalid_choice ;;
                esac
                ;;
        esac
    done
}

manage_ssh_proxy() {
    local node="$1"
    while true; do
        clear_screen
        echo
        menu_heading "Manage SSH proxy:"
        echo
        menu_option 1 Show
        menu_option 2 Add
        menu_option 3 Delete
        echo
        if ! prompt_nav; then
            continue
        fi
        case "$REPLY" in
            1) show_saved_ssh_proxy_details "$node" || true ;;
            2) add_ssh_proxy_access_keys_menu "$node" || true ;;
            3) remove_ssh_proxy_access_key_menu "$node" || true ;;
            b|B) return 0 ;;
            m|M) MAIN_MENU_REQUESTED=1; return 0 ;;
            i|I) show_info ssh_proxy ;;
            x|X) exit_tui ;;
            *) invalid_choice ;;
        esac
        [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return 0
    done
}
