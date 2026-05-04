#!/usr/bin/env python3
"""Command-line Azure VM manager.

Interactive CLI alternative to the Tkinter UI in vm_manager.py.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from azure.identity import DefaultAzureCredential

SUBSCRIPTION_API = "2022-12-01"
VM_API = "2025-04-01"
INSTANCE_VIEW_API = "2025-04-01"
AZURE_RESOURCE = "https://management.azure.com/.default"
CONFIG_DIR = Path.cwd() / "vm_configs"


@dataclass
class VmRecord:
    id: str
    name: str
    subscription_id: str
    resource_group: str
    location: str
    size: str
    os_type: str
    power_state: str


class AzureVmManagerCLI:
    def __init__(self) -> None:
        self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        self.token: Optional[str] = None
        self.vms: List[VmRecord] = []
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def run(self) -> None:
        self._print_banner()
        self._authenticate()
        while True:
            self._print_menu()
            choice = input("Select an action (1-8): ").strip()
            if choice == "1":
                self.load_vms_interactive()
            elif choice == "2":
                self.list_loaded_vms()
            elif choice == "3":
                self.perform_vm_action("start")
            elif choice == "4":
                self.perform_vm_action("deallocate")
            elif choice == "5":
                self.perform_vm_action("delete")
            elif choice == "6":
                self.export_loaded_vms()
            elif choice == "7":
                self.show_vm_details()
            elif choice == "8":
                print("\nGoodbye. Exiting Azure VM Manager CLI.")
                return
            else:
                print("\nInvalid selection. Enter a number from 1 to 8.")

    def _print_banner(self) -> None:
        print("=" * 78)
        print(" Azure VM Manager CLI (Interactive)")
        print("=" * 78)
        print("This tool runs entirely in the terminal and prompts you step-by-step.")
        print("You can load VMs, inspect details, run lifecycle actions, and export JSON.")
        print()

    def _print_menu(self) -> None:
        print("\nMain Menu")
        print("-" * 30)
        print("1) Load VMs from all enabled subscriptions")
        print("2) List loaded VMs")
        print("3) Start a VM")
        print("4) Stop (deallocate) a VM")
        print("5) Delete a VM")
        print("6) Export loaded VM inventory to JSON")
        print("7) Show VM details")
        print("8) Exit")

    def _authenticate(self) -> None:
        print("Authenticating with Azure using DefaultAzureCredential...")
        token = self.credential.get_token(AZURE_RESOURCE)
        self.token = token.token
        print("Authentication successful.")

    def _headers(self) -> Dict[str, str]:
        if not self.token:
            self._authenticate()
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = requests.request(method=method, url=url, headers=self._headers(), timeout=60, **kwargs)
        if response.status_code == 401:
            self._authenticate()
            response = requests.request(method=method, url=url, headers=self._headers(), timeout=60, **kwargs)
        response.raise_for_status()
        return response

    def _list_subscriptions(self) -> List[Dict[str, Any]]:
        url = f"https://management.azure.com/subscriptions?api-version={SUBSCRIPTION_API}"
        data = self._request("GET", url).json()
        return [s for s in data.get("value", []) if s.get("state", "").lower() == "enabled"]

    def _extract_state(self, statuses: List[Dict[str, str]]) -> str:
        for s in statuses:
            code = s.get("code", "")
            if code.startswith("PowerState/"):
                return code.split("/", 1)[1]
        return "unknown"

    def _parse_ids(self, vm_id: str) -> Tuple[str, str]:
        parts = vm_id.strip("/").split("/")
        try:
            rg = parts[parts.index("resourceGroups") + 1]
            sub = parts[parts.index("subscriptions") + 1]
        except (ValueError, IndexError):
            raise ValueError(f"Could not parse VM resource ID: {vm_id}")
        return sub, rg

    def load_vms_interactive(self) -> None:
        print("\nLoading subscriptions and VMs. This may take a minute...")
        subscriptions = self._list_subscriptions()
        collected: List[VmRecord] = []
        for sub in subscriptions:
            sub_id = sub.get("subscriptionId")
            sub_name = sub.get("displayName", "unknown")
            print(f"- Scanning subscription: {sub_name} ({sub_id})")
            url = f"https://management.azure.com/subscriptions/{sub_id}/providers/Microsoft.Compute/virtualMachines?api-version={VM_API}"
            vm_data = self._request("GET", url).json().get("value", [])
            for vm in vm_data:
                vm_id = vm.get("id", "")
                vm_name = vm.get("name", "")
                location = vm.get("location", "")
                props = vm.get("properties", {})
                hardware = props.get("hardwareProfile", {})
                storage = props.get("storageProfile", {})
                os_disk = storage.get("osDisk", {})
                vm_size = hardware.get("vmSize", "unknown")
                os_type = os_disk.get("osType", "unknown")

                isub, rg = self._parse_ids(vm_id)
                iv_url = f"https://management.azure.com{vm_id}/instanceView?api-version={INSTANCE_VIEW_API}"
                try:
                    statuses = self._request("GET", iv_url).json().get("statuses", [])
                    state = self._extract_state(statuses)
                except Exception:
                    state = "unknown"
                collected.append(
                    VmRecord(
                        id=vm_id,
                        name=vm_name,
                        subscription_id=isub,
                        resource_group=rg,
                        location=location,
                        size=vm_size,
                        os_type=str(os_type),
                        power_state=state,
                    )
                )

        self.vms = sorted(collected, key=lambda v: (v.subscription_id, v.resource_group, v.name.lower()))
        print(f"Loaded {len(self.vms)} VM(s) across {len(subscriptions)} subscription(s).")

    def _choose_vm(self) -> Optional[VmRecord]:
        if not self.vms:
            print("No VMs loaded yet. Choose menu option 1 first.")
            return None
        print("\nLoaded VMs:")
        for idx, vm in enumerate(self.vms, start=1):
            print(f"{idx:>3}. {vm.name:<28} [{vm.power_state:<12}] {vm.resource_group} / {vm.subscription_id}")
        raw = input("Enter VM number (or press Enter to cancel): ").strip()
        if not raw:
            print("Cancelled.")
            return None
        try:
            selected = self.vms[int(raw) - 1]
        except Exception:
            print("Invalid VM selection.")
            return None
        return selected

    def perform_vm_action(self, action: str) -> None:
        vm = self._choose_vm()
        if not vm:
            return
        verbs = {"start": "start", "deallocate": "stop (deallocate)", "delete": "delete"}
        confirm = input(f"Confirm {verbs[action]} VM '{vm.name}'? Type YES to continue: ").strip()
        if confirm != "YES":
            print("Action cancelled.")
            return

        if action == "delete":
            url = f"https://management.azure.com{vm.id}?api-version={VM_API}"
        else:
            url = f"https://management.azure.com{vm.id}/{action}?api-version={VM_API}"

        print(f"Submitting '{action}' request...")
        self._request("POST" if action != "delete" else "DELETE", url)
        print("Request accepted by Azure. Reload VMs to refresh state.")

    def list_loaded_vms(self) -> None:
        if not self.vms:
            print("No VMs loaded. Use option 1.")
            return
        print(f"\nLoaded VM count: {len(self.vms)}")
        for vm in self.vms:
            print(f"- {vm.name} | {vm.power_state} | {vm.location} | {vm.size} | {vm.os_type}")

    def show_vm_details(self) -> None:
        vm = self._choose_vm()
        if not vm:
            return
        print("\nVM Detail")
        print("-" * 30)
        print(f"Name:            {vm.name}")
        print(f"Power state:     {vm.power_state}")
        print(f"Subscription:    {vm.subscription_id}")
        print(f"Resource group:  {vm.resource_group}")
        print(f"Location:        {vm.location}")
        print(f"Size:            {vm.size}")
        print(f"OS type:         {vm.os_type}")
        print(f"Resource ID:     {vm.id}")

    def export_loaded_vms(self) -> None:
        if not self.vms:
            print("No VMs loaded. Use option 1.")
            return
        default_name = "azure_vm_inventory_cli.json"
        output = input(f"Output filename [{default_name}]: ").strip() or default_name
        path = CONFIG_DIR / output
        payload = {
            "generated_by": "vm_manager_cli.py",
            "vm_count": len(self.vms),
            "vms": [vm.__dict__ for vm in self.vms],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Inventory written to: {path}")


if __name__ == "__main__":
    try:
        AzureVmManagerCLI().run()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(130)
    except Exception as exc:
        print(f"\nFatal error: {exc}")
        sys.exit(1)
