#!/usr/bin/env python3

import base64
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional

import requests
from azure.identity import DefaultAzureCredential

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except Exception:
    Fernet = None
    InvalidToken = Exception
    PBKDF2HMAC = None
    hashes = None

SUBSCRIPTION_API = "2022-12-01"
VM_API = "2025-04-01"
INSTANCE_VIEW_API = "2025-04-01"
NETWORK_API = "2024-10-01"
AZURE_RESOURCE = "https://management.azure.com/.default"
POLL_SECONDS = 4
POLL_ATTEMPTS = 30
APP_SALT = b"azure-vm-manager-salt-v1"
CONFIG_DIR_NAME = "vm_configs"
SCHEDULE_DIR_NAME = "vm_schedules"


class AzureVmManagerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Azure VM Manager")
        self.root.geometry("1600x900")
        self.root.minsize(1320, 760)

        self.token: Optional[str] = None
        self.vms: List[Dict[str, Any]] = []
        self.status_images: Dict[str, tk.PhotoImage] = {}

        self.config_dir = Path.cwd() / CONFIG_DIR_NAME
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.schedule_dir = self.config_dir / SCHEDULE_DIR_NAME
        self.schedule_dir.mkdir(parents=True, exist_ok=True)
        self.current_config_path: Optional[Path] = None
        self.current_config_password: Optional[str] = None
        self.current_config_decrypted: Optional[Dict[str, Any]] = None
        self.current_schedule_path: Optional[Path] = None
        self.current_schedule_password: Optional[str] = None

        self._configure_styles()
        self._create_status_images()
        self._build_ui()
        self.refresh_config_file_list()
        self.refresh_schedule_file_list()

    def bring_to_front(self) -> None:
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.update_idletasks()
            self.root.focus_force()
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    # ---------- UI ----------
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

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Azure VM Manager", font=("Segoe UI", 16, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(header, textvariable=self.status_var).pack(side="right")

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        self.vm_tab = ttk.Frame(self.notebook, padding=8)
        self.schedule_tab = ttk.Frame(self.notebook, padding=8)
        self.config_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.vm_tab, text="VMs")
        self.notebook.add(self.schedule_tab, text="Schedules")
        self.notebook.add(self.config_tab, text="Config Files")

        self._build_vm_tab()
        self._build_schedule_tab()
        self._build_config_tab()

    def _build_vm_tab(self) -> None:
        controls = ttk.Frame(self.vm_tab)
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

        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)

        self.save_start_cfg_button = ttk.Button(controls, text="Save Start Config", command=self.save_start_config, state="disabled")
        self.save_start_cfg_button.pack(side="left")
        self.save_stop_cfg_button = ttk.Button(controls, text="Save Stop Config", command=self.save_stop_config, state="disabled")
        self.save_stop_cfg_button.pack(side="left", padx=(8, 0))
        self.save_inventory_cfg_button = ttk.Button(controls, text="Save VM Inventory", command=self.save_inventory_config, state="disabled")
        self.save_inventory_cfg_button.pack(side="left", padx=(8, 0))
        self.save_full_export_button = ttk.Button(controls, text="Save Full VM Export", command=self.save_full_vm_export, state="disabled")
        self.save_full_export_button.pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(self.vm_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        left_panel = ttk.Frame(content, padding=(0, 0, 8, 0))
        right_panel = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(left_panel, weight=3)
        content.add(right_panel, weight=2)

        ttk.Label(left_panel, text="Virtual Machines", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        tree_frame = ttk.Frame(left_panel)
        tree_frame.pack(fill="both", expand=True)

        vm_columns = ("status_text", "vm_name", "resource_group", "location", "size", "os_type", "subscription_id")
        self.tree = ttk.Treeview(tree_frame, columns=vm_columns, show="tree headings", height=18, selectmode="browse")
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

        vm_vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        vm_hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vm_vsb.set, xscrollcommand=vm_hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vm_vsb.grid(row=0, column=1, sticky="ns")
        vm_hsb.grid(row=1, column=0, sticky="ew")
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

    def _build_config_tab(self) -> None:
        top = ttk.Frame(self.config_tab)
        top.pack(fill="x", pady=(0, 10))

        self.config_dir_var = tk.StringVar(value=str(self.config_dir))
        ttk.Label(top, text="Config Folder:").pack(side="left")
        self.config_dir_entry = ttk.Entry(top, textvariable=self.config_dir_var)
        self.config_dir_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(top, text="Browse", command=self.choose_config_directory).pack(side="left")
        ttk.Button(top, text="Refresh Files", command=self.refresh_config_file_list).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="New JSON", command=self.new_config_document).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Open Selected", command=self.open_selected_config).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Delete Selected", command=self.delete_selected_config_file).pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(self.config_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content, padding=(0, 0, 8, 0))
        right = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(left, weight=2)
        content.add(right, weight=3)

        ttk.Label(left, text="Saved Config Files", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        files_frame = ttk.Frame(left)
        files_frame.pack(fill="both", expand=True)

        config_columns = ("file_name", "type", "encrypted", "modified")
        self.config_tree = ttk.Treeview(files_frame, columns=config_columns, show="headings", selectmode="browse")
        self.config_tree.heading("file_name", text="File Name")
        self.config_tree.heading("type", text="Type")
        self.config_tree.heading("encrypted", text="Encrypted")
        self.config_tree.heading("modified", text="Modified")
        self.config_tree.column("file_name", width=280, anchor="w")
        self.config_tree.column("type", width=160, anchor="w")
        self.config_tree.column("encrypted", width=90, anchor="center", stretch=False)
        self.config_tree.column("modified", width=170, anchor="w")
        cfg_vsb = ttk.Scrollbar(files_frame, orient="vertical", command=self.config_tree.yview)
        self.config_tree.configure(yscrollcommand=cfg_vsb.set)
        self.config_tree.grid(row=0, column=0, sticky="nsew")
        cfg_vsb.grid(row=0, column=1, sticky="ns")
        files_frame.rowconfigure(0, weight=1)
        files_frame.columnconfigure(0, weight=1)
        self.config_tree.bind("<<TreeviewSelect>>", self._on_config_file_selected)
        self.config_tree.bind("<Double-1>", lambda _event: self.open_selected_config())

        ttk.Label(left, text="Selected File Info", style="Section.TLabel").pack(anchor="w", pady=(12, 6))
        self.config_info_text = tk.Text(left, height=10, wrap="word")
        self.config_info_text.pack(fill="x", expand=False)
        self.config_info_text.configure(state="disabled")

        editor_bar = ttk.Frame(right)
        editor_bar.pack(fill="x", pady=(0, 6))
        ttk.Label(editor_bar, text="Config Editor", style="Section.TLabel").pack(side="left")
        ttk.Button(editor_bar, text="Open for Edit", command=self.open_selected_config).pack(side="right")
        ttk.Button(editor_bar, text="Save As Encrypted", command=self.save_current_config_as_encrypted).pack(side="right", padx=(0, 8))
        ttk.Button(editor_bar, text="Save", command=self.save_current_config).pack(side="right", padx=(0, 8))
        ttk.Button(editor_bar, text="Save As JSON", command=self.save_current_config_as_json).pack(side="right", padx=(0, 8))
        ttk.Button(editor_bar, text="Format JSON", command=self.format_current_config_json).pack(side="right", padx=(0, 8))
        ttk.Button(editor_bar, text="Validate JSON", command=self.validate_current_config_json).pack(side="right", padx=(0, 8))

        self.config_editor = tk.Text(right, wrap="none", undo=True)
        self.config_editor.pack(fill="both", expand=True)

        editor_scroll_y = ttk.Scrollbar(right, orient="vertical", command=self.config_editor.yview)
        editor_scroll_y.pack(side="right", fill="y")
        self.config_editor.configure(yscrollcommand=editor_scroll_y.set)

        bottom = ttk.Frame(self.config_tab)
        bottom.pack(fill="x", pady=(8, 0))
        self.config_status_var = tk.StringVar(value="No config file open")
        ttk.Label(bottom, textvariable=self.config_status_var).pack(side="left")
        ttk.Label(
            bottom,
            text="Tip: open a config, edit the JSON in the editor, then click Save.",
        ).pack(side="right")


    def _build_schedule_tab(self) -> None:
        controls = ttk.Frame(self.schedule_tab)
        controls.pack(fill="x", pady=(0, 10))

        ttk.Button(controls, text="New Schedule", command=self.new_schedule_document).pack(side="left")
        ttk.Button(controls, text="Load Selected VM", command=self.load_selected_vm_into_schedule).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Save Schedule", command=self.save_schedule_document).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Delete Schedule", command=self.delete_selected_schedule_file).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Run Selected Now", command=self.run_selected_schedule_now).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Register Task", command=self.register_selected_schedule_task).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Remove Task", command=self.unregister_selected_schedule_task).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Refresh", command=self.refresh_schedule_file_list).pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(self.schedule_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content, padding=(0, 0, 8, 0))
        right = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(left, weight=2)
        content.add(right, weight=3)

        ttk.Label(left, text="Saved Schedules", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        schedule_tree_frame = ttk.Frame(left)
        schedule_tree_frame.pack(fill="both", expand=True)

        schedule_columns = ("schedule_name", "vm_name", "action", "frequency", "time", "enabled")
        self.schedule_tree = ttk.Treeview(schedule_tree_frame, columns=schedule_columns, show="headings", selectmode="browse")
        self.schedule_tree.heading("schedule_name", text="Schedule")
        self.schedule_tree.heading("vm_name", text="VM")
        self.schedule_tree.heading("action", text="Action")
        self.schedule_tree.heading("frequency", text="Frequency")
        self.schedule_tree.heading("time", text="Time")
        self.schedule_tree.heading("enabled", text="Enabled")
        self.schedule_tree.column("schedule_name", width=220, anchor="w")
        self.schedule_tree.column("vm_name", width=170, anchor="w")
        self.schedule_tree.column("action", width=90, anchor="center", stretch=False)
        self.schedule_tree.column("frequency", width=100, anchor="center", stretch=False)
        self.schedule_tree.column("time", width=90, anchor="center", stretch=False)
        self.schedule_tree.column("enabled", width=80, anchor="center", stretch=False)
        sched_vsb = ttk.Scrollbar(schedule_tree_frame, orient="vertical", command=self.schedule_tree.yview)
        self.schedule_tree.configure(yscrollcommand=sched_vsb.set)
        self.schedule_tree.grid(row=0, column=0, sticky="nsew")
        sched_vsb.grid(row=0, column=1, sticky="ns")
        schedule_tree_frame.rowconfigure(0, weight=1)
        schedule_tree_frame.columnconfigure(0, weight=1)
        self.schedule_tree.bind("<<TreeviewSelect>>", self._on_schedule_selected)
        self.schedule_tree.bind("<Double-1>", lambda _event: self.open_selected_schedule())

        ttk.Label(left, text="Schedule File Info", style="Section.TLabel").pack(anchor="w", pady=(12, 6))
        self.schedule_info_text = tk.Text(left, height=10, wrap="word")
        self.schedule_info_text.pack(fill="x", expand=False)
        self.schedule_info_text.configure(state="disabled")

        ttk.Label(right, text="Schedule Editor", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(right, style="Card.TFrame", padding=12)
        form.pack(fill="x", expand=False)

        self.schedule_name_var = tk.StringVar()
        self.schedule_vm_var = tk.StringVar()
        self.schedule_action_var = tk.StringVar(value="start")
        self.schedule_frequency_var = tk.StringVar(value="weekly")
        self.schedule_time_var = tk.StringVar(value="08:00")
        self.schedule_enabled_var = tk.BooleanVar(value=True)
        self.schedule_notes_var = tk.StringVar(value="")

        ttk.Label(form, text="Schedule Name:", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.schedule_name_var).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="VM:", style="FieldLabel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.schedule_vm_combo = ttk.Combobox(form, textvariable=self.schedule_vm_var, state="readonly")
        self.schedule_vm_combo.grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Action:", style="FieldLabel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Combobox(form, textvariable=self.schedule_action_var, state="readonly", values=["start", "stop"]).grid(row=2, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Frequency:", style="FieldLabel.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=4)
        freq_combo = ttk.Combobox(form, textvariable=self.schedule_frequency_var, state="readonly", values=["daily", "weekly"])
        freq_combo.grid(row=3, column=1, sticky="ew", pady=4)
        freq_combo.bind("<<ComboboxSelected>>", lambda _event: self._sync_schedule_days_state())

        ttk.Label(form, text="Time (HH:MM):", style="FieldLabel.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.schedule_time_var).grid(row=4, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Days:", style="FieldLabel.TLabel").grid(row=5, column=0, sticky="nw", padx=(0, 10), pady=4)
        days_frame = ttk.Frame(form)
        days_frame.grid(row=5, column=1, sticky="w", pady=4)
        self.schedule_day_vars: Dict[str, tk.BooleanVar] = {}
        for idx, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            var = tk.BooleanVar(value=day in {"Mon", "Tue", "Wed", "Thu", "Fri"})
            self.schedule_day_vars[day] = var
            ttk.Checkbutton(days_frame, text=day, variable=var).grid(row=0, column=idx, padx=(0, 6))

        ttk.Checkbutton(form, text="Enabled", variable=self.schedule_enabled_var).grid(row=6, column=1, sticky="w", pady=4)

        ttk.Label(form, text="Notes:", style="FieldLabel.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.schedule_notes_var).grid(row=7, column=1, sticky="ew", pady=4)

        form.columnconfigure(1, weight=1)

        ttk.Label(right, text="Schedule JSON Preview", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
        self.schedule_json_text = tk.Text(right, height=16, wrap="word")
        self.schedule_json_text.pack(fill="both", expand=True)
        self.schedule_json_text.configure(state="disabled")

        bottom = ttk.Frame(self.schedule_tab)
        bottom.pack(fill="x", pady=(8, 0))
        self.schedule_status_var = tk.StringVar(value="No schedule selected")
        ttk.Label(bottom, textvariable=self.schedule_status_var).pack(side="left")
        self._sync_schedule_days_state()

    # ---------- Logging / state ----------
    def log(self, message: str) -> None:
        def _append() -> None:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(0, _append)

    def set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def set_config_status(self, message: str) -> None:
        self.root.after(0, lambda: self.config_status_var.set(message))

    def set_schedule_status(self, message: str) -> None:
        self.root.after(0, lambda: self.schedule_status_var.set(message))

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

    # ---------- Azure helpers ----------
    def get_azure_access_token(self) -> str:
        try:
            credential = DefaultAzureCredential()
            token = credential.get_token(AZURE_RESOURCE)
            return token.token
        except Exception as exc:
            raise RuntimeError(f"failed to get Azure token: {exc}") from exc

    def send_request(self, method: str, url: str, token: str, body: Dict[str, Any] | None = None) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            return requests.request(method=method, url=url, headers=headers, json=body, timeout=45)
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
    def parse_subscription_from_resource_id(resource_id: str) -> str:
        marker = "/subscriptions/"
        idx = resource_id.find(marker)
        if idx == -1:
            return ""
        remainder = resource_id[idx + len(marker):]
        slash_idx = remainder.find("/")
        return remainder if slash_idx == -1 else remainder[:slash_idx]

    def safe_get(self, token: str, url: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.send_request("GET", url, token)
            if response.status_code == 200:
                return response.json()
        except Exception as exc:
            self.log(f"[WRN]: GET failed for {url}: {exc}")
        return None

    def get_vm_instance_view(self, token: str, subscription_id: str, resource_group: str, vm_name: str) -> Dict[str, Any]:
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/instanceView"
            f"?api-version={INSTANCE_VIEW_API}"
        )
        response = self.send_request("GET", url, token)
        if response.status_code != 200:
            raise RuntimeError(f"unexpected status for VM instance view {vm_name}: {response.status_code}")
        return response.json()

    def map_power_state(self, instance_view: Optional[Dict[str, Any]]) -> tuple[str, str]:
        if not instance_view:
            return "unknown", "Unknown"
        for status in instance_view.get("statuses", []):
            code = str(status.get("code", "")).lower()
            display = status.get("displayStatus", "")
            if code.startswith("powerstate/"):
                if any(x in code for x in ["running"]):
                    return "running", display or "Running"
                if any(x in code for x in ["deallocated", "stopped"]):
                    return "stopped", display or "Stopped"
                if any(x in code for x in ["starting", "stopping", "deallocating"]):
                    return "transition", display or "Changing"
                return "unknown", display or code
        return "unknown", "Unknown"

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
                f"/providers/Microsoft.Compute/virtualMachines?api-version={VM_API}"
            )
            vm_resp = self.send_request("GET", vm_url, token)
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
                hardware = properties.get("hardwareProfile", {})
                storage = properties.get("storageProfile", {})
                os_disk = storage.get("osDisk", {})
                os_type = os_disk.get("osType", "Unknown")
                size = hardware.get("vmSize", "Unknown")
                location = vm.get("location", "")

                try:
                    instance_view = self.get_vm_instance_view(token, subscription_id, resource_group, vm_name)
                except Exception as exc:
                    self.log(f"[WRN]: Failed to get instance view for {vm_name}: {exc}")
                    instance_view = None
                status_key, status_text = self.map_power_state(instance_view)

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

    # ---------- VM tab helpers ----------
    def get_selected_vm(self) -> Optional[Dict[str, Any]]:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self.vms[int(selection[0])]
        except Exception:
            return None

    def _find_vm_index(self, vm_id: str) -> int:
        for idx, vm in enumerate(self.vms):
            if vm.get("vm_id") == vm_id:
                return idx
        return -1

    def _on_tree_select(self, _event: object = None) -> None:
        self.update_detail_panel()
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        selected = self.get_selected_vm()
        has_vms = bool(self.vms)
        self.save_inventory_cfg_button.configure(state="normal" if has_vms else "disabled")
        if not selected:
            for btn in [self.start_button, self.stop_button, self.delete_button, self.save_start_cfg_button, self.save_stop_cfg_button, self.save_full_export_button]:
                btn.configure(state="disabled")
            return

        self.delete_button.configure(state="normal")
        self.save_start_cfg_button.configure(state="normal")
        self.save_stop_cfg_button.configure(state="normal")
        self.save_full_export_button.configure(state="normal")

        status_key = selected.get("status_key", "unknown")
        if status_key == "running":
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
        elif status_key == "stopped":
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
        else:
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="disabled")

    def update_detail_panel(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            for key, var in self.detail_vars.items():
                var.set("—")
            self._set_text_widget(self.vm_id_text, "")
            return
        self.detail_vars["vm_name"].set(vm.get("vm_name", "—"))
        self.detail_vars["status"].set(vm.get("status_text", "—"))
        self.detail_vars["subscription_id"].set(vm.get("subscription_id", "—"))
        self.detail_vars["resource_group"].set(vm.get("resource_group", "—"))
        self.detail_vars["location"].set(vm.get("location", "—"))
        self.detail_vars["size"].set(vm.get("size", "—"))
        self.detail_vars["os_type"].set(vm.get("os_type", "—"))
        self.detail_vars["vm_id"].set(vm.get("vm_id", "—"))
        self._set_text_widget(self.vm_id_text, vm.get("vm_id", ""))

    def _set_text_widget(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

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
            if selected_vm_id and vm.get("vm_id") == selected_vm_id:
                self.tree.selection_set(str(index))
                self.tree.focus(str(index))
        self.update_detail_panel()
        self._sync_action_buttons()

    def update_single_vm_row(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.vms):
            return
        vm = self.vms[idx]
        if not self.tree.exists(str(idx)):
            self.populate_tree()
            return
        image = self.status_images.get(vm.get("status_key", "unknown"), self.status_images["unknown"])
        self.tree.item(str(idx), image=image, values=(
            vm.get("status_text", "Unknown"),
            vm.get("vm_name", ""),
            vm.get("resource_group", ""),
            vm.get("location", ""),
            vm.get("size", ""),
            vm.get("os_type", ""),
            vm.get("subscription_id", ""),
        ))
        selected = self.get_selected_vm()
        if selected and selected.get("vm_id") == vm.get("vm_id"):
            self.update_detail_panel()
            self._sync_action_buttons()


    # ---------- Schedule tab helpers ----------
    def _schedule_vm_display(self, vm: Dict[str, Any]) -> str:
        return f"{vm.get('vm_name', '')} | {vm.get('resource_group', '')} | {vm.get('subscription_id', '')}"

    def _refresh_schedule_vm_choices(self) -> None:
        values = [self._schedule_vm_display(vm) for vm in self.vms]
        self.schedule_vm_combo["values"] = values
        current = self.schedule_vm_var.get().strip()
        if current and current not in values:
            self.schedule_vm_var.set("")

    def _sync_schedule_days_state(self) -> None:
        state = "normal" if self.schedule_frequency_var.get() == "weekly" else "disabled"
        for child in self.schedule_tab.winfo_children():
            pass
        # checkbuttons are backed by the BooleanVars only; disabling is cosmetic here.

    def _iter_schedule_files(self) -> List[Path]:
        if not self.schedule_dir.exists():
            return []
        files = [p for p in self.schedule_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".encjson"}]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def _peek_schedule_file(self, file_path: Path) -> Dict[str, str]:
        result = {
            "file_name": file_path.name,
            "schedule_name": file_path.stem,
            "vm_name": "—",
            "action": "—",
            "frequency": "—",
            "time": "—",
            "enabled": "—",
            "encrypted": "Yes" if file_path.suffix.lower() == ".encjson" else "No",
        }
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
            if file_path.suffix.lower() == ".encjson":
                meta = raw.get("meta", {}) if isinstance(raw, dict) else {}
                result.update({
                    "schedule_name": meta.get("schedule_name", result["schedule_name"]),
                    "vm_name": meta.get("vm_name", result["vm_name"]),
                    "action": meta.get("action", result["action"]),
                    "frequency": meta.get("frequency", result["frequency"]),
                    "time": meta.get("time", result["time"]),
                    "enabled": "Yes" if meta.get("enabled", False) else "No",
                })
            else:
                schedule = raw.get("schedule", {}) if isinstance(raw, dict) else {}
                vm = raw.get("vm", {}) if isinstance(raw, dict) else {}
                result.update({
                    "schedule_name": raw.get("schedule_name", result["schedule_name"]),
                    "vm_name": vm.get("vm_name", result["vm_name"]),
                    "action": raw.get("action", result["action"]),
                    "frequency": schedule.get("frequency", result["frequency"]),
                    "time": schedule.get("time", result["time"]),
                    "enabled": "Yes" if raw.get("enabled", False) else "No",
                })
        except Exception:
            pass
        return result

    def refresh_schedule_file_list(self) -> None:
        if not hasattr(self, "schedule_tree"):
            return
        for item in self.schedule_tree.get_children():
            self.schedule_tree.delete(item)
        for idx, file_path in enumerate(self._iter_schedule_files()):
            info = self._peek_schedule_file(file_path)
            self.schedule_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    info["schedule_name"],
                    info["vm_name"],
                    info["action"],
                    info["frequency"],
                    info["time"],
                    info["enabled"],
                ),
            )

    def _get_selected_schedule_path(self) -> Optional[Path]:
        selection = self.schedule_tree.selection()
        if not selection:
            return None
        try:
            return self._iter_schedule_files()[int(selection[0])]
        except Exception:
            return None

    def _set_schedule_info_text(self, content: str) -> None:
        self.schedule_info_text.configure(state="normal")
        self.schedule_info_text.delete("1.0", "end")
        self.schedule_info_text.insert("1.0", content)
        self.schedule_info_text.configure(state="disabled")

    def _on_schedule_selected(self, _event: object = None) -> None:
        file_path = self._get_selected_schedule_path()
        if not file_path:
            self._set_schedule_info_text("")
            return
        info = self._peek_schedule_file(file_path)
        lines = [
            f"File: {file_path.name}",
            f"Encrypted: {info['encrypted']}",
            f"Schedule: {info['schedule_name']}",
            f"VM: {info['vm_name']}",
            f"Action: {info['action']}",
            f"Frequency: {info['frequency']}",
            f"Time: {info['time']}",
            f"Enabled: {info['enabled']}",
        ]
        self._set_schedule_info_text("\n".join(lines))
        self.set_schedule_status(f"Selected {file_path.name}")

    def _validate_schedule_time(self, value: str) -> str:
        parts = value.strip().split(":")
        if len(parts) != 2:
            raise RuntimeError("Time must be in HH:MM 24-hour format.")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise RuntimeError("Time must be in HH:MM 24-hour format.")
        return f"{hour:02d}:{minute:02d}"

    def _find_vm_by_display(self, display: str) -> Optional[Dict[str, Any]]:
        for vm in self.vms:
            if self._schedule_vm_display(vm) == display:
                return vm
        return None

    def _build_schedule_payload_from_form(self) -> Dict[str, Any]:
        name = self.schedule_name_var.get().strip()
        if not name:
            raise RuntimeError("Schedule name is required.")
        vm_display = self.schedule_vm_var.get().strip()
        vm = self._find_vm_by_display(vm_display)
        if not vm:
            raise RuntimeError("Choose a VM for the schedule.")
        action = self.schedule_action_var.get().strip().lower()
        if action not in {"start", "stop"}:
            raise RuntimeError("Action must be start or stop.")
        frequency = self.schedule_frequency_var.get().strip().lower()
        if frequency not in {"daily", "weekly"}:
            raise RuntimeError("Frequency must be daily or weekly.")
        time_value = self._validate_schedule_time(self.schedule_time_var.get())
        days = [day for day, var in self.schedule_day_vars.items() if var.get()]
        if frequency == "weekly" and not days:
            raise RuntimeError("Choose at least one day for a weekly schedule.")
        return {
            "config_type": "azure_vm_schedule",
            "schedule_name": name,
            "enabled": bool(self.schedule_enabled_var.get()),
            "action": action,
            "vm": {
                "subscription_id": vm.get("subscription_id"),
                "resource_group": vm.get("resource_group"),
                "vm_name": vm.get("vm_name"),
                "vm_id": vm.get("vm_id"),
                "location": vm.get("location"),
                "size": vm.get("size"),
                "os_type": vm.get("os_type"),
            },
            "schedule": {
                "frequency": frequency,
                "time": time_value,
                "days": days,
                "timezone": "local",
            },
            "execution": {
                "mode": "windows_task_scheduler",
                "registered_task_name": self._build_task_name(name),
            },
            "notes": self.schedule_notes_var.get().strip(),
        }

    def _set_schedule_json_preview(self, payload: Dict[str, Any]) -> None:
        self.schedule_json_text.configure(state="normal")
        self.schedule_json_text.delete("1.0", "end")
        self.schedule_json_text.insert("1.0", json.dumps(payload, indent=2, sort_keys=True))
        self.schedule_json_text.configure(state="disabled")

    def new_schedule_document(self) -> None:
        self.current_schedule_path = None
        self.current_schedule_password = None
        self.schedule_name_var.set("")
        self.schedule_vm_var.set("")
        self.schedule_action_var.set("start")
        self.schedule_frequency_var.set("weekly")
        self.schedule_time_var.set("08:00")
        self.schedule_enabled_var.set(True)
        self.schedule_notes_var.set("")
        for day, var in self.schedule_day_vars.items():
            var.set(day in {"Mon", "Tue", "Wed", "Thu", "Fri"})
        self._set_schedule_json_preview({"config_type": "azure_vm_schedule", "notes": "Build a schedule and save it."})
        self.set_schedule_status("New schedule")

    def load_selected_vm_into_schedule(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No VM Selected", "Select a VM on the VMs tab first.", parent=self.root)
            return
        self.notebook.select(self.schedule_tab)
        self._refresh_schedule_vm_choices()
        self.schedule_vm_var.set(self._schedule_vm_display(vm))
        if not self.schedule_name_var.get().strip():
            self.schedule_name_var.set(f"{vm.get('vm_name','vm')}-{self.schedule_action_var.get()}-schedule")
        try:
            self._set_schedule_json_preview(self._build_schedule_payload_from_form())
        except Exception:
            pass

    def _build_schedule_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        schedule = payload.get("schedule", {})
        vm = payload.get("vm", {})
        return {
            "schedule_name": payload.get("schedule_name", ""),
            "vm_name": vm.get("vm_name", ""),
            "action": payload.get("action", ""),
            "frequency": schedule.get("frequency", ""),
            "time": schedule.get("time", ""),
            "enabled": payload.get("enabled", False),
        }

    def _encrypt_json_payload_with_meta(self, payload: Dict[str, Any], password: str, meta: Dict[str, Any]) -> bytes:
        return self._encrypt_json_payload(payload, password, meta=meta)

    def save_schedule_document(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        self._set_schedule_json_preview(payload)
        password = self._prompt_for_encryption_password()
        if not password:
            return
        default_name = f"{payload['schedule_name'].replace(' ', '_')}.encjson"
        self.bring_to_front()
        file_path = filedialog.asksaveasfilename(
            title="Save Schedule",
            initialdir=str(self.schedule_dir),
            defaultextension=".encjson",
            initialfile=default_name,
            filetypes=[("Encrypted JSON", "*.encjson"), ("JSON", "*.json"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".json":
                path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                self.current_schedule_password = None
            else:
                path.write_bytes(self._encrypt_json_payload_with_meta(payload, password, self._build_schedule_meta(payload)))
                self.current_schedule_password = password
            self.current_schedule_path = path
            self.refresh_schedule_file_list()
            self.set_schedule_status(f"Saved {path.name}")
            self.bring_to_front()
            messagebox.showinfo("Saved", f"Schedule saved to:\n{path}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Save Failed", str(exc), parent=self.root)

    def open_selected_schedule(self) -> None:
        file_path = self._get_selected_schedule_path()
        if not file_path:
            return
        try:
            if file_path.suffix.lower() == ".encjson":
                self.bring_to_front()
                password = simpledialog.askstring("Schedule Password", f"Enter the password for:\n{file_path.name}", show="*", parent=self.root)
                if not password:
                    return
                payload = self._decrypt_json_file(file_path, password)
                self.current_schedule_password = password
            else:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                self.current_schedule_password = None
            self.current_schedule_path = file_path
            self._load_schedule_payload_into_form(payload)
            self.set_schedule_status(f"Opened {file_path.name}")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Open Failed", str(exc), parent=self.root)

    def _load_schedule_payload_into_form(self, payload: Dict[str, Any]) -> None:
        vm = payload.get("vm", {})
        vm_match = None
        for candidate in self.vms:
            if candidate.get("vm_id") == vm.get("vm_id"):
                vm_match = candidate
                break
        self.schedule_name_var.set(payload.get("schedule_name", ""))
        self.schedule_action_var.set(payload.get("action", "start"))
        schedule = payload.get("schedule", {})
        self.schedule_frequency_var.set(schedule.get("frequency", "weekly"))
        self.schedule_time_var.set(schedule.get("time", "08:00"))
        self.schedule_enabled_var.set(bool(payload.get("enabled", True)))
        self.schedule_notes_var.set(payload.get("notes", ""))
        for day, var in self.schedule_day_vars.items():
            var.set(day in set(schedule.get("days", [])))
        self._refresh_schedule_vm_choices()
        if vm_match:
            self.schedule_vm_var.set(self._schedule_vm_display(vm_match))
        else:
            self.schedule_vm_var.set(f"{vm.get('vm_name','')} | {vm.get('resource_group','')} | {vm.get('subscription_id','')}")
        self._set_schedule_json_preview(payload)

    def delete_selected_schedule_file(self) -> None:
        file_path = self._get_selected_schedule_path()
        if not file_path:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a schedule file first.", parent=self.root)
            return
        self.bring_to_front()
        if not messagebox.askyesno("Delete Schedule", f"Delete schedule file?\n\n{file_path}", parent=self.root):
            return
        try:
            file_path.unlink(missing_ok=False)
            if self.current_schedule_path == file_path:
                self.current_schedule_path = None
                self.current_schedule_password = None
            self.refresh_schedule_file_list()
            self.new_schedule_document()
            self.set_schedule_status(f"Deleted {file_path.name}")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Delete Failed", str(exc), parent=self.root)

    def _build_task_name(self, schedule_name: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in schedule_name.strip())
        return f"AzureVmManager_{safe}"

    def _build_task_scheduler_command(self, payload: Dict[str, Any]) -> List[str]:
        vm = payload.get("vm", {})
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--run-action",
            "--action", payload.get("action", "start"),
            "--subscription-id", vm.get("subscription_id", ""),
            "--resource-group", vm.get("resource_group", ""),
            "--vm-name", vm.get("vm_name", ""),
        ]

    def register_selected_schedule_task(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        task_name = self._build_task_name(payload["schedule_name"])
        schedule = payload.get("schedule", {})
        args = ["schtasks", "/Create", "/TN", task_name, "/TR", subprocess.list2cmdline(self._build_task_scheduler_command(payload)), "/ST", schedule.get("time", "08:00"), "/F"]
        frequency = schedule.get("frequency", "weekly")
        if frequency == "daily":
            args.extend(["/SC", "DAILY"])
        else:
            days = schedule.get("days", ["Mon", "Tue", "Wed", "Thu", "Fri"])
            day_map = {"Mon": "MON", "Tue": "TUE", "Wed": "WED", "Thu": "THU", "Fri": "FRI", "Sat": "SAT", "Sun": "SUN"}
            args.extend(["/SC", "WEEKLY", "/D", ",".join(day_map[d] for d in days if d in day_map)])
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "schtasks failed").strip())
            if not payload.get("enabled", True):
                subprocess.run(["schtasks", "/Change", "/TN", task_name, "/DISABLE"], capture_output=True, text=True, check=False)
            self.set_schedule_status(f"Registered task {task_name}")
            self.bring_to_front()
            messagebox.showinfo("Task Registered", f"Task Scheduler entry created:\n{task_name}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Task Registration Failed", str(exc), parent=self.root)

    def unregister_selected_schedule_task(self) -> None:
        file_path = self._get_selected_schedule_path()
        if not file_path:
            try:
                payload = self._build_schedule_payload_from_form()
            except Exception as exc:
                self.bring_to_front()
                messagebox.showerror("No Schedule", str(exc), parent=self.root)
                return
        else:
            try:
                if file_path.suffix.lower() == ".encjson":
                    self.bring_to_front()
                    password = simpledialog.askstring("Schedule Password", f"Enter the password for:\n{file_path.name}", show="*", parent=self.root)
                    if not password:
                        return
                    payload = self._decrypt_json_file(file_path, password)
                else:
                    payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.bring_to_front()
                messagebox.showerror("Open Failed", str(exc), parent=self.root)
                return
        task_name = self._build_task_name(payload.get("schedule_name", "schedule"))
        self.bring_to_front()
        if not messagebox.askyesno("Remove Task", f"Delete Task Scheduler entry?\n\n{task_name}", parent=self.root):
            return
        result = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            self.bring_to_front()
            messagebox.showerror("Remove Failed", (result.stderr or result.stdout or "schtasks failed").strip(), parent=self.root)
            return
        self.set_schedule_status(f"Removed task {task_name}")
        self.bring_to_front()
        messagebox.showinfo("Task Removed", f"Removed Task Scheduler entry:\n{task_name}", parent=self.root)

    def run_selected_schedule_now(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        vm = payload.get("vm", {})
        action = payload.get("action", "start")
        worker = self._start_vm_worker if action == "start" else self._stop_vm_worker
        threading.Thread(target=worker, args=(vm,), daemon=True).start()

    # ---------- VM actions ----------
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
            self.root.after(0, self._refresh_schedule_vm_choices)
            self.set_status(f"Loaded {len(self.vms)} VM(s)")
            self.log(f"[INF]: Loaded {len(self.vms)} VM(s)")
        except Exception as exc:
            self.set_status("Load failed")
            self.log(f"[ERR]: Failed to load VMs: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Load Failed", str(exc), parent=self.root))
        finally:
            self.set_buttons_loading(False)

    def start_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        self.bring_to_front()
        if not messagebox.askyesno("Confirm Start", f"Start VM '{vm['vm_name']}'?", parent=self.root):
            return
        threading.Thread(target=self._start_vm_worker, args=(vm,), daemon=True).start()

    def stop_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        self.bring_to_front()
        if not messagebox.askyesno("Confirm Stop", f"Stop VM '{vm['vm_name']}'?", parent=self.root):
            return
        threading.Thread(target=self._stop_vm_worker, args=(vm,), daemon=True).start()

    def delete_selected_vm(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        name = vm["vm_name"]
        rg = vm["resource_group"]
        if not messagebox.askyesno("Confirm Delete", f"Delete VM '{name}' in resource group '{rg}'?\n\nThis deletes the VM resource."):
            return
        threading.Thread(target=self._delete_vm_worker, args=(vm,), daemon=True).start()

    def _start_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Starting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.start_vm(self.token, vm)
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Starting"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_until(vm["vm_id"], {"running"})
        except Exception as exc:
            self.set_status("Start failed")
            self.log(f"[ERR]: Failed to start VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Start Failed", str(exc), parent=self.root))
        finally:
            self.set_buttons_loading(False)

    def _stop_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Stopping {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.stop_vm(self.token, vm)
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Stopping"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_until(vm["vm_id"], {"stopped"})
        except Exception as exc:
            self.set_status("Stop failed")
            self.log(f"[ERR]: Failed to stop VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Stop Failed", str(exc), parent=self.root))
        finally:
            self.set_buttons_loading(False)

    def _delete_vm_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Deleting {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            self.delete_vm_request(self.token, vm)
            idx = self._find_vm_index(vm["vm_id"])
            if idx >= 0:
                self.vms[idx]["status_key"] = "transition"
                self.vms[idx]["status_text"] = "Deleting"
                self.root.after(0, lambda idx=idx: self.update_single_vm_row(idx))
            self._poll_vm_deleted(vm["vm_id"])
        except Exception as exc:
            self.set_status("Delete failed")
            self.log(f"[ERR]: Failed to delete VM: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Delete Failed", str(exc), parent=self.root))
        finally:
            self.set_buttons_loading(False)

    def start_vm(self, token: str, vm: Dict[str, Any]) -> None:
        url = (
            f"https://management.azure.com/subscriptions/{vm['subscription_id']}"
            f"/resourceGroups/{vm['resource_group']}"
            f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}/start"
            f"?api-version={VM_API}"
        )
        response = self.send_request("POST", url, token)
        if response.status_code not in (200, 202):
            raise RuntimeError(f"unexpected status for starting VM {vm['vm_name']}: {response.status_code}\nresponse body: {response.text}")

    def stop_vm(self, token: str, vm: Dict[str, Any]) -> None:
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
                f"/providers/Microsoft.Compute/virtualMachines/{vm['vm_name']}?api-version={VM_API}"
            )
            try:
                response = self.send_request("GET", url, self.token or self.get_azure_access_token())
                if response.status_code == 404:
                    remove_idx = self._find_vm_index(vm_id)
                    if remove_idx >= 0:
                        removed_name = self.vms[remove_idx]["vm_name"]
                        del self.vms[remove_idx]
                        self.root.after(0, self.populate_tree)
                        self.root.after(0, self._refresh_schedule_vm_choices)
                        self.set_status(f"Deleted {removed_name}")
                    return
            except Exception as exc:
                self.log(f"[WRN]: Polling delete state for {vm['vm_name']} failed: {exc}")
        self.set_status(f"Timed out waiting for {vm['vm_name']} deletion")

    # ---------- Encryption helpers ----------
    def _ensure_crypto(self) -> bool:
        if Fernet is None or PBKDF2HMAC is None or hashes is None:
            messagebox.showerror("Missing Dependency", "Encrypted config support requires the 'cryptography' package.\n\nInstall it with:\npip install cryptography")
            return False
        return True

    def _derive_fernet(self, password: str) -> Fernet:
        assert PBKDF2HMAC is not None and hashes is not None and Fernet is not None
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=APP_SALT, iterations=390000)
        key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
        return Fernet(key)

    def _prompt_for_encryption_password(self) -> Optional[str]:
        if not self._ensure_crypto():
            return None
        self.bring_to_front()
        password = simpledialog.askstring("Encryption Password", "Enter a password to encrypt the config file:", show="*", parent=self.root)
        if password is None:
            return None
        password = password.strip()
        if not password:
            self.bring_to_front()
            messagebox.showwarning("Password Required", "Encryption password cannot be empty.", parent=self.root)
            return None
        self.bring_to_front()
        confirm = simpledialog.askstring("Confirm Password", "Re-enter the password:", show="*", parent=self.root)
        if confirm is None:
            return None
        if password != confirm:
            self.bring_to_front()
            messagebox.showerror("Password Mismatch", "The passwords did not match.", parent=self.root)
            return None
        return password

    def _encrypt_json_payload(self, payload: Dict[str, Any], password: str, meta: Optional[Dict[str, Any]] = None) -> bytes:
        fernet = self._derive_fernet(password)
        raw_json = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        encrypted_blob = fernet.encrypt(raw_json)
        wrapper = {
            "format": "azure-vm-manager-encrypted-json",
            "kdf": "PBKDF2HMAC-SHA256",
            "iterations": 390000,
            "payload": encrypted_blob.decode("utf-8"),
        }
        if meta:
            wrapper["meta"] = meta
        return json.dumps(wrapper, indent=2).encode("utf-8")

    def _decrypt_json_file(self, file_path: Path, password: str) -> Dict[str, Any]:
        if not self._ensure_crypto():
            raise RuntimeError("cryptography package is not available")
        wrapper = json.loads(file_path.read_text(encoding="utf-8"))
        if wrapper.get("format") != "azure-vm-manager-encrypted-json":
            raise RuntimeError("file is not in the expected encrypted config format")
        encrypted_blob = wrapper.get("payload", "")
        if not encrypted_blob:
            raise RuntimeError("encrypted payload was not found")
        fernet = self._derive_fernet(password)
        try:
            raw_json = fernet.decrypt(encrypted_blob.encode("utf-8"))
        except InvalidToken as exc:
            raise RuntimeError("password was incorrect or the file is corrupted") from exc
        return json.loads(raw_json.decode("utf-8"))

    def _save_encrypted_payload(self, payload: Dict[str, Any], suggested_name: str) -> None:
        password = self._prompt_for_encryption_password()
        if not password:
            return
        self.bring_to_front()
        file_path = filedialog.asksaveasfilename(
            title="Save Encrypted Config",
            defaultextension=".encjson",
            initialdir=str(self.config_dir),
            initialfile=suggested_name,
            filetypes=[("Encrypted JSON Config", "*.encjson"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        Path(file_path).write_bytes(self._encrypt_json_payload(payload, password))
        self.log(f"[INF]: Saved encrypted config: {file_path}")
        self.refresh_config_file_list()
        self.bring_to_front()
        messagebox.showinfo("Saved", f"Encrypted config saved to:\n{file_path}", parent=self.root)

    # ---------- Config payload builders ----------
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
            "notes": "Inventory export for automation and reference. This is not a full ARM/Bicep template.",
        }

    def save_start_config(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        self._save_encrypted_payload(self._build_vm_action_payload(vm, "start"), f"{vm['vm_name']}_start.encjson")

    def save_stop_config(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        self._save_encrypted_payload(self._build_vm_action_payload(vm, "stop"), f"{vm['vm_name']}_stop.encjson")

    def save_inventory_config(self) -> None:
        if not self.vms:
            self.bring_to_front()
            messagebox.showwarning("No VMs", "Load VMs first.", parent=self.root)
            return
        self._save_encrypted_payload(self._build_inventory_payload(), "azure_vm_inventory.encjson")

    def save_full_vm_export(self) -> None:
        vm = self.get_selected_vm()
        if not vm:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a VM first.", parent=self.root)
            return
        threading.Thread(target=self._save_full_vm_export_worker, args=(vm,), daemon=True).start()

    def _save_full_vm_export_worker(self, vm: Dict[str, Any]) -> None:
        self.set_buttons_loading(True)
        self.set_status(f"Exporting full config for {vm['vm_name']}...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            payload = self.get_full_vm_export_payload(self.token, vm)
            self.root.after(0, lambda p=payload, n=f"{vm['vm_name']}_full_export.encjson": self._save_encrypted_payload(p, n))
            self.set_status(f"Full export ready for {vm['vm_name']}")
        except Exception as exc:
            self.set_status("Full export failed")
            self.log(f"[ERR]: Failed to build full VM export: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Full Export Failed", str(exc), parent=self.root))
        finally:
            self.set_buttons_loading(False)

    def get_full_vm_export_payload(self, token: str, vm: Dict[str, Any]) -> Dict[str, Any]:
        sub = vm["subscription_id"]
        rg = vm["resource_group"]
        name = vm["vm_name"]
        vm_url = (
            f"https://management.azure.com/subscriptions/{sub}"
            f"/resourceGroups/{rg}/providers/Microsoft.Compute/virtualMachines/{name}"
            f"?api-version={VM_API}"
        )
        vm_response = self.send_request("GET", vm_url, token)
        if vm_response.status_code != 200:
            raise RuntimeError(f"unexpected status when reading VM resource: {vm_response.status_code}")
        vm_resource = vm_response.json()
        instance_view = self.get_vm_instance_view(token, sub, rg, name)

        properties = vm_resource.get("properties", {})
        storage_profile = properties.get("storageProfile", {})
        network_profile = properties.get("networkProfile", {})

        nic_details: List[Dict[str, Any]] = []
        for nic_ref in network_profile.get("networkInterfaces", []):
            nic_id = nic_ref.get("id")
            if not nic_id:
                continue
            nic_url = f"https://management.azure.com{nic_id}?api-version={NETWORK_API}"
            nic = self.safe_get(token, nic_url)
            nic_record: Dict[str, Any] = {"id": nic_id, "resource": nic}
            if nic:
                ip_configs = nic.get("properties", {}).get("ipConfigurations", [])
                nic_record["ip_configurations"] = ip_configs
                public_ips = []
                subnets = []
                for ip_cfg in ip_configs:
                    pub_ref = ip_cfg.get("properties", {}).get("publicIPAddress", {}).get("id")
                    subnet_ref = ip_cfg.get("properties", {}).get("subnet", {}).get("id")
                    if pub_ref:
                        public_ips.append(self.safe_get(token, f"https://management.azure.com{pub_ref}?api-version={NETWORK_API}"))
                    if subnet_ref:
                        subnet_url = f"https://management.azure.com{subnet_ref}?api-version={NETWORK_API}"
                        subnet_resource = self.safe_get(token, subnet_url)
                        vnet_id = subnet_ref.split("/subnets/")[0] if "/subnets/" in subnet_ref else None
                        vnet_resource = self.safe_get(token, f"https://management.azure.com{vnet_id}?api-version={NETWORK_API}") if vnet_id else None
                        subnets.append({"subnet": subnet_resource, "vnet": vnet_resource})
                nic_record["public_ip_resources"] = public_ips
                nic_record["subnet_resources"] = subnets
                nsg_id = nic.get("properties", {}).get("networkSecurityGroup", {}).get("id")
                if nsg_id:
                    nic_record["network_security_group"] = self.safe_get(token, f"https://management.azure.com{nsg_id}?api-version={NETWORK_API}")
            nic_details.append(nic_record)

        disk_details: Dict[str, Any] = {"os_disk": None, "data_disks": []}
        os_managed_disk_id = storage_profile.get("osDisk", {}).get("managedDisk", {}).get("id")
        if os_managed_disk_id:
            disk_details["os_disk"] = self.safe_get(token, f"https://management.azure.com{os_managed_disk_id}?api-version={VM_API}")
        for data_disk in storage_profile.get("dataDisks", []):
            managed_id = data_disk.get("managedDisk", {}).get("id")
            disk_details["data_disks"].append({
                "reference": data_disk,
                "resource": self.safe_get(token, f"https://management.azure.com{managed_id}?api-version={VM_API}") if managed_id else None,
            })

        recreation_hints = {
            "name": vm_resource.get("name"),
            "location": vm_resource.get("location"),
            "zones": vm_resource.get("zones"),
            "tags": vm_resource.get("tags"),
            "size": properties.get("hardwareProfile", {}).get("vmSize"),
            "os_type": storage_profile.get("osDisk", {}).get("osType"),
            "image_reference": storage_profile.get("imageReference"),
            "license_type": properties.get("licenseType"),
            "availability_set": properties.get("availabilitySet"),
            "proximity_placement_group": properties.get("proximityPlacementGroup"),
            "host": properties.get("host"),
            "priority": properties.get("priority"),
            "eviction_policy": properties.get("evictionPolicy"),
            "boot_diagnostics": properties.get("diagnosticsProfile", {}).get("bootDiagnostics"),
            "network_interfaces": network_profile.get("networkInterfaces"),
        }

        return {
            "config_type": "azure_vm_full_export",
            "created_by": "Azure VM Manager",
            "vm_summary": self._build_vm_action_payload(vm, "reference")["vm"],
            "full_export": {
                "vm_resource": vm_resource,
                "instance_view": instance_view,
                "network": nic_details,
                "disks": disk_details,
                "recreation_hints": recreation_hints,
            },
            "notes": "Strong reference export for review and recreation planning. Azure does not return secrets such as admin passwords.",
        }

    # ---------- Config tab ----------
    def choose_config_directory(self) -> None:
        self.bring_to_front()
        chosen = filedialog.askdirectory(title="Choose Config Folder", initialdir=str(self.config_dir), parent=self.root)
        if not chosen:
            return
        self.config_dir = Path(chosen)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.schedule_dir = self.config_dir / SCHEDULE_DIR_NAME
        self.schedule_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir_var.set(str(self.config_dir))
        self.refresh_config_file_list()
        self.refresh_schedule_file_list()

    def _iter_config_files(self) -> List[Path]:
        if not self.config_dir.exists():
            return []
        files = [p for p in self.config_dir.iterdir() if p.is_file() and p.suffix.lower() in {".json", ".encjson"}]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def _peek_config_file(self, file_path: Path) -> Dict[str, str]:
        info = {
            "file_name": file_path.name,
            "type": "Unknown",
            "encrypted": "Yes" if file_path.suffix.lower() == ".encjson" else "No",
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(file_path.stat().st_mtime)),
        }
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if file_path.suffix.lower() == ".encjson":
                info["type"] = payload.get("format", "Encrypted JSON")
            else:
                info["type"] = payload.get("config_type", "JSON")
        except Exception:
            pass
        return info

    def refresh_config_file_list(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir_var.set(str(self.config_dir))
        for item in self.config_tree.get_children():
            self.config_tree.delete(item)
        for idx, file_path in enumerate(self._iter_config_files()):
            info = self._peek_config_file(file_path)
            self.config_tree.insert("", "end", iid=str(idx), values=(info["file_name"], info["type"], info["encrypted"], info["modified"]))
        self._set_text_widget(self.config_info_text, f"Folder: {self.config_dir}\nFiles found: {len(self.config_tree.get_children())}")
        self.set_config_status("Config file list refreshed")

    def _get_selected_config_path(self) -> Optional[Path]:
        selection = self.config_tree.selection()
        if not selection:
            return None
        file_name = self.config_tree.item(selection[0], "values")[0]
        return self.config_dir / file_name

    def _on_config_file_selected(self, _event: object = None) -> None:
        file_path = self._get_selected_config_path()
        if not file_path:
            return
        info_lines = [
            f"Path: {file_path}",
            f"Size: {file_path.stat().st_size:,} bytes",
            f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(file_path.stat().st_mtime))}",
            f"Encrypted: {'Yes' if file_path.suffix.lower() == '.encjson' else 'No'}",
        ]
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            info_lines.append(f"Top-level type: {payload.get('config_type') or payload.get('format') or 'Unknown'}")
            info_lines.append(f"Top-level keys: {', '.join(list(payload.keys())[:12])}")
        except Exception as exc:
            info_lines.append(f"Preview error: {exc}")
        self._set_text_widget(self.config_info_text, "\n".join(info_lines))

    def open_selected_config(self) -> None:
        file_path = self._get_selected_config_path()
        if not file_path:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a config file first.", parent=self.root)
            return
        try:
            if file_path.suffix.lower() == ".encjson":
                self.bring_to_front()
                password = simpledialog.askstring("Decrypt Config", f"Enter password for:\n{file_path.name}", show="*", parent=self.root)
                if password is None:
                    return
                payload = self._decrypt_json_file(file_path, password)
                self.current_config_password = password
            else:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
                self.current_config_password = None
            self.current_config_path = file_path
            self.current_config_decrypted = payload
            self.config_editor.delete("1.0", "end")
            self.config_editor.insert("1.0", json.dumps(payload, indent=2, sort_keys=True))
            self.set_config_status(f"Opened {file_path.name}")
            self.notebook.select(self.config_tab)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Open Failed", str(exc), parent=self.root)
            self.set_config_status("Open failed")

    def _get_editor_json(self) -> Dict[str, Any]:
        text = self.config_editor.get("1.0", "end").strip()
        if not text:
            raise RuntimeError("editor is empty")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("top-level JSON must be an object")
        return parsed

    def validate_current_config_json(self) -> None:
        try:
            payload = self._get_editor_json()
            top_level_keys = ", ".join(list(payload.keys())[:12]) or "(none)"
            self.set_config_status("JSON is valid and ready to save")
            self.bring_to_front()
            messagebox.showinfo(
                "JSON Valid",
                f"JSON is valid.\n\nTop-level keys:\n{top_level_keys}",
                parent=self.root,
            )
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid JSON", str(exc), parent=self.root)

    def format_current_config_json(self) -> None:
        try:
            payload = self._get_editor_json()
            self.config_editor.delete("1.0", "end")
            self.config_editor.insert("1.0", json.dumps(payload, indent=2, sort_keys=True))
            self.set_config_status("Formatted JSON in editor")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Format Failed", str(exc), parent=self.root)

    def save_current_config_as_json(self) -> None:
        try:
            payload = self._get_editor_json()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid JSON", str(exc), parent=self.root)
            return

        default_name = (self.current_config_path.stem if self.current_config_path else "config") + ".json"
        self.bring_to_front()
        file_path = filedialog.asksaveasfilename(
            title="Save Config As JSON",
            initialdir=str(self.config_dir),
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return

        path = Path(file_path)
        try:
            path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            self.current_config_path = path
            self.current_config_password = None
            self.current_config_decrypted = payload
            self.refresh_config_file_list()
            self.set_config_status(f"Saved {path.name}")
            self.bring_to_front()
            messagebox.showinfo("Saved", f"Saved JSON to:\n{path}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Save Failed", str(exc), parent=self.root)

    def save_current_config(self) -> None:
        try:
            payload = self._get_editor_json()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid JSON", str(exc), parent=self.root)
            return
        if self.current_config_path is None:
            self._save_as_json_or_encrypted(payload)
            return
        try:
            if self.current_config_path.suffix.lower() == ".encjson":
                password = self.current_config_password
                if not password:
                    password = self._prompt_for_encryption_password()
                    if not password:
                        return
                self.current_config_path.write_bytes(self._encrypt_json_payload(payload, password))
                self.current_config_password = password
            else:
                self.current_config_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            self.current_config_decrypted = payload
            self.refresh_config_file_list()
            self.set_config_status(f"Saved {self.current_config_path.name}")
            self.bring_to_front()
            messagebox.showinfo("Saved", f"Saved:\n{self.current_config_path}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Save Failed", str(exc), parent=self.root)

    def save_current_config_as_encrypted(self) -> None:
        try:
            payload = self._get_editor_json()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid JSON", str(exc), parent=self.root)
            return
        default_name = (self.current_config_path.stem if self.current_config_path else "config") + ".encjson"
        self._save_encrypted_payload(payload, default_name)

    def _save_as_json_or_encrypted(self, payload: Dict[str, Any]) -> None:
        self.bring_to_front()
        file_path = filedialog.asksaveasfilename(
            title="Save Config",
            initialdir=str(self.config_dir),
            defaultextension=".json",
            initialfile="config.json",
            filetypes=[("JSON", "*.json"), ("Encrypted JSON", "*.encjson"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        path = Path(file_path)
        try:
            if path.suffix.lower() == ".encjson":
                password = self._prompt_for_encryption_password()
                if not password:
                    return
                path.write_bytes(self._encrypt_json_payload(payload, password))
                self.current_config_password = password
            else:
                path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
                self.current_config_password = None
            self.current_config_path = path
            self.current_config_decrypted = payload
            self.refresh_config_file_list()
            self.set_config_status(f"Saved {path.name}")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Save Failed", str(exc), parent=self.root)

    def delete_selected_config_file(self) -> None:
        file_path = self._get_selected_config_path()
        if not file_path:
            self.bring_to_front()
            messagebox.showwarning("No Selection", "Please select a config file first.", parent=self.root)
            return
        self.bring_to_front()
        if not messagebox.askyesno("Delete Config", f"Delete file?\n\n{file_path}", parent=self.root):
            return
        try:
            file_path.unlink(missing_ok=False)
            if self.current_config_path and self.current_config_path == file_path:
                self.current_config_path = None
                self.current_config_password = None
                self.current_config_decrypted = None
                self.config_editor.delete("1.0", "end")
            self.refresh_config_file_list()
            self.set_config_status(f"Deleted {file_path.name}")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Delete Failed", str(exc), parent=self.root)

    def new_config_document(self) -> None:
        template = {
            "config_type": "custom_json",
            "notes": "Edit this JSON and save it as .json or .encjson.",
        }
        self.current_config_path = None
        self.current_config_password = None
        self.current_config_decrypted = template
        self.config_editor.delete("1.0", "end")
        self.config_editor.insert("1.0", json.dumps(template, indent=2, sort_keys=True))
        self.set_config_status("New config document created")
        self.notebook.select(self.config_tab)



def run_cli_vm_action(action: str, subscription_id: str, resource_group: str, vm_name: str) -> int:
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token(AZURE_RESOURCE).token
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if action == "start":
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourceGroups/{resource_group}"
                f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/start"
                f"?api-version={VM_API}"
            )
            response = requests.post(url, headers=headers, timeout=45)
        elif action == "stop":
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourceGroups/{resource_group}"
                f"/providers/Microsoft.Compute/virtualMachines/{vm_name}/powerOff"
                f"?api-version={VM_API}"
            )
            response = requests.post(url, headers=headers, timeout=45)
        else:
            print(f"Unsupported action: {action}", file=sys.stderr)
            return 2
        if response.status_code not in (200, 202):
            print(f"Action failed: {response.status_code} {response.text}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"CLI action failed: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--run-action":
        args = sys.argv[2:]
        def get_arg(flag: str) -> str:
            if flag not in args:
                return ""
            idx = args.index(flag)
            return args[idx + 1] if idx + 1 < len(args) else ""
        sys.exit(run_cli_vm_action(
            get_arg("--action"),
            get_arg("--subscription-id"),
            get_arg("--resource-group"),
            get_arg("--vm-name"),
        ))
    root = tk.Tk()
    app = AzureVmManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
