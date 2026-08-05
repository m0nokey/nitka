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

cascade_transport_summary() {
    local deployment_id="$1" state status
    state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-transport-summary.XXXXXX")"
    if ! read_vault_state "$state"; then
        rm -f "$state"
        return 1
    fi
    python3 "$ROOT_DIR/scripts/state_cli.py" transport-summary "$deployment_id" <"$state"
    status=$?
    rm -f "$state"
    return "$status"
}

cascade_transport_label() {
    case "$1" in
        xray-reality) printf '%s\n' "Xray REALITY" ;;
        ssh-tun) printf '%s\n' "SSH TUN" ;;
        naiveproxy) printf '%s\n' "NaiveProxy" ;;
        hysteria2) printf '%s\n' "Hysteria2" ;;
        wireguard) printf '%s\n' "WireGuard" ;;
        *) printf '%s\n' "${1:-unknown}" ;;
    esac
}

cascade_transport_name() {
    local deployment_id="$1" plane="$2" summary
    summary="$(cascade_transport_summary "$deployment_id")" || return 1
    python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]]["transport"])' \
        "$plane" <<<"$summary"
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
            i)
                if [[ "$access_transport" == ssh-proxy ]]; then
                    show_info status ssh_proxy
                else
                    show_info status
                fi
                ;;
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

replace_cascade_vps() {
    local deployment_id="$1" role="$2"
    local before egress_state shared_state after captured final
    local ingress_node old_node new_node old_host new_host old_country new_country
    local choice rollback_status=0 role_label server_name port_mode dns_profile dns_lists
    local vision_port xhttp_port
    local -a port_args=()
    before="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-before.XXXXXX")"
    egress_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-egress.XXXXXX")"
    shared_state="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-shared.XXXXXX")"
    after="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-after.XXXXXX")"
    captured="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-captured.XXXXXX")"
    final="$(mktemp "$RUNTIME_TMP_DIR/.cascade-replace-final.XXXXXX")"

    if ! read_vault_state "$before"; then
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        return 1
    fi
    ingress_node="$(python3 - "$deployment_id" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[2], encoding="utf-8"))
print(state["deployments"][sys.argv[1]]["roles"]["ingress"]["node"])
PY
)"
    old_node="$(python3 - "$deployment_id" "$role" "$before" <<'PY'
import json
import sys

state = json.load(open(sys.argv[3], encoding="utf-8"))
print(state["deployments"][sys.argv[1]]["roles"][sys.argv[2]]["node"])
PY
)"
    old_host="$(python3 - "$old_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]]["host"])
PY
)"
    old_country="$(python3 - "$old_node" "$before" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]].get("country", "N/A"))
PY
)"

    if ! collect_cascade_node_access "$role"; then
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        return 0
    fi
    new_host="$CASCADE_NODE_HOST"
    if [[ -n "$(find_node_by_connection "$before" "$new_host" "$CASCADE_NODE_PORT")" ]]; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        show_result_screen "The replacement VPS already exists in the Vault. Choose a different VPS."
        return 0
    fi
    if [[ "$role" == ingress ]]; then
        if ! add_node_domain_prompt; then
            unset CASCADE_NODE_PASSWORD
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            return 0
        fi
        server_name="$ADD_NODE_SERVER_NAME"
        unset ADD_NODE_SERVER_NAME
        if ! add_node_port_mode_prompt; then
            unset CASCADE_NODE_PASSWORD
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            return 0
        fi
        port_mode="$ADD_NODE_PORT_MODE"
        vision_port="${ADD_NODE_VISION_PORT:-}"
        xhttp_port="${ADD_NODE_XHTTP_PORT:-}"
        unset ADD_NODE_PORT_MODE ADD_NODE_VISION_PORT ADD_NODE_XHTTP_PORT
        if [[ "$port_mode" == manual ]]; then
            port_args=(--vision-port "$vision_port" --xhttp-port "$xhttp_port")
        fi
        unset DNS_FILTER_CURRENT_PROFILE DNS_FILTER_LISTS
        if ! select_dns_profile initial; then
            unset CASCADE_NODE_PASSWORD
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            return 0
        fi
        dns_profile="$DNS_FILTER_PROFILE"
        dns_lists="${DNS_FILTER_LISTS:-}"
        role_label="ingress"
    else
        server_name="github.com"
        port_mode="random"
        dns_profile="disabled"
        dns_lists=""
        role_label="egress"
    fi
    if ! NITKA_BOOTSTRAP_USER="$CASCADE_NODE_USER" \
        NITKA_BOOTSTRAP_PASSWORD="$CASCADE_NODE_PASSWORD" \
        NITKA_BOOTSTRAP_PORT="$CASCADE_NODE_PORT" \
        python3 "$ROOT_DIR/scripts/state_cli.py" \
        --node-role "$role" --server-name "$server_name" --port-mode "$port_mode" \
        "${port_args[@]}" --dns-profile "$dns_profile" --dns-lists "$dns_lists" \
        add-node auto "$new_host" <"$before" >"$egress_state"; then
        unset CASCADE_NODE_PASSWORD
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        return 1
    fi
    new_node="$(cascade_new_node_name "$before" "$egress_state")"
    new_country="$(python3 - "$new_node" "$egress_state" <<'PY'
