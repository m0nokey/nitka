#!/usr/bin/env bash

local_internet_available() {
    local url
    command -v curl >/dev/null 2>&1 || return 1
    for url in \
        https://www.gstatic.com/generate_204 \
        https://deb.debian.org/ \
        https://github.com/; do
        if curl -fsSL --connect-timeout 3 --max-time 6 -o /dev/null "$url" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

show_node_status() {
    local node="$1" state
    state="$(mktemp "$RUNTIME_TMP_DIR/.node.XXXXXX")"
    if read_vault_state "$state"; then
        python3 "$ROOT_DIR/scripts/render_nodes.py" --check --node "$node" <"$state"
    fi
    rm -f "$state"
}

show_cascade() {
    local deployment_id="$1" state
    state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-screen.XXXXXX")"
    if read_vault_state "$state"; then
        python3 "$ROOT_DIR/scripts/render_cascade.py" --check "$deployment_id" <"$state"
    fi
    rm -f "$state"
}

cascade_node() {
    local deployment_id="$1" role="$2" state
    state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-node.XXXXXX")"
    if read_vault_state "$state"; then
        python3 - "$deployment_id" "$role" "$state" <<'PY'
import json
import sys

state = json.load(open(sys.argv[3], encoding="utf-8"))
deployment = state["deployments"][sys.argv[1]]
print(deployment["roles"][sys.argv[2]]["node"])
PY
    fi
    rm -f "$state"
}

select_cascade_node() {
    local deployment_id="$1" choice
    while true; do
        clear_screen
        menu_heading "Select node:"
        echo
        menu_option 1 ingress
        menu_option 2 egress
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1, 2, or b, m, i, x'; then continue; fi
        case "$choice" in
            1) CASCADE_SELECTED_NODE="$(cascade_node "$deployment_id" ingress)"; return 0 ;;
            2) CASCADE_SELECTED_NODE="$(cascade_node "$deployment_id" egress)"; return 0 ;;
            b) return 1 ;;
            m) MAIN_MENU_REQUESTED=1; return 1 ;;
            i) show_info status ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

prepare_cascade_transport_state() {
    local deployment_id="$1" state_file="$2" normalized
    normalized="$(mktemp "$RUNTIME_TMP_DIR/.cascade-transport-state.XXXXXX")"
    if python3 "$ROOT_DIR/scripts/state_cli.py" \
        normalize-cascade-transport "$deployment_id" \
        <"$state_file" >"$normalized"; then
        mv -f "$normalized" "$state_file"
        return 0
    fi
    rm -f "$normalized"
    return 1
}

import_cascade_routing() {
    local deployment_id="$1" ingress_node before after routing_file default_routing_file
    before="$(mktemp)"
    after="$(mktemp)"
    if ! read_vault_state "$before"; then
        rm -f "$before" "$after"
        return 1
    fi
    ingress_node="$(cascade_node "$deployment_id" ingress)"
    default_routing_file="${NITKA_ROUTING_FILE:-$ROOT_DIR/.local/routing/rules.yml}"
    printf '%s\n' "Path to the routing/rules.yml file:"
    printf 'Default: %s\n' "$default_routing_file"
    if ! read -r -e -p 'Path: ' routing_file; then
        rm -f "$before" "$after"
        return 1
    fi
    if [[ -z "$routing_file" ]]; then
        routing_file="$default_routing_file"
    fi
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" import-routing \
        "$ingress_node" "$routing_file" <"$before" >"$after"; then
        rm -f "$before" "$after"
        show_result_screen "Routing import failed. The existing Vault was not changed."
        return 1
    fi
    if ! prepare_cascade_transport_state "$deployment_id" "$after"; then
        rm -f "$before" "$after"
        show_result_screen "Routing import failed. The existing Vault was not changed."
        return 1
    fi
    if ! run_cascade_ingress_playbook "$deployment_id" "$after" "Updating Cascade routing policy" routing; then
        rm -f "$before" "$after"
        show_result_screen "Routing deployment failed. The existing Vault was not changed."
        return 1
    fi
    if ! vault_save "$after"; then
        rm -f "$before" "$after"
        show_result_screen "The VPN was updated, but the encrypted Vault could not be saved."
        return 1
    fi
    rm -f "$before" "$after"
    pipeline_complete "Routing policy imported and deployed successfully." 1
    return 0
}

