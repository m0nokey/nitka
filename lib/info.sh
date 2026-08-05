#!/usr/bin/env bash

show_dns_profile_matrix() {
    local -a standard_rows=(
        $'URLhaus\tON\tON\tON\tON\t~611 entries'
        $'HaGeZi Threat Intelligence Feeds Mini\t-\tON\t-\t-\t160,610 entries'
        $'HaGeZi Encrypted DNS\t-\t-\tON\t-\t3,423 entries'
        $'HaGeZi Encrypted DNS/VPN/Proxy Bypass\t-\t-\t-\tON\t17,591 entries'
        $'AdGuard CNAME Trackers\t-\t-\tON\tON\t~100,087 entries'
        $'AdGuard Mail Trackers\t-\t-\tON\tON\t~98,595 entries'
        $'ThreatFox\t-\t-\tON\tON\t~45,617 entries'
        $'HaGeZi Pro++\t-\t-\tON\t-\t272,267 entries'
        $'HaGeZi Ultimate\t-\t-\t-\tON\t294,364 entries'
        $'HaGeZi Threat Intelligence Feeds Medium\t-\t-\t-\tON\t417,094 entries'
        $'Threat Intelligence IPs\t-\t-\t-\tON\t~54,609 entries'
        $'Dynamic DNS Threats\t-\t-\toptional\tON\t1,524 entries'
        $'Suspicious Spam TLDs\t-\t-\t-\toptional\t~129 entries'
    )
    local -a custom_rows=(
        $'Pop-up Ads\t-\toptional\tincluded\tincluded\t56,598 entries'
        $'Adult Content\t-\toptional\toptional\toptional\t110,004 entries'
        $'Gambling Mini\t-\toptional\toptional\toptional\t94,060 entries'
        $'Gambling Medium\t-\t-\toptional\toptional\t155,276 entries'
        $'Gambling Full\t-\t-\toptional\toptional\t357,251 entries'
        $'Social Networks\t-\t-\toptional\toptional\t898 entries'
        $'SafeSearch\t-\t-\toptional\toptional\t206 entries'
        $'Anti Piracy\t-\t-\toptional\toptional\t36,844 entries'
    )
    printf '%s\n' "PROFILE LIST MATRIX"
    printf '%s\n' ""
    ui_print_table "    " "  " 0 \
        $'List\tMinimal\tOptimal\tFull\tMaximum\tApprox. entries' \
        "${standard_rows[@]}"
    printf '%s\n' ""
    printf '%s\n' "    Custom-only sources"
    ui_print_table "    " "  " 0 \
        $'List\tMinimal\tOptimal\tFull\tMaximum\tApprox. entries' \
        "${custom_rows[@]}"
    printf '%s\n' ""
    printf '%s\n' "    Counts are upstream list values and may change when sources update."
}