import json
import sys

print(json.load(open(sys.argv[2], encoding="utf-8"))["nodes"][sys.argv[1]].get("country", "N/A"))
PY
    )"
    unset CASCADE_NODE_PASSWORD
    if [[ "$role" == egress ]]; then
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
            share-management-key "$ingress_node" "$new_node" \
            <"$egress_state" >"$shared_state"; then
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            return 1
        fi
    else
        cp "$egress_state" "$shared_state"
    fi
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        replace-cascade-node "$deployment_id" "$role" "$new_node" \
        <"$shared_state" >"$after"; then
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        return 1
    fi

    while true; do
        clear_screen
        menu_heading "Replace Cascade $role_label VPS"
        echo
        printf '%s\n' "Current $role_label:"
        printf '%s\n' "  IP address: $old_host"
        printf '%s\n' "  Country:    $old_country"
        echo
        printf '%s\n' "New $role_label:"
        printf '%s\n' "  IP address: $new_host"
        printf '%s\n' "  Country:    $new_country"
        echo
        if [[ "$role" == ingress ]]; then
            printf '%s\n' "The egress VPS will remain unchanged."
            printf '%s\n' "Client access keys and routing policy will remain unchanged."
        else
            printf '%s\n' "The ingress VPS and client access keys will remain unchanged."
        fi
        printf '%s\n' "The old $role_label remains in the Vault until the replacement is verified."
        echo
        menu_option 1 Continue
        menu_option 2 Cancel
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1 or 2, or b, m, i, x'; then continue; fi
        case "$choice" in
            1) break ;;
            2|b) rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"; return 0 ;;
            m) MAIN_MENU_REQUESTED=1; rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"; return 0 ;;
            i) show_info status ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done

    pipeline_start "Replacing Cascade $role_label VPS" install
    if ! deploy_node "$new_node" "$after" "" 1 bootstrap-only "cascade-$role"; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        show_result_screen "New $role_label bootstrap failed. The existing Cascade and Vault were not changed."
        return 1
    fi
    if [[ "$role" == egress ]]; then
        if ! run_cascade_playbooks "$deployment_id" "$after" egress-only; then
            pipeline_abort
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            show_result_screen "New egress deployment failed. The existing Cascade and Vault were not changed."
            return 1
        fi
        if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
            capture-cascade-transport-keys "$deployment_id" \
            "$STATE_DIR/cascade/$deployment_id/ssh" \
            <"$after" >"$captured"; then
            pipeline_abort
            rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
            show_result_screen "New egress transport keys could not be captured. The existing Vault was not changed."
            return 1
        fi
    else
        cp "$after" "$captured"
    fi
    if ! run_cascade_playbooks "$deployment_id" "$captured" ingress-only; then
        if run_cascade_playbooks "$deployment_id" "$before" ingress-only; then
            rollback_status=1
        fi
        pipeline_abort
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        if ((rollback_status)); then
            show_result_screen "Ingress verification failed. The previous Cascade configuration was restored." \
                "The existing Vault was not changed."
        else
            show_result_screen "Ingress verification failed and automatic rollback was unsuccessful." \
                "The existing Vault was not changed. Check the ingress VPS."
        fi
        return 1
    fi
    if ! python3 "$ROOT_DIR/scripts/state_cli.py" \
        remove-node "$old_node" <"$captured" >"$final"; then
        pipeline_abort
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        show_result_screen "The replacement was verified, but the old $role_label could not be removed from the new Vault state."
        return 1
    fi
    if ! vault_save "$final"; then
        if run_cascade_playbooks "$deployment_id" "$before" ingress-only; then
            rollback_status=1
        fi
        pipeline_abort
        rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
        if ((rollback_status)); then
            show_result_screen "Vault save failed. The previous Cascade configuration was restored." \
                "The old $role_label remains in the Vault."
        else
            show_result_screen "Vault save failed and automatic rollback was unsuccessful." \
                "The previous Cascade state remains recorded locally; check the ingress VPS."
        fi
        return 1
    fi
    rm -f "$before" "$egress_state" "$shared_state" "$after" "$captured" "$final"
    pipeline_complete "Cascade $role_label VPS replaced successfully." 1
    return 0
}

