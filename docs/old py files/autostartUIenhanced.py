#!/usr/bin/env python3

import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, List, Any

import requests
from azure.identity import DefaultAzureCredential

# API version constants
SUBSCRIPTION_API = "2022-12-01"
VM_API = "2025-04-01"
INSTANCE_VIEW_API = "2025-04-01"
AZURE_RESOURCE = "https://management.azure.com/.default"


class AzureVmStarterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Azure VM Manager")
        self.root.geometry("1380x800")
        self.root.minsize(1180, 720)

        self.token: str | None = None
        self.vms: List[Dict[str, str]] = []

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

        ttk.Label(main, text="Azure VM Manager", font=("Segoe UI", 16, "bold")).pack(
            anchor="w", pady=(0, 10)
        )

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 10))

        self.load_button = ttk.Button(controls, text="Load VMs", command=self.load_vms)
        self.load_button.pack(side="left")

        self.start_button = ttk.Button(
            controls, text="Start Selected VM", command=self.start_selected_vm, state="disabled"
        )
        self.start_button.pack(side="left", padx=(8, 0))

        self.stop_button = ttk.Button(
            controls, text="Stop Selected VM", command=self.stop_selected_vm, state="disabled"
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        self.delete_button = ttk.Button(
            controls, text="Delete Selected VM", command=self.delete_selected_vm, state="disabled"
        )
        self.delete_button.pack(side="left", padx=(8, 0))

        self.refresh_button = ttk.Button(controls, text="Refresh", command=self.load_vms)
        self.refresh_button.pack(side="left", padx=(8, 0))

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

        columns = ("status_text", "vm_name", "resource_group", "subscription_id")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            height=18,
            selectmode="browse",
        )
        self.tree.heading("#0", text="")
        self.tree.heading("status_text", text="Status")
        self.tree.heading("vm_name", text="VM Name")
        self.tree.heading("resource_group", text="Resource Group")
        self.tree.heading("subscription_id", text="Subscription")

        self.tree.column("#0", width=40, anchor="center", stretch=False)
        self.tree.column("status_text", width=120, anchor="w", stretch=False)
        self.tree.column("vm_name", width=220, anchor="w")
        self.tree.column("resource_group", width=200, anchor="w")
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
            ttk.Label(details_card, text=label_text + ":", style="FieldLabel.TLabel").grid(
                row=row_idx, column=0, sticky="nw", padx=(0, 10), pady=4
            )
            ttk.Label(details_card, textvariable=self.detail_vars[key], wraplength=420, justify="left").grid(
                row=row_idx, column=1, sticky="nw", pady=4
            )

        details_card.columnconfigure(1, weight=1)

        ttk.Label(right_panel, text="Resource ID", style="FieldLabel.TLabel").pack(anchor="w", pady=(12, 4))
        self.vm_id_text = tk.Text(right_panel, height=5, wrap="word")
        self.vm_id_text.pack(fill="x", expand=False)
        self.vm_id_text.configure(state="disabled")

        ttk.Label(right_panel, text="Log", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.log_text = tk.Text(right_panel, height=14, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", lambda _event: self.start_selected_vm())

    def _make_circle_icon(self, fill: str, outline: str | None = None) -> tk.PhotoImage:
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
            else:
                self._sync_action_buttons()

        self.root.after(0, _update)

    def _sync_action_buttons(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")
            self.delete_button.configure(state="disabled")
            return

        vm = self.vms[int(selection[0])]
        status_key = vm.get("status_key", "unknown")

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
        selection = self.tree.selection()
        if not selection:
            return
        vm = self.vms[int(selection[0])]
        self._show_vm_details(vm)

    def _show_vm_details(self, vm: Dict[str, str]) -> None:
        self.detail_vars["vm_name"].set(vm.get("vm_name", "—"))
        self.detail_vars["status"].set(vm.get("status_text", "Unknown"))
        self.detail_vars["subscription_id"].set(vm.get("subscription_id", "—"))
        self.detail_vars["resource_group"].set(vm.get("resource_group", "—"))
        self.detail_vars["location"].set(vm.get("location", "—"))
        self.detail_vars["size"].set(vm.get("size", "—"))
        self.detail_vars["os_type"].set(vm.get("os_type", "—"))
        self.detail_vars["vm_id"].set(vm.get("vm_id", "—"))

        self.vm_id_text.configure(state="normal")
        self.vm_id_text.delete("1.0", "end")
        self.vm_id_text.insert("1.0", vm.get("vm_id", "—"))
        self.vm_id_text.configure(state="disabled")

    def _clear_vm_details(self) -> None:
        for key in self.detail_vars:
            self.detail_vars[key].set("—")
        self.vm_id_text.configure(state="normal")
        self.vm_id_text.delete("1.0", "end")
        self.vm_id_text.configure(state="disabled")

    def get_azure_access_token(self) -> str:
        try:
            credential = DefaultAzureCredential()
            token = credential.get_token(AZURE_RESOURCE)
            return token.token
        except Exception as exc:
            raise RuntimeError(f"failed to get Azure token: {exc}") from exc

    def send_request(
        self,
        method: str,
        url: str,
        token: str,
        body: Dict[str, Any] | None = None,
    ) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
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

    def get_vm_power_state(self, token: str, subscription_id: str, resource_group: str, vm_name: str) -> str:
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/instanceView"
            f"?api-version={INSTANCE_VIEW_API}"
        )
        try:
            resp = self.send_request("GET", url, token)
        except Exception as exc:
            self.log(f"[WRN]: Could not get status for {vm_name}: {exc}")
            return "Unknown"

        if resp.status_code != 200:
            self.log(f"[WRN]: Could not get status for {vm_name}: HTTP {resp.status_code}")
            return "Unknown"

        try:
            payload = resp.json()
        except ValueError:
            return "Unknown"

        for status in payload.get("statuses", []):
            code = status.get("code", "")
            display = status.get("displayStatus", "")
            if code.startswith("PowerState/"):
                return display or code.split("/", 1)[-1]
        return "Unknown"

    @staticmethod
    def get_status_key(status_text: str) -> str:
        normalized = status_text.lower()
        if "running" in normalized:
            return "running"
        if "stopped" in normalized or "deallocated" in normalized:
            return "stopped"
        if "starting" in normalized or "stopping" in normalized or "updating" in normalized or "deleting" in normalized:
            return "transition"
        if "deleted" in normalized:
            return "deleted"
        return "unknown"

    def collect_vms(self, token: str) -> List[Dict[str, str]]:
        subscription_url = f"https://management.azure.com/subscriptions?api-version={SUBSCRIPTION_API}"
        resp = self.send_request("GET", subscription_url, token)
        if resp.status_code != 200:
            raise RuntimeError(f"unexpected status for subscriptions: {resp.status_code}")

        try:
            subs_resp = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"failed to parse subscriptions JSON: {exc}") from exc

        all_vms: List[Dict[str, str]] = []
        for sub in subs_resp.get("value", []):
            subscription_id = sub.get("subscriptionId")
            if not subscription_id:
                continue

            self.log(f"[INF]: Processing subscription {subscription_id}")
            vm_url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/providers/Microsoft.Compute/virtualMachines?api-version={VM_API}"
            )
            try:
                vm_resp = self.send_request("GET", vm_url, token)
            except Exception as exc:
                self.log(f"[ERR]: Failed to fetch VMs for {subscription_id}: {exc}")
                continue

            if vm_resp.status_code != 200:
                self.log(f"[ERR]: Unexpected status for VMs in {subscription_id}: {vm_resp.status_code}")
                continue

            try:
                vms_resp = vm_resp.json()
            except ValueError as exc:
                self.log(f"[ERR]: Failed to parse VMs JSON: {exc}")
                continue

            for vm in vms_resp.get("value", []):
                vm_id = vm.get("id", "")
                vm_name = vm.get("name", "")
                resource_group = self.parse_resource_group(vm_id)
                location = vm.get("location", "")
                hardware_profile = vm.get("properties", {}).get("hardwareProfile", {})
                storage_profile = vm.get("properties", {}).get("storageProfile", {})
                os_disk = storage_profile.get("osDisk", {})
                os_type = os_disk.get("osType", "")
                size = hardware_profile.get("vmSize", "")

                if not vm_name or not resource_group:
                    continue

                status_text = self.get_vm_power_state(token, subscription_id, resource_group, vm_name)
                status_key = self.get_status_key(status_text)
                all_vms.append(
                    {
                        "subscription_id": subscription_id,
                        "resource_group": resource_group,
                        "vm_name": vm_name,
                        "vm_id": vm_id,
                        "location": location or "—",
                        "size": size or "—",
                        "os_type": os_type or "—",
                        "status_text": status_text,
                        "status_key": status_key,
                    }
                )

        all_vms.sort(key=lambda x: (x["vm_name"].lower(), x["resource_group"].lower()))
        return all_vms

    def populate_tree(self, vms: List[Dict[str, str]]) -> None:
        selected_vm_id = None
        selection = self.tree.selection()
        if selection:
            selected_vm_id = self.vms[int(selection[0])].get("vm_id")

        def _populate() -> None:
            for item in self.tree.get_children():
                self.tree.delete(item)

            selected_iid = None
            for index, vm in enumerate(vms):
                self.tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    text="",
                    image=self.status_images.get(vm.get("status_key", "unknown"), self.status_images["unknown"]),
                    values=(
                        vm["status_text"],
                        vm["vm_name"],
                        vm["resource_group"],
                        vm["subscription_id"],
                    ),
                )
                if selected_vm_id and vm.get("vm_id") == selected_vm_id:
                    selected_iid = str(index)

            if vms:
                if selected_iid is None:
                    selected_iid = "0"
                self.tree.selection_set(selected_iid)
                self.tree.focus(selected_iid)
                self.tree.see(selected_iid)
                self._show_vm_details(vms[int(selected_iid)])
            else:
                self._clear_vm_details()

            self._sync_action_buttons()

        self.root.after(0, _populate)

    def update_single_vm_view(self, vm: Dict[str, str]) -> None:
        def _update() -> None:
            for index, item in enumerate(self.vms):
                if item.get("vm_id") == vm.get("vm_id"):
                    iid = str(index)
                    if self.tree.exists(iid):
                        self.tree.item(
                            iid,
                            image=self.status_images.get(vm.get("status_key", "unknown"), self.status_images["unknown"]),
                            values=(
                                vm["status_text"],
                                vm["vm_name"],
                                vm["resource_group"],
                                vm["subscription_id"],
                            ),
                        )
                    break

            selection = self.tree.selection()
            if selection and self.vms[int(selection[0])].get("vm_id") == vm.get("vm_id"):
                self._show_vm_details(vm)
            self._sync_action_buttons()

        self.root.after(0, _update)

    def load_vms(self) -> None:
        threading.Thread(target=self._load_vms_worker, daemon=True).start()

    def _load_vms_worker(self) -> None:
        self.set_buttons_loading(True)
        self.set_status("Loading VMs...")
        self.log("[INF]: Loading VM list from Azure...")
        try:
            self.token = self.get_azure_access_token()
            self.vms = self.collect_vms(self.token)
            self.populate_tree(self.vms)
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

    def get_selected_vm(self) -> Dict[str, str] | None:
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No Selection", "Please select a VM first.")
            return None
        return self.vms[int(selection[0])]

    def start_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            return
        if not messagebox.askyesno("Confirm Start", f"Start VM '{vm['vm_name']}' in resource group '{vm['resource_group']}'?"):
            return
        threading.Thread(target=self._start_vm_worker, args=(vm,), daemon=True).start()

    def stop_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            return
        if not messagebox.askyesno("Confirm Stop", f"Stop VM '{vm['vm_name']}' in resource group '{vm['resource_group']}'?"):
            return
        threading.Thread(target=self._stop_vm_worker, args=(vm,), daemon=True).start()

    def delete_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            return

        warning = (
            f"Delete VM '{vm['vm_name']}' in resource group '{vm['resource_group']}'?\n\n"
            f"This removes the VM resource itself. Attached disks, NICs, public IPs, and other resources may remain unless you delete them separately.\n\n"
            f"This action cannot be undone."
        )
        if not messagebox.askyesno("Confirm Delete", warning, icon="warning"):
            return

        threading.Thread(target=self._delete_vm_worker, args=(vm,), daemon=True).start()

    def _start_vm_worker(self, vm: Dict[str, str]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Starting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.start_vm(self.token, vm)
            vm["status_text"] = "Starting"
            vm["status_key"] = self.get_status_key(vm["status_text"])
            self.update_single_vm_view(vm)
            self.log(f"[INF]: VM {vm['vm_name']} start request accepted")
            self.set_status(f"Start requested for {vm['vm_name']}")
            threading.Thread(target=self._poll_vm_status_worker, args=(vm, "running"), daemon=True).start()
        except Exception as exc:
            self.set_status("Start failed")
            self.log(f"[ERR]: Failed to start selected VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Start Failed", str(exc)))
            self.set_buttons_loading(False)
        else:
            self.set_buttons_loading(False)

    def _stop_vm_worker(self, vm: Dict[str, str]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Stopping {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.stop_vm(self.token, vm)
            vm["status_text"] = "Stopping"
            vm["status_key"] = self.get_status_key(vm["status_text"])
            self.update_single_vm_view(vm)
            self.log(f"[INF]: VM {vm['vm_name']} stop request accepted")
            self.set_status(f"Stop requested for {vm['vm_name']}")
            threading.Thread(target=self._poll_vm_status_worker, args=(vm, "stopped"), daemon=True).start()
        except Exception as exc:
            self.set_status("Stop failed")
            self.log(f"[ERR]: Failed to stop selected VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Stop Failed", str(exc)))
            self.set_buttons_loading(False)
        else:
            self.set_buttons_loading(False)

    def _delete_vm_worker(self, vm: Dict[str, str]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Deleting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.delete_vm(self.token, vm)
            vm["status_text"] = "Deleting"
            vm["status_key"] = self.get_status_key(vm["status_text"])
            self.update_single_vm_view(vm)
            self.log(f"[INF]: VM {vm['vm_name']} delete request accepted")
            self.set_status(f"Delete requested for {vm['vm_name']}")
            threading.Thread(target=self._poll_vm_deleted_worker, args=(vm,), daemon=True).start()
        except Exception as exc:
            self.set_status("Delete failed")
            self.log(f"[ERR]: Failed to delete selected VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Delete Failed", str(exc)))
            self.set_buttons_loading(False)
        else:
            self.set_buttons_loading(False)

    def _poll_vm_status_worker(self, vm: Dict[str, str], expected_state: str, attempts: int = 40, delay_seconds: int = 5) -> None:
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            for _ in range(attempts):
                time.sleep(delay_seconds)
                latest = self.get_vm_power_state(self.token, vm["subscription_id"], vm["resource_group"], vm["vm_name"])
                vm["status_text"] = latest
                vm["status_key"] = self.get_status_key(latest)
                self.update_single_vm_view(vm)
                self.set_status(f"{vm['vm_name']}: {latest}")
                if vm["status_key"] == expected_state:
                    self.log(f"[INF]: VM {vm['vm_name']} reached state: {latest}")
                    return
            self.log(f"[WRN]: Timed out waiting for {vm['vm_name']} to reach state '{expected_state}'")
        except Exception as exc:
            self.log(f"[ERR]: Failed while polling status for {vm['vm_name']}: {exc}")

    def _poll_vm_deleted_worker(self, vm: Dict[str, str], attempts: int = 60, delay_seconds: int = 5) -> None:
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            for _ in range(attempts):
                time.sleep(delay_seconds)
                exists = self.vm_exists(self.token, vm)
                if not exists:
                    self.log(f"[INF]: VM {vm['vm_name']} no longer exists in Azure")
                    self._remove_vm_from_ui(vm)
                    self.set_status(f"Deleted {vm['vm_name']}")
                    return
                latest = self.get_vm_power_state(self.token, vm["subscription_id"], vm["resource_group"], vm["vm_name"])
                vm["status_text"] = f"Deleting ({latest})" if latest != "Unknown" else "Deleting"
                vm["status_key"] = "transition"
                self.update_single_vm_view(vm)
            self.log(f"[WRN]: Timed out waiting for VM deletion: {vm['vm_name']}")
        except Exception as exc:
            self.log(f"[ERR]: Failed while polling VM deletion for {vm['vm_name']}: {exc}")

    def _remove_vm_from_ui(self, vm: Dict[str, str]) -> None:
        def _remove() -> None:
            selected_vm_id = vm.get("vm_id")
            self.vms = [item for item in self.vms if item.get("vm_id") != selected_vm_id]
            self.populate_tree(self.vms)
            if not self.vms:
                self._clear_vm_details()

        self.root.after(0, _remove)

    def start_vm(self, token: str, vm: Dict[str, str]) -> None:
        subscription_id = vm["subscription_id"]
        resource_group = vm["resource_group"]
        vm_name = vm["vm_name"]
        start_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/start?api-version={VM_API}"
        )
        self.log(
            f"[DBG]: Sending POST request to start VM.\n"
            f"    SubscriptionID: {subscription_id}\n"
            f"    ResourceGroup: {resource_group}\n"
            f"    VM Name: {vm_name}\n"
            f"    URL: {start_url}"
        )
        start_resp = self.send_request("POST", start_url, token)
        if start_resp.status_code not in (200, 202):
            raise RuntimeError(
                f"unexpected status for starting VM {vm_name}: {start_resp.status_code}\nresponse body: {start_resp.text}"
            )

    def stop_vm(self, token: str, vm: Dict[str, str]) -> None:
        subscription_id = vm["subscription_id"]
        resource_group = vm["resource_group"]
        vm_name = vm["vm_name"]
        stop_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/powerOff?api-version={VM_API}"
        )
        self.log(
            f"[DBG]: Sending POST request to stop VM.\n"
            f"    SubscriptionID: {subscription_id}\n"
            f"    ResourceGroup: {resource_group}\n"
            f"    VM Name: {vm_name}\n"
            f"    URL: {stop_url}"
        )
        stop_resp = self.send_request("POST", stop_url, token)
        if stop_resp.status_code not in (200, 202):
            raise RuntimeError(
                f"unexpected status for stopping VM {vm_name}: {stop_resp.status_code}\nresponse body: {stop_resp.text}"
            )

    def delete_vm(self, token: str, vm: Dict[str, str]) -> None:
        subscription_id = vm["subscription_id"]
        resource_group = vm["resource_group"]
        vm_name = vm["vm_name"]
        delete_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}?api-version={VM_API}"
        )
        self.log(
            f"[DBG]: Sending DELETE request to remove VM.\n"
            f"    SubscriptionID: {subscription_id}\n"
            f"    ResourceGroup: {resource_group}\n"
            f"    VM Name: {vm_name}\n"
            f"    URL: {delete_url}"
        )
        delete_resp = self.send_request("DELETE", delete_url, token)
        if delete_resp.status_code not in (200, 202, 204):
            raise RuntimeError(
                f"unexpected status for deleting VM {vm_name}: {delete_resp.status_code}\nresponse body: {delete_resp.text}"
            )

    def vm_exists(self, token: str, vm: Dict[str, str]) -> bool:
        subscription_id = vm["subscription_id"]
        resource_group = vm["resource_group"]
        vm_name = vm["vm_name"]
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}?api-version={VM_API}"
        )
        resp = self.send_request("GET", url, token)
        if resp.status_code == 404:
            return False
        if resp.status_code == 200:
            return True
        raise RuntimeError(f"unexpected status while checking VM existence for {vm_name}: {resp.status_code}")


def main() -> None:
    root = tk.Tk()
    try:
        root.iconname("Azure VM Manager")
    except Exception:
        pass
    app = AzureVmStarterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