show_info() {
    local topic="${1:-general}"
    local context="${2:-selected}"
    local reset="$COLOR_RESET" blue="$COLOR_LINE" gray="$COLOR_MUTED"

    if [[ ! -t 1 ]]; then
        reset=''
        blue=''
        gray=''
    fi

    info_desc() {
        printf '    %b%s%b\n' "$gray" "$1" "$reset"
    }

    clear_screen
    echo
    case "$topic" in
        add-node)
            printf '%b  Add VPN server:%b\n' "$blue" "$reset"
            info_desc "Choose a topology first: a standalone VPN or a two-node Cascade VPN."
            info_desc "For a standalone VPN, choose Xray REALITY or the SSH proxy transport."
            info_desc "Cascade uses Xray REALITY for client access and SSH TUN between its nodes."
            info_desc "SSH proxy is a fast temporary TCP proxy based on OpenSSH."
            info_desc "Native UDP is not supported by OpenSSH."
            info_desc "An optional external UDP relay uses UDP-over-TCP and may be unstable for calls, games, and realtime audio."
            info_desc "Follow the steps shown in the installation pipeline."
            ;;
        status)
            local -a status_rows=(
                $'Active\tXray is running and both VPN ports are reachable.'
                $'Partial\tXray is running and only one VPN port is reachable.'
                $'VPN unavailable\tThe VPS responded, but Xray is not confirmed running.'
                $'Unreachable\tNo VPN or management port responded; DPI or a provider firewall may be involved.'
            )
            printf '%b  Status:%b\n' "$blue" "$reset"
            ui_print_table "    " "  " 0 $'STATUS\tDESCRIPTION' "${status_rows[@]}"
            if [[ "$context" == selected ]]; then
                echo
                printf '%b  Selected server menu:%b\n' "$blue" "$reset"
                printf '%s\n' "    1. Manage VPN server"
                info_desc "       Open server operations, ad and threat blocking, country blocking, or deletion."
                if [[ "$context" == ssh_proxy ]]; then
                    printf '%s\n' "    2. Manage SSH proxy"
                    info_desc "       Show, add, or delete the SSH proxy usernames and keys."
                else
                    printf '%s\n' "    2. Manage access keys"
                    info_desc "       Show, add, or delete the VPN client keys for this server."
                fi
            fi
            ;;
        access_keys)
            printf '%b  Access keys:%b\n' "$blue" "$reset"
            info_desc "Each access key contains one Vision UUID and one XHTTP UUID."
            info_desc "Both UUIDs are deployed together and deleted together."
            info_desc "You can add up to 50 access keys at once."
            info_desc "In the delete screen, enter a key number or the last number to delete all keys."
            echo
            printf '%b  Manage access keys menu:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Show"
            info_desc "       Display the Vision and XHTTP connection links for each key."
            printf '%s\n' "    2. Add"
            info_desc "       Generate 1 to 50 new access keys and deploy them to the VPS."
            printf '%s\n' "    3. Delete"
            info_desc "       Delete one key, or choose the final number to delete all keys."
            echo
            printf '%b  Access-key screens:%b\n' "$blue" "$reset"
            info_desc "Add: enter the number of keys to generate, from 1 to 50."
            info_desc "Delete: select a key number, then confirm with y or cancel with n."
            info_desc "Delete all: confirm that every Vision and XHTTP key should be deleted."
            ;;
        ssh_proxy)
            printf '%b  SSH proxy access:%b\n' "$blue" "$reset"
            info_desc "Each access has its own generated username and Ed25519 private key."
            info_desc "The username has no shell and can only create dynamic TCP forwarding."
            info_desc "All access keys use the same SSH proxy port on this VPS."
            info_desc "The management SSH user and key are separate and are not shown here."
            echo
            printf '%b  SSH proxy capabilities:%b\n' "$blue" "$reset"
            info_desc "TCP proxy: supported."
            info_desc "Native UDP: not supported."
            info_desc "UDP relay: best effort, UDP-over-TCP."
            info_desc "The standard SSH proxy deployment does not include a UDP relay."
            info_desc "UDP-over-TCP may be unstable for calls, games, and realtime audio."
            echo
            printf '%b  Manage SSH proxy menu:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Show"
            info_desc "       Display usernames, private keys, and test commands."
            printf '%s\n' "    2. Add"
            info_desc "       Generate 1 to 50 new usernames and Ed25519 key pairs."
            printf '%s\n' "    3. Delete"
            info_desc "       Revoke one access key and its username from the proxy."
            info_desc "       The last remaining proxy access key cannot be deleted."
            ;;
        dns)
            local -a blocklist_rows=(
                $'Minimal\tmalware and malicious websites.'
                $'Optimal\tmalware, phishing, scams and selected trackers.'
                $'Full\tads, tracking, telemetry and malware.'
                $'Maximum\tbroad threat protection and known DNS bypass services.'
                $'Custom\tchoose extra categories within the VPS resource limit.'
            )
            local -a list_description_rows=(
                $'URLhaus\tmalware delivery and malicious website domains.'
                $'Threat Intelligence Feeds Mini\tmalware, phishing, scams and attacker infrastructure.'
                $'Encrypted DNS\tknown DoH and DoT resolver domains.'
                $'DNS/VPN/Proxy Bypass\tknown DoH, VPN and proxy service domains; not all exit-node IPs.'
                $'CNAME Trackers\ttrackers hidden behind CNAME DNS records.'
                $'Mail Trackers\ttracking pixels and link-tracking domains in emails.'
                $'ThreatFox\tmalware indicators and command-and-control domains.'
                $'Pro++\tads, trackers, telemetry, malware, phishing and scams.'
                $'Ultimate\taggressive privacy and security filtering with higher false positives.'
                $'Threat Intelligence Feeds Medium\ta larger malware, phishing, scam and attacker infrastructure feed.'
                $'Threat Intelligence IPs\tIP-related threat indicators in RPZ form; not an IP firewall.'
                $'Dynamic DNS Threats\tsuspicious dynamic DNS used by malware and phishing.'
                $'Suspicious Spam TLDs\tselected high-abuse TLDs; legitimate sites may be blocked.'
                $'Pop-up Ads\tknown pop-up and aggressive advertising domains.'
                $'Adult Content\tadult and NSFW domains; not a complete parental-control system.'
                $'Gambling Mini\tselected betting, casino and gambling domains.'
                $'Gambling Medium\ta broader betting, casino and gambling domain list.'
                $'Gambling Full\tthe broadest betting, casino and gambling domain list.'
                $'Social Networks\tselected social media domains.'
                $'SafeSearch\thelps enforce safer search endpoints.'
                $'Anti Piracy\ttorrent, warez and known piracy domains.'
                $'Source repository\thttps://github.com/hagezi/dns-blocklists'
            )
            printf '%b  Block ads and threats:%b\n' "$blue" "$reset"
            ui_print_table "    " "  -  " 0 "PROFILE\tDESCRIPTION" "${blocklist_rows[@]}"
            info_desc "Full includes Encrypted DNS protection; Custom can disable it for TV compatibility."
            info_desc "Blocked domains return NXDOMAIN. DNS filtering does not replace a firewall."
            info_desc "Resource floors: Minimal/Optimal 1 vCPU and 1280 MB RAM; Full 2 vCPU and 1792 MB RAM."
            info_desc "Maximum requires 2 vCPU and 2304 MB RAM. Custom is calculated from the selected lists."
            info_desc "Large feeds work best with 4 GB RAM."
            echo
            printf '%b  Block ads and threats menu:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Disabled"
            info_desc "       Delete DNS blocklists from the VPS."
            printf '%s\n' "    2. Minimal"
            info_desc "       Enable malware and malicious website protection."
            printf '%s\n' "    3. Optimal"
            info_desc "       Add phishing, scams, and selected tracker protection."
            printf '%s\n' "    4. Full"
            info_desc "       Add advertising, tracking, telemetry, and broader threat protection."
            printf '%s\n' "    5. Maximum"
            info_desc "       Add broad threat feeds and known DNS bypass service domains."
            printf '%s\n' "    6. Custom"
            info_desc "       Toggle individual lists and apply a resource-checked combination."
            echo
            show_dns_profile_matrix
            echo
            printf '%b  List descriptions:%b\n' "$blue" "$reset"
            ui_print_table "    " "  -  " 0 "LIST\tDESCRIPTION" "${list_description_rows[@]}"
            echo
            printf '%b  Custom selection:%b\n' "$blue" "$reset"
            info_desc "Select a number to toggle a list. [ON] means it will be deployed."
            info_desc "The selected lists are checked against the detected VPS CPU and RAM."
            info_desc "Large alternatives are mutually exclusive: Pro++/Ultimate, TIF Mini/Medium, and Gambling sizes."
            info_desc "Apply saves the selected lists in the Vault only after the VPS deployment succeeds."
            ;;
        local_region)
            printf '%b  Block countries:%b\n' "$blue" "$reset"
            info_desc "Select one or more countries whose destinations should be blocked on the VPS."
            info_desc "This is a fallback policy for clients that cannot route local traffic directly."
            info_desc "Xray blocks IP ranges assigned to the selected countries."
            info_desc "For Russia, .ru and .рф domains are also matched when RU is selected."
            info_desc "Other country domains may use foreign CDNs and are not a complete domain-zone block."
            info_desc "This does not route traffic directly and does not guarantee that a VPN will be undetectable."
            info_desc "Shared hosting, CDNs, geolocation databases, and country domains can cause false positives."
            info_desc "The policy is stored for this VPN node and applies to all its access keys."
            echo
            printf '%b  Block countries menu:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Select countries"
            info_desc "       Search by country name or ISO code and toggle multiple checkboxes."
            printf '%s\n' "    2. Disable policy"
            info_desc "       Delete all country blocking rules from this VPN node."
            info_desc "Use Apply after selecting countries; the Vault changes only after deployment succeeds."
            ;;
        server)
            printf '%b  Manage VPN server:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Check VPN status"
            info_desc "       Test management SSH, the Xray container, and both VPN ports."
            printf '%s\n' "    2. Open SSH session"
            info_desc "       Connect as the saved management user using the saved SSH key and port."
            printf '%s\n' "    3. Restart VPN server"
            info_desc "       Restart the Xray Docker stack without changing keys or profiles."
            printf '%s\n' "    4. Block ads and threats"
            info_desc "       Choose protection against ads, trackers, malware, phishing, and other known threats."
            printf '%s\n' "    5. Block countries"
            info_desc "       Stop connections to selected countries when direct bypass is unavailable."
            printf '%s\n' "    6. Rotate SSH key"
            info_desc "       Generate a new SSH key for secure access to this VPS and disable the old key."
            printf '%s\n' "    7. Delete VPN server"
            info_desc "       Clean Xray and Docker from the VPS before deleting its Vault entry."
            ;;
        cascade-server)
            printf '%b  Manage Cascade VPN server:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Check VPN status"
            info_desc "       Test both VPS nodes, the selected backhaul, and the VPN ports."
            printf '%s\n' "    2. Open SSH session"
            info_desc "       Select ingress or egress and connect with its saved management key."
            printf '%s\n' "    3. Restart VPN server"
            info_desc "       Restart both Cascade VPN stacks without changing keys or policies."
            printf '%s\n' "    4. Block ads and threats"
            info_desc "       Change the DNS protection profile on the egress VPS."
            printf '%s\n' "    5. Block countries"
            info_desc "       Apply country blocking rules on the ingress VPS."
            printf '%s\n' "    6. Rotate SSH key"
            info_desc "       Generate a new management key for the selected Cascade VPS."
            printf '%s\n' "    7. Manage routing rules"
            info_desc "       Import and deploy the Cascade ingress routing policy."
            printf '%s\n' "    8. Update Cascade"
            info_desc "       Reapply the current Cascade settings to both VPS nodes."
            printf '%s\n' "    9. Replace VPS node"
            info_desc "       Choose ingress or egress, deploy its replacement, verify the Cascade, and then update the Vault."
            printf '%s\n' "   10. Delete VPN server"
            info_desc "       Clean both VPS nodes before deleting the Cascade from the Vault."
            info_desc "The replacement operation changes a VPS IP but keeps the selected access and backhaul transports."
            info_desc "If DPI blocks a transport signature, use a separate transport migration."
            info_desc "Access and backhaul transports are independent and can be implemented separately."
            ;;
        vault)
            printf '%b  Vault:%b\n' "$blue" "$reset"
            info_desc "The Vault is encrypted local storage for VPS access data and VPN keys."
            info_desc "It is unlocked only when an operation needs the saved data."
            info_desc "Keep the Vault password safe: it cannot be recovered from the file."
            info_desc "Before each successful Vault replacement, the previous encrypted file is saved as a recovery copy."
            info_desc "The newest 20 automatic recovery copies are kept; older copies are deleted automatically."
            info_desc "Automatic recovery copies are internal and are not shown in the backup browser."
            info_desc "Backup encrypted state creates a manual tar.gz archive for recovery and transfer."
            echo
            printf '%b  Vault menu:%b\n' "$blue" "$reset"
            printf '%s\n' "    1. Change encryption password"
            info_desc "       Re-encrypt the Vault with a new local password."
            printf '%s\n' "    2. Backup encrypted state"
            info_desc "       Create a copy of the encrypted Vault for recovery."
            printf '%s\n' "    3. Restore encrypted state"
            info_desc "       Replace the current Vault with a selected encrypted backup."
            printf '%s\n' "    4. View backups"
            info_desc "       Show user encrypted archives with their paths and timestamps."
            printf '%s\n' "    5. Delete Vault"
            info_desc "       Delete local Vault data, backups, VPS credentials, and VPN keys."
            info_desc "When no Vault exists, option 1 creates it, option 2 restores a backup, and option 3 lists backups."
            ;;
        removal)
            printf '%b  VPN server deletion:%b\n' "$blue" "$reset"
            info_desc "Confirm with y to delete the VPN server from the VPS."
            info_desc "Cancel with n or b to leave the VPS and Vault unchanged."
            info_desc "Xray, Docker, updater services, and the deploy user are deleted from the VPS."
            info_desc "The original SSH configuration is restored from its backup."
            info_desc "The server stays in the Vault if remote deletion fails."
            info_desc "After a failed cleanup, choose 1 to retry or 2 to delete only the local Vault entry."
            info_desc "Press b to keep the server in the Vault and return."
            ;;
        install-recovery)
            printf '%b  Incomplete installation:%b\n' "$blue" "$reset"
            info_desc "Resume uses the same saved SSH key, port, and settings."
            info_desc "Abort removes the partial installation and restores the original SSH access."
            info_desc "Start over is only for a VPS that you cleaned manually."
            info_desc "The main Vault entry is created only after the installation passes all checks."
            ;;
        *)
            printf '%b  Nitka:%b\n' "$blue" "$reset"
            info_desc "This tool installs and manages your Xray VPN servers."
            info_desc "VPS access data and VPN keys are kept in the encrypted Vault."
            echo
            printf '%b  Main menu:%b\n' "$blue" "$reset"
            echo
            printf '%s\n' "  1. VPN servers"
            info_desc " - View the VPN servers saved in the Vault."
            info_desc " - Check the VPS, SSH, Xray, and VPN port status."
            info_desc " - Select a server to manage it."
            info_desc " - If there are no servers, add one with option 2 first."
            echo
            printf '%s\n' "     Selected server menu:"
            printf '%s\n' "     1. Manage VPN server"
            info_desc "        Check status, restart Xray, manage DNS and country policy, rotate the SSH key, or delete the server."
            printf '%s\n' "     2. Manage access keys"
            info_desc "        Show, add, or delete VPN access keys for this server."
            echo
            printf '%s\n' "  2. Add VPN server"
            info_desc " - Enter the VPS IP address, SSH user, SSH port, and password on separate screens."
            info_desc " - Review the connection details, then let the manager check SSH access and VPS resources."
            info_desc " - Enter a Reality camouflage domain, or use github.com by default."
            info_desc " - Each VPN key gets two connection links: vision and xhttp."
            info_desc "   If one link is blocked or unstable, use the other."
            info_desc "   Random ports are generated automatically."
            info_desc " - Configure Xray VPN ports using the four setup options."
            info_desc "   The default is random ports for both links."
            info_desc "   Manual ports must be different and must not overlap the generated SSH port."
            info_desc " - Choose a profile to block ads, trackers, malware, phishing, and other threats."
            info_desc "   The menu checks VPS resources before allowing a profile."
            info_desc " - Install Docker, Xray, automatic updates, and SSH hardening."
            info_desc " - Generate VPN access keys and save all connection data in the Vault."
            info_desc " - Repeating setup for the same VPS is safe and idempotent."
            echo
            printf '%s\n' "  3. Vault"
            printf '%s\n' "     1. Change encryption password"
            info_desc "        Change the password protecting the local Vault."
            printf '%s\n' "     2. Backup encrypted state"
            info_desc "        Create a backup containing the encrypted Vault file."
            printf '%s\n' "     3. Restore encrypted state"
            info_desc "        Replace the current Vault with a selected encrypted backup."
            printf '%s\n' "     4. View backups"
            info_desc "        List user encrypted archives with their paths and timestamps."
            printf '%s\n' "     5. Delete Vault"
            info_desc "        Delete local VPS access data, VPN keys, and Vault backups."
            echo
            printf '%s\n' "  Manage VPN server"
            printf '%s\n' "     1. Check VPN status"
            info_desc "        Test SSH access, the Xray container, and both VPN ports."
            printf '%s\n' "     2. Open SSH session"
            info_desc "        Connect using the saved management key and port."
            printf '%s\n' "     3. Restart VPN server"
            info_desc "        Restart the Xray Docker stack without changing access keys."
            printf '%s\n' "     4. Block ads and threats"
            info_desc "        Enable, disable, or change protection against ads and known threats."
            printf '%s\n' "     5. Block countries"
            info_desc "        Stop connections to selected countries when client bypass is unavailable."
            printf '%s\n' "     6. Rotate SSH key"
            info_desc "        Generate a new SSH key for secure access to this VPS and disable the old key."
            printf '%s\n' "     7. Delete VPN server"
            info_desc "        Delete the Xray installation and clean up the VPS."
            info_desc "        The Vault is changed only after remote deletion succeeds."
            echo
            printf '%s\n' "  Manage access keys"
            printf '%s\n' "     1. Show"
            info_desc "        Display the Vision and XHTTP connection links for each key."
            printf '%s\n' "     2. Add"
            info_desc "        Add from 1 to 50 access keys and deploy them to the VPS."
            printf '%s\n' "     3. Delete"
            info_desc "        Select a key by number and delete both protocol UUIDs together."
            info_desc "        Choose the last number to delete every access key at once."
            ;;
    esac
    echo
    read -r -e -p "Press Enter to return" _
}