manage_cascade_routing() {
    local deployment_id="$1" choice
    while true; do
        clear_screen
        menu_heading "Manage routing rules:"
        echo
        menu_option 1 "Import routing policy"
        printf '%s\n' "The imported policy is stored in the encrypted Vault."
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1, b, m, i, or x'; then continue; fi
        case "$choice" in
            1) clear_screen; import_cascade_routing "$deployment_id" || true ;;
            b) return 0 ;;
            m) MAIN_MENU_REQUESTED=1; return 0 ;;
            i) show_info status ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

restart_cascade() {
    local deployment_id="$1" ingress_node egress_node state
    ingress_node="$(cascade_node "$deployment_id" ingress)"
    egress_node="$(cascade_node "$deployment_id" egress)"
    state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-restart.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    rm -f "$state"
    if run_node_playbook "$egress_node" restart.yml "" "Restarting Cascade egress" cascade_restart cascade-egress \
        && run_node_playbook "$ingress_node" restart.yml "" "Restarting Cascade ingress" cascade_restart cascade-ingress; then
        pipeline_complete "Cascade servers restarted successfully." 1
        return 0
    fi
    return 1
}

update_cascade() {
    local deployment_id="$1" state
    state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-update.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    pipeline_start "Updating Cascade" update
    if run_cascade_playbooks "$deployment_id" "$state" \
        && vault_save "$state"; then
        rm -f "$state"
        pipeline_complete "Cascade updated successfully." 1
        return 0
    fi
    rm -f "$state"
    pipeline_abort
    return 1
}

remove_cascade() {
    local deployment_id="$1" confirm ingress_node egress_node
    while true; do
        clear_screen
        menu_heading "Delete VPN server:"
        echo
        printf '%s\n' "This deletes the Cascade and both VPS nodes. (y/n)"
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice confirm '?: ' 'y or n, or b, m, i, x'; then continue; fi
        case "$confirm" in
            [Yy]) break ;;
            [Nn]|b) return 0 ;;
            m) MAIN_MENU_REQUESTED=1; return 0 ;;
            i) show_info removal ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
    ingress_node="$(cascade_node "$deployment_id" ingress)"
    egress_node="$(cascade_node "$deployment_id" egress)"
    clear_screen
    menu_heading "Deleting VPN server:"
    echo
    printf '%s\n' "Deleting the Cascade egress and ingress VPS nodes."
    echo
    if ! remove_remote_node "$egress_node" || ! remove_remote_node "$ingress_node"; then
        show_result_screen "Cascade deletion did not complete. The Vault was not changed."
        return 0
    fi
    if state_mutate remove-cascade "$deployment_id"; then
        pipeline_complete "Cascade and both VPS nodes were deleted." 1
        return "$NODE_REMOVED_STATUS"
    fi
    show_result_screen "The VPS nodes were cleaned, but the encrypted Vault could not be updated."
    return 0
}

