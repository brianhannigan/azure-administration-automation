#!/usr/bin/env python3

import sys
import requests
from typing import Dict, List, Any

from azure.identity import DefaultAzureCredential

# API version constants
SUBSCRIPTION_API = "2022-12-01"
VM_API = "2025-04-01"
AZURE_RESOURCE = "https://management.azure.com/.default"


def get_azure_access_token() -> str:
    """Obtain a Bearer token using DefaultAzureCredential."""
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token(AZURE_RESOURCE)
        return token.token
    except Exception as exc:
        raise RuntimeError(f"failed to get Azure token: {exc}") from exc


def send_request(method: str, url: str, token: str, body: Dict[str, Any] | None = None) -> requests.Response:
    """Send an HTTP request with Bearer token authentication."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=body,
            timeout=30,
        )
        return response
    except requests.RequestException as exc:
        raise RuntimeError(f"failed to send request: {exc}") from exc


def parse_resource_group(resource_id: str) -> str:
    """
    Extract the resource group from a resource ID.

    Example:
    /subscriptions/{sid}/resourceGroups/{rg}/providers/...
    """
    marker = "/resourceGroups/"
    idx = resource_id.find(marker)
    if idx == -1:
        return ""

    remainder = resource_id[idx + len(marker):]
    slash_idx = remainder.find("/")
    if slash_idx == -1:
        return remainder
    return remainder[:slash_idx]


def collect_vms(token: str) -> List[Dict[str, str]]:
    """Collect all VMs across all subscriptions."""
    subscription_url = (
        f"https://management.azure.com/subscriptions"
        f"?api-version={SUBSCRIPTION_API}"
    )

    resp = send_request("GET", subscription_url, token)

    if resp.status_code != 200:
        raise RuntimeError(
            f"unexpected status for subscriptions: {resp.status_code}"
        )

    try:
        subs_resp = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"failed to parse subscriptions JSON: {exc}") from exc

    all_vms: List[Dict[str, str]] = []

    for sub in subs_resp.get("value", []):
        subscription_id = sub.get("subscriptionId")
        if not subscription_id:
            continue

        print(f"[INF]: Processing subscription {subscription_id}")

        vm_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/providers/Microsoft.Compute/virtualMachines"
            f"?api-version={VM_API}"
        )

        try:
            vm_resp = send_request("GET", vm_url, token)
        except Exception as exc:
            print(
                f"[ERR]: Failed to fetch VMs for {subscription_id}: {exc}",
                file=sys.stderr,
            )
            continue

        if vm_resp.status_code != 200:
            print(
                f"[ERR]: Unexpected status for VMs in {subscription_id}: {vm_resp.status_code}",
                file=sys.stderr,
            )
            continue

        try:
            vms_resp = vm_resp.json()
        except ValueError as exc:
            print(f"[ERR]: Failed to parse VMs JSON: {exc}", file=sys.stderr)
            continue

        for vm in vms_resp.get("value", []):
            vm_id = vm.get("id", "")
            vm_name = vm.get("name", "")
            resource_group = parse_resource_group(vm_id)

            if not vm_name or not resource_group:
                continue

            all_vms.append(
                {
                    "subscription_id": subscription_id,
                    "resource_group": resource_group,
                    "vm_name": vm_name,
                    "vm_id": vm_id,
                }
            )

    return all_vms


def choose_vm(vms: List[Dict[str, str]]) -> Dict[str, str]:
    """Display a numbered VM list and ask the user which one to start."""
    if not vms:
        raise RuntimeError("no VMs found in accessible subscriptions")

    print("\nAvailable VMs:")
    print("-" * 80)
    for idx, vm in enumerate(vms, start=1):
        print(
            f"{idx}. VM Name: {vm['vm_name']}\n"
            f"   Subscription: {vm['subscription_id']}\n"
            f"   Resource Group: {vm['resource_group']}"
        )
    print("-" * 80)

    while True:
        choice = input("Enter the number of the VM to start: ").strip()

        if not choice.isdigit():
            print("[ERR]: Please enter a valid number.")
            continue

        vm_index = int(choice)
        if vm_index < 1 or vm_index > len(vms):
            print(f"[ERR]: Please enter a number between 1 and {len(vms)}.")
            continue

        return vms[vm_index - 1]


def start_vm(token: str, vm: Dict[str, str]) -> None:
    """Send the start request for the selected VM."""
    subscription_id = vm["subscription_id"]
    resource_group = vm["resource_group"]
    vm_name = vm["vm_name"]

    start_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/start"
        f"?api-version={VM_API}"
    )

    print(
        f"\n[DBG]: Sending POST request to start VM.\n"
        f"    SubscriptionID: {subscription_id}\n"
        f"    ResourceGroup: {resource_group}\n"
        f"    VM Name: {vm_name}\n"
        f"    URL: {start_url}"
    )

    start_resp = send_request("POST", start_url, token)

    if start_resp.status_code not in (200, 202):
        raise RuntimeError(
            f"unexpected status for starting VM {vm_name}: {start_resp.status_code}"
        )

    print(f"[INF]: VM {vm_name} start request accepted")


def main() -> None:
    try:
        token = get_azure_access_token()
    except Exception as exc:
        print(f"[ERR]: Failed to get Azure token: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        vms = collect_vms(token)
    except Exception as exc:
        print(f"[ERR]: Failed to collect VMs: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        selected_vm = choose_vm(vms)
    except Exception as exc:
        print(f"[ERR]: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        start_vm(token, selected_vm)
    except Exception as exc:
        print(f"[ERR]: Failed to start selected VM: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()