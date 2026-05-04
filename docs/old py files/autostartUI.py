#!/usr/bin/env python3

import base64
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from typing import Dict, List, Any, Optional

import requests
from azure.identity import DefaultAzureCredential

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except Exception:
    Fernet = None
    PBKDF2HMAC = None
    hashes = None


SUBSCRIPTION_API = "2022-12-01"
VM_API = "2025-04-01"
INSTANCE_VIEW_API = "2025-04-01"
AZURE_RESOURCE = "https://management.azure.com/.default"
POLL_SECONDS = 4
POLL_ATTEMPTS = 30


class AzureVmManagerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Azure VM Manager")
        self.root.geometry("1480x840")
        self.root.minsize(1220, 720)

        self.token: Optional[str] = None
        self.vms: List[Dict[str, Any]] = []

        self._configure_styles()
        self._build_ui()
        self._create_status_images()

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("FieldLabel.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("Card.TFrame", relief="solid", borderwidth=1)
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Azure VM Manager", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 10))

        self.load_button = ttk.Button(controls, text="Load VMs", command=self.load_vms)
        self.load_button.pack(side="left")

        self.start_button = ttk.Button(controls, text="Start Selected VM", command=self.start_selected_vm, state="disabled")
        self.start_button.pack(side="left", padx=(8, 0))

        self.stop_button = ttk.Button(controls, text="Stop Selected VM", command=self.stop_selected_vm, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))

        self.delete_button = ttk.Button(controls, text="Delete Selected VM", command=self.delete_selected_vm, state="disabled")
        self.delete_button.pack(side="left", padx=(8, 0))

        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.load_vms)
        self.refresh_button.pack(side="left", padx=(8, 0))

        self.save_start_cfg_button = ttk.Button(controls, text="Save Start Config", command=self.save_start_config, state="disabled")
        self.save_start_cfg_button.pack(side="left", padx=(18, 0))

        self.save_stop_cfg_button = ttk.Button(controls, text="Save Stop Config", command=self.save_stop_config, state="disabled")
        self.save_stop_cfg_button.pack(side="left", padx=(8, 0))

        self.save_inventory_cfg_button = ttk.Button(controls, text="Save VM Inventory", command=self.save_inventory_config, state="disabled")
        self.save_inventory_cfg_button.pack(side="left", padx=(8, 0))

        self.save_full_export_button = ttk.Button(controls, text="Save Full VM Export", command=self.save_full_vm_export, state="disabled")
        self.save_full_export_button.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        content = ttk.Panedwindow(main, orient="horizontal")
        content.pack(fill="both", expand=True)

        left_panel = ttk.Frame(content, padding=(0, 0, 8, 0))
        right_panel = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(left_panel, weight=3)
        content.add(right_panel, weight=2)

        ttk.Label(left_panel, text="Virtual Machines", style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        tree_frame = ttk.Frame(left_panel)
        tree_frame.pack(fill="both", expand=True)

        columns = ("status_text", "vm_name", "resource_group", "location", "size", "os_type", "subscription_id")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", height=18, selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.heading("status_text", text="Status")
        self.tree.heading("vm_name", text="VM Name")
        self.tree.heading("resource_group", text="Resource Group")
        self.tree.heading("location", text="Location")
        self.tree.heading("size", text="Size")
        self.tree.heading("os_type", text="OS")
        self.tree.heading("subscription_id", text="Subscription")

        self.tree.column("#0", width=38, anchor="center", stretch=False)
        self.tree.column("status_text", width=110, anchor="w", stretch=False)
        self.tree.column("vm_name", width=220, anchor="w")
        self.tree.column("resource_group", width=180, anchor="w")
        self.tree.column("location", width=110, anchor="w", stretch=False)
        self.tree.column("size", width=170, anchor="w")
        self.tree.column("os_type", width=100, anchor="w", stretch=False)
        self.tree.column("subscription_id", width=260, anchor="w")

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        ttk.Label(right_panel, text="VM Details", style="Section.TLabel").pack(anchor="w", pady=(0, 6))

        details_card = ttk.Frame(right_panel, style="Card.TFrame", padding=12)
        details_card.pack(fill="x", expand=False)

        self.detail_vars = {
            "vm_name": tk.StringVar(value="—"),
            "status": tk.StringVar(value="—"),
            "subscription_id": tk.StringVar(value="—"),
            "resource_group": tk.StringVar(value="—"),
            "location": tk.StringVar(value="—"),
            "size": tk.StringVar(value="—"),
            "os_type": tk.StringVar(value="—"),
            "vm_id": tk.StringVar(value="—"),
        }

        rows = [
            ("VM Name", "vm_name"),
            ("Status", "status"),
            ("Subscription", "subscription_id"),
            ("Resource Group", "resource_group"),
            ("Location", "location"),
            ("VM Size", "size"),
            ("OS Type", "os_type"),
        ]
        for row_idx, (label_text, key) in enumerate(rows):
            ttk.Label(details_card, text=label_text + ":", style="FieldLabel.TLabel").grid(row=row_idx, column=0, sticky="nw", padx=(0, 10), pady=4)
            ttk.Label(details_card, textvariable=self.detail_vars[key], wraplength=420, justify="left").grid(row=row_idx, column=1, sticky="nw", pady=4)
        details_card.columnconfigure(1, weight=1)

        ttk.Label(right_panel, text="Resource ID", style="FieldLabel.TLabel").pack(anchor="w", pady=(12, 4))
        self.vm_id_text = tk.Text(right_panel, height=4, wrap="word")
        self.vm_id_text.pack(fill="x", expand=False)
        self.vm_id_text.configure(state="disabled")

        ttk.Label(right_panel, text="Log", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.log_text = tk.Text(right_panel, height=14, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self.start_selected_vm())

    def _make_circle_icon(self, fill: str, outline: Optional[str] = None) -> tk.PhotoImage:
        size = 14
        img = tk.PhotoImage(width=size, height=size)
        img.put("", to=(0, 0, size, size))
        outline = outline or fill
        cx = cy = (size - 1) / 2
        r = 5.0
        for y in range(size):
            for x in range(size):
                dx = x - cx
                dy = y - cy
                dist2 = dx * dx + dy * dy
                if dist2 <= r * r:
                    color = outline if dist2 >= (r - 1.2) * (r - 1.2) else fill
                    img.put(color, (x, y))
        return img

    def _create_status_images(self) -> None:
        self.status_images = {
            "running": self._make_circle_icon("#18a558", "#0f7a3f"),
            "stopped": self._make_circle_icon("#d93025", "#a61b14"),
            "transition": self._make_circle_icon("#f9ab00", "#c47e00"),
            "unknown": self._make_circle_icon("#9aa0a6", "#6b7280"),
            "deleted": self._make_circle_icon("#6b7280", "#374151"),
        }

    def log(self, message: str) -> None:
        def _append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, _append)

    def set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def set_buttons_loading(self, is_loading: bool) -> None:
        def _update() -> None:
            state = "disabled" if is_loading else "normal"
            self.load_button.configure(state=state)
            self.refresh_button.configure(state=state)
            if is_loading:
                self.start_button.configure(state="disabled")
                self.stop_button.configure(state="disabled")
                self.delete_button.configure(state="disabled")
                self.save_start_cfg_button.configure(state="disabled")
                self.save_stop_cfg_button.configure(state="disabled")
                self.save_inventory_cfg_button.configure(state="disabled")
                self.save_full_export_button.configure(state="disabled")
            else:
                self._sync_action_buttons()
        self.root.after(0, _update)

    def _sync_action_buttons(self) -> None:
        selected = self.get_selected_vm()
        has_vms = bool(self.vms)
        self.save_inventory_cfg_button.configure(state="normal" if has_vms else "disabled")
        self.save_full_export_button.configure(state="normal" if selected else "disabled")

        if not selected:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.delete_button.configure(state="disabled")
            self.save_start_cfg_button.configure(state="disabled")
            self.save_stop_cfg_button.configure(state="disabled")
            self.save_full_export_button.configure(state="disabled")
            return

        self.save_start_cfg_button.configure(state="normal")
        self.save_stop_cfg_button.configure(state="normal")

        status_key = selected.get("status_key", "unknown")
        if status_key == "running":
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.delete_button.configure(state="normal")
        elif status_key == "stopped":
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.delete_button.configure(state="normal")
        elif status_key in ("transition", "deleted"):
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.delete_button.configure(state="disabled")
        else:
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="normal")
            self.delete_button.configure(state="normal")

    def _on_tree_select(self, _event: object = None) -> None:
        self._sync_action_buttons()
        vm = self.get_selected_vm()
        if vm:
            self._show_vm_details(vm)

    def _show_vm_details(self, vm: Dict[str, Any]) -> None:
        self.detail_vars["vm_name"].set(vm.get("vm_name", "—"))
        self.detail_vars["status"].set(vm.get("status_text", "—"))
        self.detail_vars["subscription_id"].set(vm.get("subscription_id", "—"))
        self.detail_vars["resource_group"].set(vm.get("resource_group", "—"))
        self.detail_vars["location"].set(vm.get("location", "—"))
        self.detail_vars["size"].set(vm.get("size", "—"))
        self.detail_vars["os_type"].set(vm.get("os_type", "—"))
        self._set_vm_id_text(vm.get("vm_id", "—"))

    def _set_vm_id_text(self, value: str) -> None:
        self.vm_id_text.configure(state="normal")
        self.vm_id_text.delete("1.0", "end")
        self.vm_id_text.insert("1.0", value)
        self.vm_id_text.configure(state="disabled")

    def get_selected_vm(self) -> Optional[Dict[str, Any]]:
        selection = self.tree.selection()
        if not selection:
            return None
        idx = int(selection[0])
        if idx < 0 or idx >= len(self.vms):
            return None
        return self.vms[idx]

    def get_azure_access_token(self) -> str:
        try:
            credential = DefaultAzureCredential()
            token = credential.get_token(AZURE_RESOURCE)
            return token.token
        except Exception as exc:
            raise RuntimeError(f"failed to get Azure token: {exc}") from exc

    def send_request(self, method: str, url: str, token: str, body: Optional[Dict[str, Any]] = None) -> requests.Response:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            return requests.request(method=method, url=url, headers=headers, json=body, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"failed to send request: {exc}") from exc

    @staticmethod
    def parse_resource_group(resource_id: str) -> str:
        marker = "/resourceGroups/"
        idx = resource_id.find(marker)
        if idx == -1:
            return ""
        remainder = resource_id[idx + len(marker):]
        slash_idx = remainder.find("/")
        return remainder if slash_idx == -1 else remainder[:slash_idx]

    @staticmethod
    def map_power_state(instance_view: Dict[str, Any]) -> tuple[str, str]:
        statuses = instance_view.get("statuses", [])
        for status in statuses:
            code = str(status.get("code", "")).lower()
            display = status.get("displayStatus", "")
            if code.startswith("powerstate/"):
                if "running" in code:
                    return "running", display or "VM running"
                if any(word in code for word in ["deallocated", "stopped"]):
                    return "stopped", display or "VM stopped"
                if any(word in code for word in ["starting", "stopping", "deallocating", "updating"]):
                    return "transition", display or "VM changing state"
                return "unknown", display or code
        return "unknown", "Unknown"

    def get_vm_instance_view(self, token: str, subscription_id: str, resource_group: str, vm_name: str) -> Dict[str, Any]:
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/instanceView"
            f"?api-version={INSTANCE_VIEW_API}"
        )
        response = self.send_request("GET", url, token)
        if response.status_code != 200:
            raise RuntimeError(f"instanceView returned {response.status_code} for VM {vm_name}")
        return response.json()

    def get_resource_by_id(self, token: str, resource_id: str, api_version: str) -> Optional[Dict[str, Any]]:
        if not resource_id:
            return None
        url = f"https://management.azure.com{resource_id}?api-version={api_version}"
        response = self.send_request("GET", url, token)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RuntimeError(f"resource GET failed for {resource_id}: {response.status_code}\nresponse body: {response.text}")
        return response.json()

    @staticmethod
    def _extract_resource_group_from_id(resource_id: str) -> str:
        return AzureVmManagerApp.parse_resource_group(resource_id)

    def get_full_vm_export_payload(self, token: str, vm: Dict[str, Any]) -> Dict[str, Any]:
        vm_resource = self.get_resource_by_id(token, vm["vm_id"], VM_API)
        if not vm_resource:
            raise RuntimeError(f"VM resource not found for export: {vm['vm_name']}")

        vm_properties = vm_resource.get("properties", {})
        network_profile = vm_properties.get("networkProfile", {})
        storage_profile = vm_properties.get("storageProfile", {})
        diagnostics_profile = vm_properties.get("diagnosticsProfile", {})

        export_payload: Dict[str, Any] = {
            "config_type": "azure_full_vm_export",
            "created_by": "Azure VM Manager",
            "recreate_ready": True,
            "notes": [
                "This export captures the VM resource plus related NIC, IP, subnet, VNet, NSG, and disk details when accessible.",
                "Secrets such as admin passwords are not returned by Azure and are not included.",
                "This is intended as a strong recreation reference, not a complete ARM/Bicep deployment package.",
            ],
            "vm_summary": {
                "subscription_id": vm.get("subscription_id"),
                "resource_group": vm.get("resource_group"),
                "vm_name": vm.get("vm_name"),
                "vm_id": vm.get("vm_id"),
                "location": vm.get("location"),
                "size": vm.get("size"),
                "os_type": vm.get("os_type"),
                "status_key": vm.get("status_key"),
                "status_text": vm.get("status_text"),
            },
            "vm_resource": vm_resource,
            "instance_view": None,
            "related_resources": {
                "network_interfaces": [],
                "public_ips": [],
                "subnets": [],
                "virtual_networks": [],
                "network_security_groups": [],
                "managed_disks": [],
                "availability_set": None,
                "proximity_placement_group": None,
                "host_group": None,
            },
            "recreation_hints": {
                "location": vm_resource.get("location"),
                "vm_size": vm_properties.get("hardwareProfile", {}).get("vmSize"),
                "os_type": storage_profile.get("osDisk", {}).get("osType"),
                "image_reference": storage_profile.get("imageReference"),
                "license_type": vm_properties.get("licenseType"),
                "priority": vm_properties.get("priority"),
                "eviction_policy": vm_properties.get("evictionPolicy"),
                "provision_vm_agent": vm_properties.get("osProfile", {}).get("windowsConfiguration", {}).get("provisionVMAgent")
                    if vm_properties.get("osProfile", {}).get("windowsConfiguration") else None,
                "boot_diagnostics": diagnostics_profile.get("bootDiagnostics"),
                "zones": vm_resource.get("zones", []),
                "tags": vm_resource.get("tags", {}),
            },
        }

        try:
            export_payload["instance_view"] = self.get_vm_instance_view(
                token,
                vm["subscription_id"],
                vm["resource_group"],
                vm["vm_name"],
            )
        except Exception as exc:
            self.log(f"[WRN]: Could not add instance view to export for {vm['vm_name']}: {exc}")

        api_versions = {
            "nic": "2024-05-01",
            "pip": "2024-05-01",
            "vnet": "2024-05-01",
            "subnet": "2024-05-01",
            "nsg": "2024-05-01",
            "disk": "2024-03-02",
            "availability_set": "2024-03-01",
            "ppg": "2024-03-01",
            "host_group": "2024-03-01",
        }

        seen_ids = {key: set() for key in ["public_ips", "subnets", "virtual_networks", "network_security_groups", "managed_disks", "network_interfaces"]}

        for nic_ref in network_profile.get("networkInterfaces", []) or []:
            nic_id = nic_ref.get("id")
            if not nic_id or nic_id in seen_ids["network_interfaces"]:
                continue
            seen_ids["network_interfaces"].add(nic_id)
            try:
                nic = self.get_resource_by_id(token, nic_id, api_versions["nic"])
                if not nic:
                    continue
                export_payload["related_resources"]["network_interfaces"].append(nic)
                nic_props = nic.get("properties", {})
                nic_nsg = nic_props.get("networkSecurityGroup", {}).get("id")
                if nic_nsg and nic_nsg not in seen_ids["network_security_groups"]:
                    seen_ids["network_security_groups"].add(nic_nsg)
                    nsg = self.get_resource_by_id(token, nic_nsg, api_versions["nsg"])
                    if nsg:
                        export_payload["related_resources"]["network_security_groups"].append(nsg)
                for ip_cfg in nic_props.get("ipConfigurations", []) or []:
                    ip_props = ip_cfg.get("properties", {})
                    pip_id = ip_props.get("publicIPAddress", {}).get("id")
                    if pip_id and pip_id not in seen_ids["public_ips"]:
                        seen_ids["public_ips"].add(pip_id)
                        pip = self.get_resource_by_id(token, pip_id, api_versions["pip"])
                        if pip:
                            export_payload["related_resources"]["public_ips"].append(pip)
                    subnet_id = ip_props.get("subnet", {}).get("id")
                    if subnet_id and subnet_id not in seen_ids["subnets"]:
                        seen_ids["subnets"].add(subnet_id)
                        subnet = self.get_resource_by_id(token, subnet_id, api_versions["subnet"])
                        if subnet:
                            export_payload["related_resources"]["subnets"].append(subnet)
                            subnet_nsg = subnet.get("properties", {}).get("networkSecurityGroup", {}).get("id")
                            if subnet_nsg and subnet_nsg not in seen_ids["network_security_groups"]:
                                seen_ids["network_security_groups"].add(subnet_nsg)
                                nsg = self.get_resource_by_id(token, subnet_nsg, api_versions["nsg"])
                                if nsg:
                                    export_payload["related_resources"]["network_security_groups"].append(nsg)
                            vnet_id = subnet_id.split("/subnets/")[0] if "/subnets/" in subnet_id else None
                            if vnet_id and vnet_id not in seen_ids["virtual_networks"]:
                                seen_ids["virtual_networks"].add(vnet_id)
                                vnet = self.get_resource_by_id(token, vnet_id, api_versions["vnet"])
                                if vnet:
                                    export_payload["related_resources"]["virtual_networks"].append(vnet)
            except Exception as exc:
                self.log(f"[WRN]: Could not fully export NIC resources for {vm['vm_name']}: {exc}")

        disk_ids = []
        os_disk_id = storage_profile.get("osDisk", {}).get("managedDisk", {}).get("id")
        if os_disk_id:
            disk_ids.append(os_disk_id)
        for data_disk in storage_profile.get("dataDisks", []) or []:
            data_disk_id = data_disk.get("managedDisk", {}).get("id")
            if data_disk_id:
                disk_ids.append(data_disk_id)
        for disk_id in disk_ids:
            if disk_id in seen_ids["managed_disks"]:
                continue
            seen_ids["managed_disks"].add(disk_id)
            try:
                disk = self.get_resource_by_id(token, disk_id, api_versions["disk"])
                if disk:
                    export_payload["related_resources"]["managed_disks"].append(disk)
            except Exception as exc:
                self.log(f"[WRN]: Could not export disk {disk_id} for {vm['vm_name']}: {exc}")

        for key, api_key in [("availabilitySet", "availability_set"), ("proximityPlacementGroup", "ppg"), ("host", "host_group")]:
            resource_id = vm_properties.get(key, {}).get("id")
            if resource_id:
                try:
                    exported = self.get_resource_by_id(token, resource_id, api_versions[api_key])
                    if key == "availabilitySet":
                        export_payload["related_resources"]["availability_set"] = exported
                    elif key == "proximityPlacementGroup":
                        export_payload["related_resources"]["proximity_placement_group"] = exported
                    elif key == "host":
                        export_payload["related_resources"]["host_group"] = exported
                except Exception as exc:
                    self.log(f"[WRN]: Could not export related resource {resource_id} for {vm['vm_name']}: {exc}")

        return export_payload

    def collect_vms(self, token: str) -> List[Dict[str, Any]]:
        subscription_url = f"https://management.azure.com/subscriptions?api-version={SUBSCRIPTION_API}"
        resp = self.send_request("GET", subscription_url, token)
        if resp.status_code != 200:
            raise RuntimeError(f"unexpected status for subscriptions: {resp.status_code}")
        subs_resp = resp.json()

        all_vms: List[Dict[str, Any]] = []
        for sub in subs_resp.get("value", []):
            subscription_id = sub.get("subscriptionId")
            if not subscription_id:
                continue
            self.log(f"[INF]: Processing subscription {subscription_id}")
            vm_url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/providers/Microsoft.Compute/virtualMachines"
                f"?api-version={VM_API}"
            )
            try:
                vm_resp = self.send_request("GET", vm_url, token)
            except Exception as exc:
                self.log(f"[ERR]: Failed to fetch VMs for {subscription_id}: {exc}")
                continue
            if vm_resp.status_code != 200:
                self.log(f"[ERR]: Unexpected status for VMs in {subscription_id}: {vm_resp.status_code}")
                continue

            vms_resp = vm_resp.json()
            for vm in vms_resp.get("value", []):
                vm_id = vm.get("id", "")
                vm_name = vm.get("name", "")
                resource_group = self.parse_resource_group(vm_id)
                if not vm_name or not resource_group:
                    continue

                properties = vm.get("properties", {})
                hardware_profile = properties.get("hardwareProfile", {})
                storage_profile = properties.get("storageProfile", {})
                os_disk = storage_profile.get("osDisk", {})
                os_type = os_disk.get("osType", "Unknown")
                size = hardware_profile.get("vmSize", "Unknown")
                location = vm.get("location", "Unknown")

                status_key = "unknown"
                status_text = "Unknown"
                try:
                    instance_view = self.get_vm_instance_view(token, subscription_id, resource_group, vm_name)
                    status_key, status_text = self.map_power_state(instance_view)
                except Exception as exc:
                    self.log(f"[WRN]: Could not get power state for {vm_name}: {exc}")

                all_vms.append({
                    "subscription_id": subscription_id,
                    "resource_group": resource_group,
                    "vm_name": vm_name,
                    "vm_id": vm_id,
                    "location": location,
                    "size": size,
                    "os_type": os_type,
                    "status_key": status_key,
                    "status_text": status_text,
                })
        return all_vms

    def populate_tree(self) -> None:
        selected_vm_id = None
        selected = self.get_selected_vm()
        if selected:
            selected_vm_id = selected.get("vm_id")

        for item in self.tree.get_children():
            self.tree.delete(item)

        for index, vm in enumerate(self.vms):
            image = self.status_images.get(vm.get("status_key", "unknown"), self.status_images["unknown"])
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text="",
                image=image,
                values=(
                    vm.get("status_text", "Unknown"),
                    vm.get("vm_name", ""),
                    vm.get("resource_group", ""),
                    vm.get("location", ""),
                    vm.get("size", ""),
                    vm.get("os_type", ""),
                    vm.get("subscription_id", ""),
                ),
            )

        if selected_vm_id:
            for idx, vm in enumerate(self.vms):
                if vm.get("vm_id") == selected_vm_id:
                    self.tree.selection_set(str(idx))
                    self.tree.focus(str(idx))
                    self.tree.see(str(idx))
                    break

        self._sync_action_buttons()

    def update_single_vm_row(self, vm_index: int) -> None:
        if vm_index < 0 or vm_index >= len(self.vms):
            return
        vm = self.vms[vm_index]
        if not self.tree.exists(str(vm_index)):
            self.populate_tree()
            return
        image = self.status_images.get(vm.get("status_key", "unknown"), self.status_images["unknown"])
        self.tree.item(
            str(vm_index),
            image=image,
            values=(
                vm.get("status_text", "Unknown"),
                vm.get("vm_name", ""),
                vm.get("resource_group", ""),
                vm.get("location", ""),
                vm.get("size", ""),
                vm.get("os_type", ""),
                vm.get("subscription_id", ""),
            ),
        )
        selected = self.tree.selection()
        if selected and selected[0] == str(vm_index):
            self._show_vm_details(vm)
        self._sync_action_buttons()

    def load_vms(self) -> None:
        threading.Thread(target=self._load_vms_worker, daemon=True).start()

    def _load_vms_worker(self) -> None:
        self.set_buttons_loading(True)
        self.set_status("Loading VMs...")
        self.log("[INF]: Loading VM list from Azure...")
        try:
            self.token = self.get_azure_access_token()
            self.vms = self.collect_vms(self.token)
            self.root.after(0, self.populate_tree)
            self.set_status(f"Loaded {len(self.vms)} VM(s)")
            self.log(f"[INF]: Loaded {len(self.vms)} VM(s)")
            if not self.vms:
                self.root.after(0, lambda: messagebox.showinfo("No VMs", "No VMs were found in accessible subscriptions."))
        except Exception as exc:
            self.set_status("Load failed")
            self.log(f"[ERR]: Failed to load VMs: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Load Failed", str(exc)))
        finally:
            self.set_buttons_loading(False)

    def start_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        if not messagebox.askyesno("Confirm Start", f"Start VM '{vm['vm_name']}' in resource group '{vm['resource_group']}'?"):
            return
        threading.Thread(target=self._start_vm_worker, args=(vm,), daemon=True).start()

    def stop_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        if not messagebox.askyesno("Confirm Stop", f"Stop VM '{vm['vm_name']}' in resource group '{vm['resource_group']}'?"):
            return
        threading.Thread(target=self._stop_vm_worker, args=(vm,), daemon=True).start()

    def delete_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete VM '{vm['vm_name']}'?\n\nThis deletes the VM resource. Attached disks, NICs, and public IPs may remain unless removed separately.",
            icon="warning",
        ):
            return
        threading.Thread(target=self._delete_vm_worker, args=(vm,), daemon=True).start()

    def start_vm_request(self, token: str, vm: Dict[str, Any]) -> None:
        url = (
            f"https://management.azure.com/subscriptions/{vm['subscription_id']}"
            f"/resourceGroups/{vm['resource_group']}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}/start"
            f"?api-version={VM_API}"
        )
        response = self.send_request("POST", url, token)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"unexpected status for starting VM {vm['vm_name']}: {response.status_code}\nresponse body: {response.text}")

    def stop_vm_request(self, token: str, vm: Dict[str, Any]) -> None:
        url = (
            f"https://management.azure.com/subscriptions/{vm['subscription_id']}"
            f"/resourceGroups/{vm['resource_group']}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}/powerOff"
            f"?api-version={VM_API}"
        )
        response = self.send_request("POST", url, token)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"unexpected status for stopping VM {vm['vm_name']}: {response.status_code}\nresponse body: {response.text}")

    def delete_vm_request(self, token: str, vm: Dict[str, Any]) -> None:
        url = (
            f"https://management.azure.com/subscriptions/{vm['subscription_id']}"
            f"/resourceGroups/{vm['resource_group']}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}"
            f"?api-version={VM_API}"
        )
        response = self.send_request("DELETE", url, token)
        if response.status_code not in (200, 202, 204):
            raise RuntimeError(f"unexpected status for deleting VM {vm['vm_name']}: {response.status_code}\nresponse body: {response.text}")

    def _find_vm_index(self, vm_id: str) -> int:
        for idx, candidate in enumerate(self.vms):
            if candidate.get("vm_id") == vm_id:
                return idx
        return -1

    def _start_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Starting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.start_vm_request(self.token, vm)
            self.log(f"[INF]: Start requested for {vm['vm_name']}")
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Starting"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_until(vm["vm_id"], {"running"})
        except Exception as exc:
            self.set_status("Start failed")
            self.log(f"[ERR]: Failed to start VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Start Failed", str(exc)))
        finally:
            self.set_buttons_loading(False)

    def _stop_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Stopping {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.stop_vm_request(self.token, vm)
            self.log(f"[INF]: Stop requested for {vm['vm_name']}")
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Stopping"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_until(vm["vm_id"], {"stopped"})
        except Exception as exc:
            self.set_status("Stop failed")
            self.log(f"[ERR]: Failed to stop VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Stop Failed", str(exc)))
        finally:
            self.set_buttons_loading(False)

    def _delete_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Deleting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.delete_vm_request(self.token, vm)
            self.log(f"[INF]: Delete requested for {vm['vm_name']}")
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Deleting"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_deleted(vm["vm_id"])
        except Exception as exc:
            self.set_status("Delete failed")
            self.log(f"[ERR]: Failed to delete VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Delete Failed", str(exc)))
        finally:
            self.set_buttons_loading(False)

    def _poll_vm_until(self, vm_id: str, target_states: set[str]) -> None:
        idx = self._find_vm_index(vm_id)
        if idx < 0:
            return
        vm = self.vms[idx]
        for _ in range(POLL_ATTEMPTS):
            time.sleep(POLL_SECONDS)
            try:
                instance_view = self.get_vm_instance_view(self.token or self.get_azure_access_token(), vm["subscription_id"], vm["resource_group"], vm["vm_name"])
                status_key, status_text = self.map_power_state(instance_view)
                idx = self._find_vm_index(vm_id)
                if idx < 0:
                    return
                self.vms[idx]["status_key"] = status_key
                self.vms[idx]["status_text"] = status_text
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
                if status_key in target_states:
                    self.set_status(f"{vm['vm_name']} is now {status_text}")
                    return
            except Exception as exc:
                self.log(f"[WRN]: Polling state for {vm['vm_name']} failed: {exc}")
        self.set_status(f"Timed out waiting for {vm['vm_name']} state update")

    def _poll_vm_deleted(self, vm_id: str) -> None:
        idx = self._find_vm_index(vm_id)
        if idx < 0:
            return
        vm = self.vms[idx]
        for _ in range(POLL_ATTEMPTS):
            time.sleep(POLL_SECONDS)
            url = (
                f"https://management.azure.com/subscriptions/{vm['subscription_id']}"
                f"/resourceGroups/{vm['resource_group']}"
                f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}"
                f"?api-version={VM_API}"
            )
            try:
                response = self.send_request("GET", url, self.token or self.get_azure_access_token())
                if response.status_code == 404:
                    remove_idx = self._find_vm_index(vm_id)
                    if remove_idx >= 0:
                        removed_name = self.vms[remove_idx]["vm_name"]
                        del self.vms[remove_idx]
                        self.root.after(0, self.populate_tree)
                        self.set_status(f"Deleted {removed_name}")
                    return
            except Exception as exc:
                self.log(f"[WRN]: Polling delete state for {vm['vm_name']} failed: {exc}")
        self.set_status(f"Timed out waiting for {vm['vm_name']} deletion")

    def _prompt_for_encryption_password(self) -> Optional[str]:
        if Fernet is None or PBKDF2HMAC is None or hashes is None:
            messagebox.showerror(
                "Missing Dependency",
                "Encrypted config export requires the 'cryptography' package.\n\nInstall it with:\npip install cryptography",
            )
            return None
        password = simpledialog.askstring("Encryption Password", "Enter a password to encrypt the config file:", show="*")
        if password is None:
            return None
        password = password.strip()
        if not password:
            messagebox.showwarning("Password Required", "Encryption password cannot be empty.")
            return None
        confirm = simpledialog.askstring("Confirm Password", "Re-enter the password:", show="*")
        if confirm is None:
            return None
        if password != confirm:
            messagebox.showerror("Password Mismatch", "The passwords did not match.")
            return None
        return password

    def _encrypt_json_payload(self, payload: Dict[str, Any], password: str) -> bytes:
        assert PBKDF2HMAC is not None and Fernet is not None and hashes is not None
        salt = b"azure-vm-manager-salt-v1"  # deterministic app salt for portability
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
        key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
        fernet = Fernet(key)
        raw_json = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        encrypted_blob = fernet.encrypt(raw_json)
        wrapper = {
            "format": "azure-vm-manager-encrypted-json",
            "kdf": "PBKDF2HMAC-SHA256",
            "iterations": 390000,
            "payload": encrypted_blob.decode("utf-8"),
        }
        return json.dumps(wrapper, indent=2).encode("utf-8")

    def _save_encrypted_payload(self, payload: Dict[str, Any], suggested_name: str) -> None:
        password = self._prompt_for_encryption_password()
        if not password:
            return
        file_path = filedialog.asksaveasfilename(
            title="Save Encrypted Config",
            defaultextension=".encjson",
            initialfile=suggested_name,
            filetypes=[("Encrypted JSON Config", "*.encjson"), ("All Files", "*.*")],
        )
        if not file_path:
            return
        encrypted = self._encrypt_json_payload(payload, password)
        with open(file_path, "wb") as f:
            f.write(encrypted)
        self.log(f"[INF]: Saved encrypted config: {file_path}")
        messagebox.showinfo("Saved", f"Encrypted config saved to:\n{file_path}")

    def _build_vm_action_payload(self, vm: Dict[str, Any], action: str) -> Dict[str, Any]:
        return {
            "config_type": "azure_vm_action",
            "action": action,
            "created_by": "Azure VM Manager",
            "vm": {
                "subscription_id": vm.get("subscription_id"),
                "resource_group": vm.get("resource_group"),
                "vm_name": vm.get("vm_name"),
                "vm_id": vm.get("vm_id"),
                "location": vm.get("location"),
                "size": vm.get("size"),
                "os_type": vm.get("os_type"),
                "status_key": vm.get("status_key"),
                "status_text": vm.get("status_text"),
            },
            "notes": "This file stores VM targeting details for a future start/stop workflow. It does not store Azure access tokens.",
        }

    def _build_inventory_payload(self) -> Dict[str, Any]:
        return {
            "config_type": "azure_vm_inventory",
            "created_by": "Azure VM Manager",
            "vm_count": len(self.vms),
            "vms": [
                {
                    "subscription_id": vm.get("subscription_id"),
                    "resource_group": vm.get("resource_group"),
                    "vm_name": vm.get("vm_name"),
                    "vm_id": vm.get("vm_id"),
                    "location": vm.get("location"),
                    "size": vm.get("size"),
                    "os_type": vm.get("os_type"),
                    "status_key": vm.get("status_key"),
                    "status_text": vm.get("status_text"),
                }
                for vm in self.vms
            ],
            "notes": "This inventory can be used as a source file when recreating VM definitions or preparing future automation. It does not include full NIC/disk/image/template details from Azure ARM exports.",
        }

    def save_start_config(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        payload = self._build_vm_action_payload(vm, "start")
        safe_name = f"{vm['vm_name']}_start.encjson"
        self._save_encrypted_payload(payload, safe_name)

    def save_stop_config(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        payload = self._build_vm_action_payload(vm, "stop")
        safe_name = f"{vm['vm_name']}_stop.encjson"
        self._save_encrypted_payload(payload, safe_name)

    def save_inventory_config(self) -> None:
        if not self.vms:
            messagebox.showwarning("No VMs", "Load VMs first.")
            return
        payload = self._build_inventory_payload()
        self._save_encrypted_payload(payload, "azure_vm_inventory.encjson")

    def save_full_vm_export(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return
        threading.Thread(target=self._save_full_vm_export_worker, args=(vm,), daemon=True).start()

    def _save_full_vm_export_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Exporting full config for {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            payload = self.get_full_vm_export_payload(self.token, vm)
            safe_name = f"{vm['vm_name']}_full_export.encjson"
            self.root.after(0, lambda p=payload, n=safe_name: self._save_encrypted_payload(p, n))
            self.log(f"[INF]: Full VM export prepared for {vm['vm_name']}")
            self.set_status(f"Full export ready for {vm['vm_name']}")
        except Exception as exc:
            self.set_status("Full export failed")
            self.log(f"[ERR]: Failed to build full VM export: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Full Export Failed", str(exc)))
        finally:
            self.set_buttons_loading(False)


def main() -> None:
    root = tk.Tk()
    app = AzureVmManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