manage_cascade_server() {
    local deployment_id="$1" removal_status
    while true; do
        clear_screen
        echo
        show_cascade "$deployment_id"
        echo
        menu_option 1 "Check VPN status"
        menu_option 2 "Open SSH session"
        menu_option 3 "Restart VPN server"
        menu_option 4 "Block ads and threats"
        menu_option 5 "Block countries"
        menu_option 6 "Rotate SSH key"
        menu_option 7 "Manage routing rules"
        menu_option 8 "Update Cascade"
        menu_option 9 "Delete VPN server"
        echo
        if ! prompt_nav; then continue; fi
        case "$REPLY" in
            1) clear_screen; show_cascade "$deployment_id"; pause_result_screen ;;
            2)
                if select_cascade_node "$deployment_id"; then
                    open_node_ssh_session "$CASCADE_SELECTED_NODE" || true
                fi
                ;;
            3)
                clear_screen
                if restart_cascade "$deployment_id"; then :; else pause_result_screen; fi
                ;;
            4)
                manage_cascade_dns_protection "$deployment_id" || true
                ;;
            5)
                manage_cascade_country_policy "$deployment_id" || true
                ;;
            6)
                if select_cascade_node "$deployment_id"; then
                    clear_screen
                    rotate_ssh_key "$CASCADE_SELECTED_NODE" || true
                fi
                ;;
            7) manage_cascade_routing "$deployment_id" ;;
            8)
                clear_screen
                if update_cascade "$deployment_id"; then :; else pause_result_screen; fi
                ;;
            9)
                remove_cascade "$deployment_id" || removal_status=$?
                removal_status="${removal_status:-0}"
                if ((removal_status == NODE_REMOVED_STATUS)); then
                    return "$NODE_REMOVED_STATUS"
                fi
                return
                ;;
            i) show_info server ;;
            b) return ;;
            m) MAIN_MENU_REQUESTED=1; return ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
        [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return
    done
}

manage_cascade() {
    local deployment_id="$1" ingress_node
    while true; do
        clear_screen
        echo
        show_cascade "$deployment_id"
        echo
        menu_option 1 "Manage VPN server"
        menu_option 2 "Manage access keys"
        echo
        if ! prompt_nav; then continue; fi
        case "$REPLY" in
            1) manage_cascade_server "$deployment_id" ;;
            2)
                ingress_node="$(cascade_node "$deployment_id" ingress)"
                manage_keys "$ingress_node"
                ;;
            i) show_info status ;;
            b) return ;;
            m) MAIN_MENU_REQUESTED=1; return ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
        [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return
    done
}

open_node_ssh_session() {
    local node="$1" state host user port private_key key_file known_hosts_file ssh_status
    state="$(mktemp "$RUNTIME_TMP_DIR/.ssh-session.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$state")"
    user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management_user"])' "$node" <"$state")"
    port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management_port"])' "$node" <"$state")"
    private_key="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management_private_key"], end="")' "$node" <"$state")"
    known_hosts_file="$(mktemp /tmp/xray-known-hosts.XXXXXX)"
    if ! write_node_known_hosts "$state" "$node" "$known_hosts_file"; then
        rm -f "$state" "$known_hosts_file"
        clear_screen
        printf '%s\n' "The SSH host key is not pinned for this VPN server. Redeploy it before opening an SSH session."
        wait_action_return
        return 1
    fi
    rm -f "$state"

    if [[ -z "$host" || -z "$user" || -z "$port" || -z "$private_key" ]]; then
        rm -f "$known_hosts_file"
        clear_screen
        printf '%s\n' "Saved SSH management credentials are incomplete for this VPN server."
        wait_action_return
        return 1
    fi

    key_file="$(mktemp /tmp/xray-ssh-session.XXXXXX)"
    chmod 600 "$key_file"
    printf '%s\n' "$private_key" >"$key_file"
    unset private_key

    clear_screen
    printf '%s\n' "Opening SSH session to ${user}@${host}:${port}."
    printf '%s\n' "Exit the remote shell to return to Nitka."
    echo
    if ssh -tt -i "$key_file" -p "$port" \
        -o IdentitiesOnly=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$known_hosts_file" \
        -o ConnectTimeout=8 \
        -o ConnectionAttempts=1 \
        "$user@$host"; then
        ssh_status=0
    else
        ssh_status=$?
    fi
    rm -f "$key_file" "$known_hosts_file"
    echo
    if ((ssh_status != 0)); then
        printf '%s\n' "SSH session ended with exit code ${ssh_status}."
    else
        printf '%s\n' "SSH session closed."
    fi
    wait_action_return
    return 0
}
manage_node() {
    local node="$1" node_status
    while true; do
        clear_screen
        echo
        show_node_status "$node"
        echo
        menu_option 1 "Manage VPN server"
        menu_option 2 "Manage access keys"
        echo
        if ! prompt_nav; then continue; fi
        case "$REPLY" in
            1)
                if manage_server "$node"; then
                    :
                else
                    node_status=$?
                    if ((node_status == NODE_REMOVED_STATUS)); then
                        return "$NODE_REMOVED_STATUS"
                    fi
                fi
                [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return
                ;;
            2) manage_keys "$node"; [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return ;;
            i) show_info status ;;
            b) return ;;
            m) MAIN_MENU_REQUESTED=1; return ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