replace_cascade_node_menu() {
    local deployment_id="$1" choice
    local backhaul_transport backhaul_label access_transport access_label
    while true; do
        backhaul_transport="$(cascade_transport_name "$deployment_id" backhaul 2>/dev/null || printf '%s' unknown)"
        backhaul_label="$(cascade_transport_label "$backhaul_transport")"
        access_transport="$(cascade_transport_name "$deployment_id" access 2>/dev/null || printf '%s' unknown)"
        access_label="$(cascade_transport_label "$access_transport")"
        clear_screen
        menu_heading "Replace Cascade VPS"
        echo
        printf '%s\n' "Choose the VPS role to replace."
        printf '%s\n' "Client access transport: $access_label."
        printf '%s\n' "Ingress-to-egress transport: $backhaul_label."
        printf '%s\n' "Replacing a VPS changes its IP but keeps both selected transports."
        printf '%s\n' "If DPI blocks a transport itself, a separate transport migration is required."
        echo
        menu_option 1 "Replace ingress VPS"
        menu_option 2 "Replace egress VPS"
        echo
        menu_control b back
        menu_control m main
        menu_control i info
        menu_control x exit
        echo
        if ! read_required_choice choice '?: ' '1 or 2, or b, m, i, x'; then continue; fi
        case "$choice" in
            1) replace_cascade_vps "$deployment_id" ingress || true; return ;;
            2) replace_cascade_vps "$deployment_id" egress || true; return ;;
            b) return ;;
            m) MAIN_MENU_REQUESTED=1; return ;;
            i) show_info cascade-server ;;
            x) exit_tui ;;
            *) invalid_choice ;;
        esac
    done
}

remove_cascade() {
    local deployment_id="$1" confirm ingress_node egress_node state_file cascade_extra
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
    state_file="$(mktemp "$RUNTIME_TMP_DIR/.cascade-remove-state.XXXXXX")"
    cascade_extra="$(mktemp "$RUNTIME_TMP_DIR/.cascade-remove-extra.XXXXXX")"
    if ! read_vault_state "$state_file" || \
        ! python3 "$ROOT_DIR/scripts/state_cli.py" extract-cascade "$deployment_id" \
            <"$state_file" >"$cascade_extra"; then
        rm -f "$state_file" "$cascade_extra"
        show_result_screen "Cascade deletion could not read its deployment state."
        return 0
    fi
    clear_screen
    menu_heading "Deleting VPN server:"
    echo
    printf '%s\n' "Deleting the Cascade egress and ingress VPS nodes."
    echo
    if ! remove_remote_node "$egress_node" "$cascade_extra" egress || \
        ! remove_remote_node "$ingress_node" "$cascade_extra" ingress; then
        rm -f "$state_file" "$cascade_extra"
        show_result_screen "Cascade deletion did not complete. The Vault was not changed."
        return 0
    fi
    rm -f "$state_file" "$cascade_extra"
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
        menu_option 9 "Replace VPS node"
        menu_option 10 "Delete VPN server"
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
                replace_cascade_node_menu "$deployment_id"
                ;;
            10)
                remove_cascade "$deployment_id" || removal_status=$?
                removal_status="${removal_status:-0}"
                if ((removal_status == NODE_REMOVED_STATUS)); then
                    return "$NODE_REMOVED_STATUS"
                fi
                return
                ;;
            i) show_info cascade-server ;;
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
            i) show_info cascade-server ;;
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
    user="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["user"])' "$node" <"$state")"
    port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["port"])' "$node" <"$state")"
    private_key="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["private_key"], end="")' "$node" <"$state")"
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
    local node="$1" node_status state access_transport
    while true; do
        clear_screen
        echo
        show_node_status "$node"
        echo
        state="$(mktemp "$RUNTIME_TMP_DIR/.node-menu.XXXXXX")"
        if ! read_vault_state "$state"; then
            rm -f "$state"
            return 1
        fi
        access_transport="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["access"].get("transport", "xray-reality"), end="")' "$node" <"$state")"
        rm -f "$state"
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
            2)
                if [[ "$access_transport" == ssh-proxy ]]; then
                    manage_ssh_proxy "$node" || true
                else
                    manage_keys "$node"
                fi
                [[ "$MAIN_MENU_REQUESTED" == 1 ]] && return
                ;;
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
    local node="$1" extra_source="${2:-}" topology_role="${3:-}"
    # Removal remains independent of the pinned management host key.
    if run_remove_with_management_key "$node" "$extra_source" "$topology_role"; then
        # The deploy user cannot remove itself while it is the Ansible user.
        # The first pass restores the original SSH access; the second pass
        # removes the deploy account and the remaining Nitka state as root.
        run_remove_with_bootstrap "$node" "$extra_source" "$topology_role"
        return $?
    fi
    run_remove_with_bootstrap "$node" "$extra_source" "$topology_role"
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
        management_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["management"]["port"])' "$node" <"$state")"
        bootstrap_port="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][sys.argv[1]]["bootstrap"]["port"])' "$node" <"$state")"
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