manage_server() {
    local node="$1" removal_status
    while true; do
        clear_screen
        echo
        menu_heading "Manage VPN server:"
        echo
        menu_option 1 "Check VPN status"
        menu_option 2 "Open SSH session"
        menu_option 3 "Restart VPN server"
        menu_option 4 "Block ads and threats"
        menu_option 5 "Block countries"
        menu_option 6 "Rotate SSH key"
        menu_option 7 "Delete VPN server"
        echo
        if ! prompt_nav; then continue; fi
        case "$REPLY" in
            1) clear_screen; show_node_status "$node" || true; pause_result_screen ;;
            2) open_node_ssh_session "$node" || true ;;
            3)
                clear_screen
                if run_node_playbook "$node" restart.yml "" "Restarting VPN service" restart; then
                    pipeline_complete "VPN server restarted successfully." 1
                else
                    pause_result_screen
                fi
                ;;
            4) manage_dns_protection "$node" || true; [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return ;;
            5) manage_local_region_policy "$node" || true; [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return ;;
            6) clear_screen; rotate_ssh_key "$node" || true ;;
            7)
                clear_screen
                if remove_node "$node"; then
                    return
                else
                    removal_status=$?
                fi
                if ((removal_status == NODE_REMOVED_STATUS)); then
                    return "$NODE_REMOVED_STATUS"
                fi
                return
                ;;
            i) show_info server ;;
            b) return ;;
            m) MAIN_MENU_REQUESTED=1; return ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

remove_remote_node() {
    local node="$1"
    # Removal remains independent of the pinned management host key.
    if run_remove_with_management_key "$node"; then
        # The deploy user cannot remove itself while it is the Ansible user.
        # The first pass restores the original SSH access; the second pass
        # removes the deploy account and the remaining Nitka state as root.
        run_remove_with_bootstrap "$node"
        return $?
    fi
    run_remove_with_bootstrap "$node"
}

remove_node() {
    local node="$1" confirm local_confirm removed=0 host management_port bootstrap_port state
    clear_screen
    while true; do
        clear_screen
        echo
        menu_heading "Delete VPN server:"
        echo
        printf '%s\n' "Are you sure you want to delete this VPN server? (y/n)"
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice confirm '?: ' 'y or n, or b, m, i, x'; then continue; fi
        case "$confirm" in
            [Yy]) break ;;
            [Nn]|b) return 0 ;;
            m) MAIN_MENU_REQUESTED=1; return 0 ;;
            i) show_info removal ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done

    state="$(mktemp)"
    if read_vault_state "$state"; then
        host="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["host"])' "$node" <"$state")"
        management_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management_port"])' "$node" <"$state")"
        bootstrap_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap_ssh_port"])' "$node" <"$state")"
    fi
    rm -f "$state"

    clear_screen
    menu_heading "Deleting VPN server:"
    echo
    printf '%s\n' "VPS address: ${host:-unknown}"
    printf '%s\n' "Deleting the remote VPN server."
    echo
    if remove_remote_node "$node"; then
        if state_mutate remove-node "$node"; then
            pipeline_complete "VPN server deleted from VPS and Vault." 1
            return "$NODE_REMOVED_STATUS"
        fi
        pipeline_abort
        show_result_screen "The VPS was cleaned, but the local Vault could not be updated."
        return 0
    fi

    while true; do
        clear_screen
        menu_heading "VPN server was not deleted:"
        echo
        printf '%s\n' "VPS address: ${host:-unknown}"
        printf '%s\n' "Management SSH port: ${management_port:-unknown}"
        printf '%s\n' "Initial SSH port: ${bootstrap_port:-22}"
        echo
        printf '%s\n' "Remote deletion did not complete."
        printf '%s\n' "The VPS may be unreachable or its SSH service may be unavailable."
        printf '%s\n' "The VPN server is still saved in the local Vault."
        echo
        menu_option 1 "Try delete again"
        menu_option 2 "Delete from local Vault only"
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice local_confirm '?: ' '1 or 2, or b, m, i, x'; then continue; fi
        case "$local_confirm" in
            2)
                if state_mutate remove-node "$node"; then
                    show_result_screen "VPN server deleted from the local Vault."
                    removed=1
                fi
                break
                ;;
            1)
                if remove_remote_node "$node"; then
                    if state_mutate remove-node "$node"; then
                        pipeline_complete "VPN server deleted from VPS and Vault." 1
                        removed=1
                    else
                        pipeline_abort
                    fi
                    break
                fi
                ;;
            i) show_info removal ;;
            b) break ;;
            m) MAIN_MENU_REQUESTED=1; break ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
    if ((removed)); then
        return "$NODE_REMOVED_STATUS"
    fi
    return 0
}
vpn_servers() {
    local items item_count selection kind name node state node_status
    while true; do
        clear_screen
        state="$(mktemp "$RUNTIME_TMP_DIR/.servers.XXXXXX")"
        if ! read_vault_state "$state"; then
            rm -f "$state"
            return 1
        fi
        if ! items="$(python3 "$ROOT_DIR/scripts/render_fleet.py" --items <"$state")"; then
            rm -f "$state"
            return 1
        fi
        item_count="$(printf '%s\n' "$items" | sed '/^$/d' | wc -l | tr -d ' ')"
        if [[ "$item_count" == 0 ]]; then
            echo
            menu_heading "VPN servers:"
            echo
            printf '%s\n' "  No VPN servers configured."
            echo
            menu_option 1 "Add VPN server"
            echo
            if ! prompt_nav; then continue; fi
            case "$REPLY" in
                1) rm -f "$state"; add_vpn_server_menu || true; return ;;
                i) show_info add-node; rm -f "$state"; continue ;;
                b) rm -f "$state"; return ;;
                m) rm -f "$state"; MAIN_MENU_REQUESTED=1; return ;;
                x) rm -f "$state"; exit_tui ;;
                *) invalid_choice; rm -f "$state"; continue ;;
            esac
        fi
        python3 "$ROOT_DIR/scripts/render_fleet.py" --check <"$state"
        echo
        if ! prompt_nav "1-$item_count"; then continue; fi
        case "$REPLY" in
            i) show_info status; rm -f "$state"; continue ;;
            b) rm -f "$state"; return ;;
            m) rm -f "$state"; MAIN_MENU_REQUESTED=1; return ;;
            x) rm -f "$state"; exit_tui ;;
            *[!0-9]*) invalid_choice; rm -f "$state"; continue ;;
            *)
                selection="$(printf '%s\n' "$items" | sed -n "${REPLY}p")"
                rm -f "$state"
                IFS=$'\t' read -r kind name <<<"$selection"
                if [[ -n "$name" && "$kind" == cascade ]]; then
                    manage_cascade "$name" || true
                    return
                elif [[ -n "$name" ]]; then
                    node_status=0
                    if manage_node "$name"; then
                        :
                    else
                        node_status=$?
                    fi
                    if ((node_status == NODE_REMOVED_STATUS)); then
                        continue
                    fi
                    return
                fi
                invalid_choice
                ;;
        esac
    done
}
