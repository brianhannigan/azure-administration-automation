#!/usr/bin/env python3

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta, timezone
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
RESOURCE_API = "2025-04-01"
COMPUTE_SCHEDULE_API = "2026-03-01-preview"
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
        self.deploy_tab = ttk.Frame(self.notebook, padding=8)
        self.config_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.vm_tab, text="VMs")
        self.notebook.add(self.schedule_tab, text="Schedules")
        self.notebook.add(self.deploy_tab, text="Deploy VM")
        self.notebook.add(self.config_tab, text="Config Files")

        self._build_vm_tab()
        self._build_schedule_tab()
        self._build_deploy_tab()
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

    def _build_deploy_tab(self) -> None:
        controls = ttk.Frame(self.deploy_tab)
        controls.pack(fill="x", pady=(0, 10))

        ttk.Button(controls, text="Import ARM Template", command=self.import_arm_template_file).pack(side="left")
        ttk.Button(controls, text="Import Parameters", command=self.import_arm_parameters_file).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Apply Template Metadata", command=self.apply_deploy_template_metadata).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Analyze VM Template", command=self.analyze_deploy_template).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Add Missing VM Pieces", command=self.add_missing_vm_template_pieces).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Load Resource Groups", command=self.load_deploy_resource_groups).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Format Template", command=self.format_deploy_template_editor).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Format Parameters", command=self.format_deploy_parameters_editor).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Preview Changes", command=self.preview_deploy_changes).pack(side="left", padx=(12, 0))
        ttk.Button(controls, text="Validate", command=self.validate_deploy_template).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Deploy", command=self.deploy_arm_template).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="View Deployment Result", command=self.view_deployment_result).pack(side="left", padx=(8, 0))

        content = ttk.Panedwindow(self.deploy_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content, padding=(0, 0, 8, 0))
        right = ttk.Frame(content, padding=(8, 0, 0, 0))
        content.add(left, weight=2)
        content.add(right, weight=3)

        ttk.Label(left, text="Deployment Settings", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        form = ttk.Frame(left, style="Card.TFrame", padding=12)
        form.pack(fill="x", expand=False)

        self.deploy_template_path_var = tk.StringVar(value="")
        self.deploy_parameters_path_var = tk.StringVar(value="")
        self.deploy_name_var = tk.StringVar(value="vmdeploy-001")
        self.deploy_subscription_var = tk.StringVar(value="")
        self.deploy_resource_group_var = tk.StringVar(value="")
        self.deploy_location_var = tk.StringVar(value="eastus")
        self.deploy_mode_var = tk.StringVar(value="Incremental")
        self.deploy_template_schema_var = tk.StringVar(value="—")
        self.deploy_status_var = tk.StringVar(value="Import an ARM template to begin")
        self.deploy_rg_status_var = tk.StringVar(value="Resource groups not loaded")
        self.deploy_create_rg_if_missing_var = tk.BooleanVar(value=False)
        self.deploy_last_result_text = "No deployment result yet."
        self.deploy_last_preview_text = "No preview changes yet."
        self.deploy_last_analysis_text = "No template analysis yet."
        self._deploy_template_after_id: Optional[str] = None
        self._deploy_parameters_after_id: Optional[str] = None

        ttk.Label(form, text="Deployment Name:", style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.deploy_name_var).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Subscription:", style="FieldLabel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.deploy_subscription_combo = ttk.Combobox(form, textvariable=self.deploy_subscription_var, state="readonly")
        self.deploy_subscription_combo.grid(row=1, column=1, sticky="ew", pady=4)
        self.deploy_subscription_combo.bind("<<ComboboxSelected>>", self._on_deploy_subscription_changed)

        ttk.Label(form, text="Resource Group:", style="FieldLabel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        self.deploy_resource_group_combo = ttk.Combobox(form, textvariable=self.deploy_resource_group_var)
        self.deploy_resource_group_combo.grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(form, textvariable=self.deploy_rg_status_var).grid(row=3, column=1, sticky="w", pady=(0, 4))
        ttk.Checkbutton(
            form,
            text="Create resource group if missing",
            variable=self.deploy_create_rg_if_missing_var,
        ).grid(row=4, column=1, sticky="w", pady=(0, 4))

        ttk.Label(form, text="Location:", style="FieldLabel.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.deploy_location_var).grid(row=5, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Mode:", style="FieldLabel.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Combobox(form, textvariable=self.deploy_mode_var, state="readonly", values=["Incremental", "Complete"]).grid(row=6, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Template File:", style="FieldLabel.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.deploy_template_path_var, state="readonly").grid(row=7, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Parameters File:", style="FieldLabel.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.deploy_parameters_path_var, state="readonly").grid(row=8, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Template Schema:", style="FieldLabel.TLabel").grid(row=9, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Label(form, textvariable=self.deploy_template_schema_var, wraplength=320, justify="left").grid(row=9, column=1, sticky="w", pady=4)
        form.columnconfigure(1, weight=1)

        self.deploy_info_notebook = ttk.Notebook(left)
        self.deploy_info_notebook.pack(fill="both", expand=True, pady=(12, 0))

        analysis_tab = ttk.Frame(self.deploy_info_notebook, padding=4)
        param_tab = ttk.Frame(self.deploy_info_notebook, padding=4)
        preview_tab = ttk.Frame(self.deploy_info_notebook, padding=4)
        result_tab = ttk.Frame(self.deploy_info_notebook, padding=4)
        self.deploy_info_notebook.add(analysis_tab, text="Template Analysis")
        self.deploy_info_notebook.add(param_tab, text="Template Parameters")
        self.deploy_info_notebook.add(preview_tab, text="Preview Changes")
        self.deploy_info_notebook.add(result_tab, text="Deployment Result")

        self.deploy_analysis_text = tk.Text(analysis_tab, height=18, wrap="word")
        self.deploy_analysis_text.pack(fill="both", expand=True)
        self.deploy_analysis_text.configure(state="disabled")
        self._set_text_widget(self.deploy_analysis_text, self.deploy_last_analysis_text)

        self.deploy_param_summary_text = tk.Text(param_tab, height=18, wrap="word")
        self.deploy_param_summary_text.pack(fill="both", expand=True)
        self.deploy_param_summary_text.configure(state="disabled")

        self.deploy_preview_text = tk.Text(preview_tab, height=18, wrap="word")
        self.deploy_preview_text.pack(fill="both", expand=True)
        self.deploy_preview_text.configure(state="disabled")
        self._set_text_widget(self.deploy_preview_text, self.deploy_last_preview_text)

        self.deploy_result_text = tk.Text(result_tab, height=18, wrap="word")
        self.deploy_result_text.pack(fill="both", expand=True)
        self.deploy_result_text.configure(state="disabled")
        self._set_text_widget(self.deploy_result_text, self.deploy_last_result_text)

        right_notebook = ttk.Notebook(right)
        right_notebook.pack(fill="both", expand=True)

        template_frame = ttk.Frame(right_notebook, padding=4)
        params_frame = ttk.Frame(right_notebook, padding=4)
        right_notebook.add(template_frame, text="ARM Template JSON")
        right_notebook.add(params_frame, text="Parameters JSON")

        self.deploy_template_text = tk.Text(template_frame, wrap="none", undo=True)
        self.deploy_template_text.pack(side="left", fill="both", expand=True)
        template_v = ttk.Scrollbar(template_frame, orient="vertical", command=self.deploy_template_text.yview)
        template_v.pack(side="right", fill="y")
        self.deploy_template_text.configure(yscrollcommand=template_v.set)

        self.deploy_parameters_text = tk.Text(params_frame, wrap="none", undo=True)
        self.deploy_parameters_text.pack(side="left", fill="both", expand=True)
        params_v = ttk.Scrollbar(params_frame, orient="vertical", command=self.deploy_parameters_text.yview)
        params_v.pack(side="right", fill="y")
        self.deploy_parameters_text.configure(yscrollcommand=params_v.set)

        self.deploy_template_text.bind("<KeyRelease>", self._schedule_deploy_template_sync)
        self.deploy_template_text.bind("<<Paste>>", self._schedule_deploy_template_sync)
        self.deploy_parameters_text.bind("<KeyRelease>", self._schedule_deploy_parameters_sync)
        self.deploy_parameters_text.bind("<<Paste>>", self._schedule_deploy_parameters_sync)

        bottom = ttk.Frame(self.deploy_tab)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, textvariable=self.deploy_status_var).pack(side="left")
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
        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(controls, text="Deploy to Azure", command=self.deploy_selected_schedule_to_azure).pack(side="left")
        ttk.Button(controls, text="Disable Azure Schedule", command=self.disable_selected_azure_schedule).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Enable Azure Schedule", command=self.enable_selected_azure_schedule).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Remove Azure Schedule", command=self.remove_selected_azure_schedule).pack(side="left", padx=(8, 0))
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
        self.schedule_timezone_var = tk.StringVar(value="UTC")
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

        ttk.Label(form, text="Time Zone:", style="FieldLabel.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.schedule_timezone_var).grid(row=5, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Days:", style="FieldLabel.TLabel").grid(row=6, column=0, sticky="nw", padx=(0, 10), pady=4)
        days_frame = ttk.Frame(form)
        days_frame.grid(row=6, column=1, sticky="w", pady=4)
        self.schedule_day_vars: Dict[str, tk.BooleanVar] = {}
        for idx, day in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            var = tk.BooleanVar(value=day in {"Mon", "Tue", "Wed", "Thu", "Fri"})
            self.schedule_day_vars[day] = var
            ttk.Checkbutton(days_frame, text=day, variable=var).grid(row=0, column=idx, padx=(0, 6))

        ttk.Checkbutton(form, text="Enabled", variable=self.schedule_enabled_var).grid(row=7, column=1, sticky="w", pady=4)

        ttk.Label(form, text="Notes:", style="FieldLabel.TLabel").grid(row=8, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(form, textvariable=self.schedule_notes_var).grid(row=8, column=1, sticky="ew", pady=4)

        ttk.Label(form, text="Azure note:", style="FieldLabel.TLabel").grid(row=9, column=0, sticky="nw", padx=(0, 10), pady=4)
        ttk.Label(form, text="Azure deployment uses Start for start schedules and Deallocate for stop schedules.", wraplength=520, justify="left").grid(row=9, column=1, sticky="w", pady=4)

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
                "timezone": self.schedule_timezone_var.get().strip() or "UTC",
            },
            "execution": {
                "mode": "windows_task_scheduler",
                "registered_task_name": self._build_task_name(name),
                "azure": {
                    "api_version": COMPUTE_SCHEDULE_API,
                    "deployment_mode": "compute_schedule_preview",
                    "scheduled_action_name": self._build_azure_scheduled_action_name(name),
                    "subscription_id": vm.get("subscription_id"),
                    "resource_group": vm.get("resource_group"),
                    "vm_resource_id": vm.get("vm_id"),
                    "deployed": False,
                    "disabled": not bool(self.schedule_enabled_var.get()),
                },
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
        self.schedule_timezone_var.set("UTC")
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
        self.schedule_timezone_var.set(schedule.get("timezone", "UTC"))
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

    def _get_task_wrapper_root(self) -> Path:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        wrapper_root = Path(base) / "AzureVmManager" / "task_wrappers"
        wrapper_root.mkdir(parents=True, exist_ok=True)
        return wrapper_root

    def _get_task_wrapper_path(self, task_name: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_name)
        digest = hashlib.sha1(task_name.encode("utf-8")).hexdigest()[:10]
        short_name = f"{safe_name[:40]}_{digest}.cmd"
        return self._get_task_wrapper_root() / short_name

    def _write_task_wrapper(self, task_name: str, payload: Dict[str, Any]) -> Path:
        wrapper_path = self._get_task_wrapper_path(task_name)
        quoted_command = subprocess.list2cmdline(self._build_task_scheduler_command(payload))
        lines = [
            "@echo off",
            "setlocal",
            quoted_command,
            "set EXIT_CODE=%ERRORLEVEL%",
            "endlocal & exit /b %EXIT_CODE%",
            "",
        ]
        wrapper_path.write_text("\r\n".join(lines), encoding="utf-8")
        return wrapper_path

    def register_selected_schedule_task(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        task_name = self._build_task_name(payload["schedule_name"])
        schedule = payload.get("schedule", {})
        wrapper_path = self._write_task_wrapper(task_name, payload)
        args = ["schtasks", "/Create", "/TN", task_name, "/TR", str(wrapper_path), "/ST", schedule.get("time", "08:00"), "/F"]
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

    def _build_azure_scheduled_action_name(self, schedule_name: str) -> str:
        base = "".join(ch.lower() if ch.isalnum() else "-" for ch in schedule_name.strip())
        while "--" in base:
            base = base.replace("--", "-")
        base = base.strip("-")
        if len(base) < 3:
            base = (base + "-vm")[:3]
        return base[:24]

    def _azure_weekdays(self, days: List[str]) -> List[str]:
        mapping = {
            "Mon": "Monday",
            "Tue": "Tuesday",
            "Wed": "Wednesday",
            "Thu": "Thursday",
            "Fri": "Friday",
            "Sat": "Saturday",
            "Sun": "Sunday",
        }
        return [mapping[d] for d in days if d in mapping]

    def _azure_action_for_schedule(self, payload: Dict[str, Any]) -> str:
        action = str(payload.get("action", "start")).lower()
        return "Start" if action == "start" else "Deallocate"

    def _azure_schedule_url(self, subscription_id: str, resource_group: str, action_name: str) -> str:
        return (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.ComputeSchedule/scheduledActions/{action_name}"
            f"?api-version={COMPUTE_SCHEDULE_API}"
        )

    def _azure_associated_schedules_url(self, vm_id: str) -> str:
        return f"https://management.azure.com{vm_id}/providers/Microsoft.ComputeSchedule/associatedScheduledActions?api-version={COMPUTE_SCHEDULE_API}"

    def _azure_attach_resources_url(self, subscription_id: str, resource_group: str, action_name: str) -> str:
        return (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.ComputeSchedule/scheduledActions/{action_name}/attachResources"
            f"?api-version={COMPUTE_SCHEDULE_API}"
        )

    def _azure_enable_disable_url(self, subscription_id: str, resource_group: str, action_name: str, enable: bool) -> str:
        verb = "enable" if enable else "disable"
        return (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{resource_group}"
            f"/providers/Microsoft.ComputeSchedule/scheduledActions/{action_name}/{verb}"
            f"?api-version={COMPUTE_SCHEDULE_API}"
        )

    def _normalize_schedule_for_azure(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.loads(json.dumps(payload))
        execution = payload.setdefault("execution", {})
        azure = execution.setdefault("azure", {})
        vm = payload.get("vm", {})
        azure.setdefault("api_version", COMPUTE_SCHEDULE_API)
        azure.setdefault("deployment_mode", "compute_schedule_preview")
        azure.setdefault("scheduled_action_name", self._build_azure_scheduled_action_name(payload.get("schedule_name", "schedule")))
        azure.setdefault("subscription_id", vm.get("subscription_id"))
        azure.setdefault("resource_group", vm.get("resource_group"))
        azure.setdefault("vm_resource_id", vm.get("vm_id"))
        azure.setdefault("deployed", False)
        azure.setdefault("disabled", not bool(payload.get("enabled", True)))
        return payload

    def _build_azure_schedule_body(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._normalize_schedule_for_azure(payload)
        vm = payload.get("vm", {})
        schedule = payload.get("schedule", {})
        now = datetime.now(timezone.utc)
        start_time = now.replace(second=0, microsecond=0)
        end_time = start_time + timedelta(days=3650)
        body = {
            "location": vm.get("location") or "eastus",
            "properties": {
                "resourceType": "VirtualMachine",
                "actionType": self._azure_action_for_schedule(payload),
                "startTime": start_time.isoformat().replace("+00:00", "Z"),
                "endTime": end_time.isoformat().replace("+00:00", "Z"),
                "schedule": {
                    "scheduledTime": f"{schedule.get('time', '08:00')}:00",
                    "timeZone": schedule.get("timezone", "UTC") or "UTC",
                },
                "disabled": not bool(payload.get("enabled", True)),
            },
            "tags": {
                "managed-by": "AzureVmManagerApp",
                "vm-name": vm.get("vm_name", ""),
                "vm-resource-id": vm.get("vm_id", ""),
                "schedule-name": payload.get("schedule_name", ""),
            },
        }
        frequency = str(schedule.get("frequency", "weekly")).lower()
        if frequency == "weekly":
            body["properties"]["schedule"]["requestedWeekDays"] = self._azure_weekdays(schedule.get("days", []))
        return body

    def _save_schedule_payload_back_to_current_path(self, payload: Dict[str, Any]) -> None:
        path = self.current_schedule_path
        if not path:
            return
        if path.suffix.lower() == ".encjson":
            if not self.current_schedule_password:
                return
            path.write_bytes(self._encrypt_json_payload_with_meta(payload, self.current_schedule_password, self._build_schedule_meta(payload)))
        else:
            path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self.refresh_schedule_file_list()
        self._set_schedule_json_preview(payload)

    def _deploy_schedule_to_azure_worker(self, payload: Dict[str, Any]) -> None:
        self.set_schedule_status("Deploying schedule to Azure...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            payload = self._normalize_schedule_for_azure(payload)
            azure = payload["execution"]["azure"]
            vm = payload.get("vm", {})
            action_name = azure["scheduled_action_name"]
            subscription_id = azure.get("subscription_id") or vm.get("subscription_id")
            resource_group = azure.get("resource_group") or vm.get("resource_group")
            vm_id = azure.get("vm_resource_id") or vm.get("vm_id")
            if not subscription_id or not resource_group or not vm_id:
                raise RuntimeError("Missing Azure schedule deployment values.")

            body = self._build_azure_schedule_body(payload)
            create_resp = self.send_request("PUT", self._azure_schedule_url(subscription_id, resource_group, action_name), self.token, body)
            if create_resp.status_code not in (200, 201, 202):
                raise RuntimeError(f"Azure schedule create failed: {create_resp.status_code}\n{create_resp.text}")

            attach_body = {"resources": [{"resourceId": vm_id, "type": "Microsoft.Compute/virtualMachines"}]}
            attach_resp = self.send_request("POST", self._azure_attach_resources_url(subscription_id, resource_group, action_name), self.token, attach_body)
            if attach_resp.status_code not in (200, 202):
                raise RuntimeError(f"Azure schedule attach failed: {attach_resp.status_code}\n{attach_resp.text}")

            azure.update({
                "deployed": True,
                "disabled": not bool(payload.get("enabled", True)),
                "scheduled_action_id": f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.ComputeSchedule/scheduledActions/{action_name}",
                "last_deployed_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
            self._save_schedule_payload_back_to_current_path(payload)
            self.set_schedule_status(f"Azure schedule deployed: {action_name}")
            self.bring_to_front()
            self.root.after(0, lambda: messagebox.showinfo("Azure Schedule Deployed", f"Created Azure scheduled action:\n{action_name}", parent=self.root))
        except Exception as exc:
            self.set_schedule_status("Azure deploy failed")
            self.bring_to_front()
            self.root.after(0, lambda exc=exc: messagebox.showerror("Azure Deploy Failed", str(exc), parent=self.root))

    def deploy_selected_schedule_to_azure(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        payload = self._normalize_schedule_for_azure(payload)
        self._set_schedule_json_preview(payload)
        self.bring_to_front()
        if not messagebox.askyesno(
            "Deploy to Azure",
            "Create or update an Azure-side schedule for this VM?\n\nNote: stop schedules are deployed as Azure Deallocate actions.",
            parent=self.root,
        ):
            return
        threading.Thread(target=self._deploy_schedule_to_azure_worker, args=(payload,), daemon=True).start()

    def _toggle_selected_azure_schedule(self, enable: bool) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
            payload = self._normalize_schedule_for_azure(payload)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            azure = payload["execution"]["azure"]
            subscription_id = azure.get("subscription_id") or payload.get("vm", {}).get("subscription_id")
            resource_group = azure.get("resource_group") or payload.get("vm", {}).get("resource_group")
            action_name = azure.get("scheduled_action_name")
            resp = self.send_request("POST", self._azure_enable_disable_url(subscription_id, resource_group, action_name, enable), self.token)
            if resp.status_code != 200:
                raise RuntimeError(f"Azure schedule {'enable' if enable else 'disable'} failed: {resp.status_code}\n{resp.text}")
            payload["enabled"] = enable
            payload["execution"]["azure"]["disabled"] = not enable
            self.schedule_enabled_var.set(enable)
            self._save_schedule_payload_back_to_current_path(payload)
            self._set_schedule_json_preview(payload)
            self.set_schedule_status(f"Azure schedule {'enabled' if enable else 'disabled'}: {action_name}")
            self.bring_to_front()
            messagebox.showinfo("Azure Schedule Updated", f"Azure schedule {'enabled' if enable else 'disabled'}:\n{action_name}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Azure Schedule Update Failed", str(exc), parent=self.root)

    def enable_selected_azure_schedule(self) -> None:
        self._toggle_selected_azure_schedule(True)

    def disable_selected_azure_schedule(self) -> None:
        self._toggle_selected_azure_schedule(False)

    def remove_selected_azure_schedule(self) -> None:
        try:
            payload = self._build_schedule_payload_from_form()
            payload = self._normalize_schedule_for_azure(payload)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Invalid Schedule", str(exc), parent=self.root)
            return
        azure = payload["execution"]["azure"]
        action_name = azure.get("scheduled_action_name")
        self.bring_to_front()
        if not messagebox.askyesno("Remove Azure Schedule", f"Delete Azure scheduled action?\n\n{action_name}", parent=self.root):
            return
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            subscription_id = azure.get("subscription_id") or payload.get("vm", {}).get("subscription_id")
            resource_group = azure.get("resource_group") or payload.get("vm", {}).get("resource_group")
            resp = self.send_request("DELETE", self._azure_schedule_url(subscription_id, resource_group, action_name), self.token)
            if resp.status_code not in (202, 204, 200):
                raise RuntimeError(f"Azure schedule delete failed: {resp.status_code}\n{resp.text}")
            azure["deployed"] = False
            azure["disabled"] = True
            azure["last_removed_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            payload["enabled"] = False
            self.schedule_enabled_var.set(False)
            self._save_schedule_payload_back_to_current_path(payload)
            self._set_schedule_json_preview(payload)
            self.set_schedule_status(f"Azure schedule removed: {action_name}")
            self.bring_to_front()
            messagebox.showinfo("Azure Schedule Removed", f"Deleted Azure scheduled action:\n{action_name}", parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Azure Remove Failed", str(exc), parent=self.root)

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
            self.root.after(0, self._refresh_deploy_subscription_choices)
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
    # ---------- Deploy VM tab ----------
    def _refresh_deploy_subscription_choices(self) -> None:
        values = sorted({str(vm.get("subscription_id", "")).strip() for vm in self.vms if vm.get("subscription_id")})
        self.deploy_subscription_combo["values"] = values
        current = self.deploy_subscription_var.get().strip()
        if current and current not in values:
            self.deploy_subscription_var.set("")
        if not current and values:
            self.deploy_subscription_var.set(values[0])

    def _read_json_file(self, file_path: str) -> Dict[str, Any]:
        with open(file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _set_text_json(self, widget: tk.Text, payload: Dict[str, Any]) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", json.dumps(payload, indent=2, sort_keys=True))

    def _get_json_from_text(self, widget: tk.Text, label: str) -> Dict[str, Any]:
        raw = widget.get("1.0", "end").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{label} must be a JSON object")
        return parsed

    def _normalize_arm_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not payload:
            return {}
        if "parameters" in payload and isinstance(payload.get("parameters"), dict):
            return payload["parameters"]
        normalized: Dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict) and ("value" in value or "reference" in value):
                normalized[key] = value
            else:
                normalized[key] = {"value": value}
        return normalized

    def _build_default_parameters_from_template(self, template: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name, definition in template.get("parameters", {}).items():
            entry: Dict[str, Any] = {}
            if isinstance(definition, dict) and "defaultValue" in definition:
                entry["value"] = definition.get("defaultValue")
            else:
                entry["value"] = ""
            result[name] = entry
        return {
            "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
            "contentVersion": "1.0.0.0",
            "parameters": result,
        }


    def _flatten_arm_resources(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        flat: List[Dict[str, Any]] = []
        for resource in resources or []:
            if isinstance(resource, dict):
                flat.append(resource)
                nested = resource.get("resources", [])
                if isinstance(nested, list) and nested:
                    flat.extend(self._flatten_arm_resources(nested))
        return flat

    def _template_expr_contains(self, value: Any, needles: List[str]) -> bool:
        text = json.dumps(value).lower() if not isinstance(value, str) else value.lower()
        return any(needle.lower() in text for needle in needles)

    def _analyze_vm_template(self, template: Dict[str, Any]) -> Dict[str, Any]:
        resources = self._flatten_arm_resources(template.get("resources", []) if isinstance(template, dict) else [])
        vm_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.compute/virtualmachines"]
        disk_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.compute/disks"]
        nic_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.network/networkinterfaces"]
        vnet_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.network/virtualnetworks"]
        pip_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.network/publicipaddresses"]
        nsg_resources = [r for r in resources if str(r.get("type", "")).lower() == "microsoft.network/networksecuritygroups"]

        lines: List[str] = []
        warnings: List[str] = []
        suggestions: List[str] = []

        lines.append(f"Top-level/nested resources found: {len(resources)}")
        lines.append(f"VM resources: {len(vm_resources)}")
        lines.append(f"Disk resources: {len(disk_resources)}")
        lines.append(f"NIC resources: {len(nic_resources)}")
        lines.append(f"VNet resources: {len(vnet_resources)}")
        lines.append(f"Public IP resources: {len(pip_resources)}")
        lines.append(f"NSG resources: {len(nsg_resources)}")
        lines.append("")

        vm_ready = False
        has_networking = False
        has_disk = False
        has_os = False

        if not vm_resources:
            warnings.append("This template does not contain Microsoft.Compute/virtualMachines, so it will not deploy a VM.")
            if disk_resources:
                warnings.append("This looks like a disk-only template. Deploying it will create a managed disk, not a virtual machine.")
                suggestions.append("Use 'Add Missing VM Pieces' to scaffold a VNet, Public IP, NIC, and VM around the existing OS disk.")
            else:
                suggestions.append("Add a Microsoft.Compute/virtualMachines resource and the supporting networking resources before deploying.")
        else:
            for idx, vm in enumerate(vm_resources, start=1):
                props = vm.get("properties", {}) if isinstance(vm, dict) else {}
                storage = props.get("storageProfile", {}) if isinstance(props, dict) else {}
                os_profile = props.get("osProfile", {}) if isinstance(props, dict) else {}
                hardware = props.get("hardwareProfile", {}) if isinstance(props, dict) else {}
                network_profile = props.get("networkProfile", {}) if isinstance(props, dict) else {}
                vm_name = vm.get("name", f"vm_{idx}")
                vm_has_network = bool((network_profile.get("networkInterfaces") or []))
                vm_has_disk = bool(storage.get("osDisk") or storage.get("imageReference"))
                vm_has_os = bool(os_profile) and bool(hardware.get("vmSize"))
                has_networking = has_networking or vm_has_network
                has_disk = has_disk or vm_has_disk
                has_os = has_os or vm_has_os
                vm_ready = vm_ready or (vm_has_network and vm_has_disk and vm_has_os)
                lines.append(
                    f"VM {idx}: name={vm_name} | network={'yes' if vm_has_network else 'no'} | "
                    f"disk={'yes' if vm_has_disk else 'no'} | os/hardware={'yes' if vm_has_os else 'no'}"
                )
                if not vm_has_network:
                    warnings.append(f"VM {vm_name} is missing networkProfile.networkInterfaces.")
                if not vm_has_disk:
                    warnings.append(f"VM {vm_name} is missing storageProfile.osDisk or storageProfile.imageReference.")
                if not vm_has_os:
                    warnings.append(f"VM {vm_name} is missing osProfile and/or hardwareProfile.vmSize.")

            if not nic_resources and not has_networking:
                suggestions.append("Add a NIC resource and attach it to the VM networkProfile.")
            if not (vnet_resources or self._template_expr_contains(resources, ["subnet", "virtualnetworks/"])):
                suggestions.append("Add a VNet and subnet, or make sure the NIC references an existing subnet resource ID.")
            if not pip_resources:
                suggestions.append("Add a Public IP if you want direct external connectivity.")
            if not disk_resources and not has_disk:
                suggestions.append("Add an OS disk or imageReference to the VM storageProfile.")

        ready_text = "YES" if vm_ready else "NO"
        lines.insert(0, f"Will this template deploy a VM? {ready_text}")
        if warnings:
            lines.append("\nWarnings:")
            lines.extend(f"- {w}" for w in warnings)
        if suggestions:
            lines.append("\nSuggested fixes:")
            lines.extend(f"- {s}" for s in suggestions)
        return {
            "vm_ready": vm_ready,
            "has_vm_resource": bool(vm_resources),
            "has_networking": has_networking or bool(nic_resources),
            "has_disk": has_disk or bool(disk_resources),
            "has_os": has_os,
            "warnings": warnings,
            "suggestions": suggestions,
            "summary": "\n".join(lines),
        }

    def _set_deploy_analysis(self, content: str) -> None:
        self.deploy_last_analysis_text = content
        self.root.after(0, lambda: self._set_text_widget(self.deploy_analysis_text, content))

    def _preflight_vm_template_warning(self, template: Dict[str, Any]) -> Optional[str]:
        analysis = self._analyze_vm_template(template)
        self._set_deploy_analysis(analysis.get("summary", "No analysis available."))
        if analysis.get("vm_ready"):
            return None
        warning_lines = ["This template is not currently ready to deploy a full VM.", "", analysis.get("summary", "")]
        return "\n".join([line for line in warning_lines if line])

    def analyze_deploy_template(self) -> None:
        try:
            template = self._get_json_from_text(self.deploy_template_text, "ARM Template")
            summary = self._analyze_vm_template(template).get("summary", "No analysis available.")
            self._set_deploy_analysis(summary)
            self.deploy_info_notebook.select(0)
            self.deploy_status_var.set("Template analysis updated")
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Template Analysis Failed", str(exc), parent=self.root)

    def add_missing_vm_template_pieces(self) -> None:
        try:
            template = self._get_json_from_text(self.deploy_template_text, "ARM Template")
            params_payload = self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON") if self.deploy_parameters_text.get("1.0", "end").strip() else self._build_default_parameters_from_template(template)
            params_root = params_payload if isinstance(params_payload, dict) else {}
            param_values = params_root.setdefault("parameters", {})
            template.setdefault("parameters", {})
            template.setdefault("variables", {})
            template.setdefault("resources", [])
            resources = template["resources"]
            flat = self._flatten_arm_resources(resources)

            def ensure_param(name: str, ptype: str, default: Any = None) -> None:
                if name not in template["parameters"]:
                    entry = {"type": ptype}
                    if default is not None and ptype.lower() != "securestring":
                        entry["defaultValue"] = default
                    template["parameters"][name] = entry
                if name not in param_values:
                    value = "" if ptype.lower() == "securestring" and default is None else default
                    param_values[name] = {"value": value}

            ensure_param("vmName", "string", "vm-from-template")
            ensure_param("adminUsername", "string", "azureuser")
            ensure_param("adminPassword", "secureString", None)
            ensure_param("vmSize", "string", "Standard_B2s")
            ensure_param("location", "string", self.deploy_location_var.get().strip() or "eastus2")
            ensure_param("vnetName", "string", "vm-vnet")
            ensure_param("subnetName", "string", "default")
            ensure_param("nicName", "string", "vm-nic")
            ensure_param("publicIpName", "string", "vm-pip")
            ensure_param("computerName", "string", "vmhost")

            os_type = "Linux"
            disk_resource = next((r for r in flat if str(r.get("type", "")).lower() == "microsoft.compute/disks"), None)
            if disk_resource:
                os_type = (((disk_resource.get("properties") or {}).get("osType")) or "Linux")

            disk_param_name = None
            for pname, pdef in (template.get("parameters") or {}).items():
                if str(pname).lower().startswith("disks_") and isinstance(pdef, dict):
                    disk_param_name = pname
                    break

            has_vnet = any(str(r.get("type", "")).lower() == "microsoft.network/virtualnetworks" for r in flat)
            has_pip = any(str(r.get("type", "")).lower() == "microsoft.network/publicipaddresses" for r in flat)
            has_nic = any(str(r.get("type", "")).lower() == "microsoft.network/networkinterfaces" for r in flat)
            vm_resource = next((r for r in flat if str(r.get("type", "")).lower() == "microsoft.compute/virtualmachines"), None)
            additions: List[str] = []

            if not has_vnet:
                resources.append({
                    "type": "Microsoft.Network/virtualNetworks",
                    "apiVersion": "2024-07-01",
                    "name": "[parameters('vnetName')]",
                    "location": "[parameters('location')]",
                    "properties": {
                        "addressSpace": {"addressPrefixes": ["10.0.0.0/16"]},
                        "subnets": [{"name": "[parameters('subnetName')]", "properties": {"addressPrefix": "10.0.0.0/24"}}],
                    },
                })
                additions.append("virtual network")
            if not has_pip:
                resources.append({
                    "type": "Microsoft.Network/publicIPAddresses",
                    "apiVersion": "2024-07-01",
                    "name": "[parameters('publicIpName')]",
                    "location": "[parameters('location')]",
                    "sku": {"name": "Standard"},
                    "properties": {"publicIPAllocationMethod": "Static"},
                })
                additions.append("public IP")
            if not has_nic:
                resources.append({
                    "type": "Microsoft.Network/networkInterfaces",
                    "apiVersion": "2024-07-01",
                    "name": "[parameters('nicName')]",
                    "location": "[parameters('location')]",
                    "dependsOn": [
                        "[resourceId('Microsoft.Network/virtualNetworks', parameters('vnetName'))]",
                        "[resourceId('Microsoft.Network/publicIPAddresses', parameters('publicIpName'))]",
                    ],
                    "properties": {
                        "ipConfigurations": [{
                            "name": "ipconfig1",
                            "properties": {
                                "privateIPAllocationMethod": "Dynamic",
                                "subnet": {"id": "[resourceId('Microsoft.Network/virtualNetworks/subnets', parameters('vnetName'), parameters('subnetName'))]"},
                                "publicIPAddress": {"id": "[resourceId('Microsoft.Network/publicIPAddresses', parameters('publicIpName'))]"},
                            },
                        }]
                    },
                })
                additions.append("network interface")

            if not vm_resource:
                if disk_param_name:
                    disk_id_expr = f"[resourceId('Microsoft.Compute/disks', parameters('{disk_param_name}'))]"
                elif disk_resource:
                    disk_name = disk_resource.get("name")
                    if isinstance(disk_name, str) and disk_name.strip().startswith("["):
                        disk_id_expr = f"[resourceId('Microsoft.Compute/disks', {disk_name[1:-1]})]"
                    else:
                        disk_id_expr = f"[resourceId('Microsoft.Compute/disks', '{disk_name}') ]"
                else:
                    disk_id_expr = None

                storage_profile: Dict[str, Any]
                depends_on = ["[resourceId('Microsoft.Network/networkInterfaces', parameters('nicName'))]"]
                if disk_id_expr:
                    storage_profile = {
                        "osDisk": {
                            "osType": os_type,
                            "createOption": "Attach",
                            "managedDisk": {"id": disk_id_expr},
                            "deleteOption": "Delete",
                        }
                    }
                    if disk_id_expr not in depends_on:
                        depends_on.append(disk_id_expr)
                else:
                    storage_profile = {
                        "imageReference": {
                            "publisher": "Canonical",
                            "offer": "0001-com-ubuntu-server-jammy",
                            "sku": "22_04-lts-gen2",
                            "version": "latest",
                        },
                        "osDisk": {"createOption": "FromImage", "deleteOption": "Delete"},
                    }

                vm_resource = {
                    "type": "Microsoft.Compute/virtualMachines",
                    "apiVersion": "2024-11-01",
                    "name": "[parameters('vmName')]",
                    "location": "[parameters('location')]",
                    "dependsOn": depends_on,
                    "properties": {
                        "hardwareProfile": {"vmSize": "[parameters('vmSize')]"},
                        "osProfile": {
                            "computerName": "[parameters('computerName')]",
                            "adminUsername": "[parameters('adminUsername')]",
                            "adminPassword": "[parameters('adminPassword')]",
                        },
                        "storageProfile": storage_profile,
                        "networkProfile": {"networkInterfaces": [{"id": "[resourceId('Microsoft.Network/networkInterfaces', parameters('nicName'))]"}]},
                    },
                }
                if str(os_type).lower() == "linux":
                    vm_resource["properties"]["osProfile"]["linuxConfiguration"] = {"disablePasswordAuthentication": False}
                resources.append(vm_resource)
                additions.append("virtual machine")
            else:
                props = vm_resource.setdefault("properties", {})
                if not props.get("hardwareProfile"):
                    props["hardwareProfile"] = {"vmSize": "[parameters('vmSize')]"}
                    additions.append("vm hardwareProfile")
                if not props.get("networkProfile"):
                    props["networkProfile"] = {"networkInterfaces": [{"id": "[resourceId('Microsoft.Network/networkInterfaces', parameters('nicName'))]"}]}
                    additions.append("vm networkProfile")
                if not props.get("storageProfile"):
                    if disk_param_name:
                        props["storageProfile"] = {
                            "osDisk": {
                                "osType": os_type,
                                "createOption": "Attach",
                                "managedDisk": {"id": f"[resourceId('Microsoft.Compute/disks', parameters('{disk_param_name}'))]"},
                            }
                        }
                    additions.append("vm storageProfile")
                if not props.get("osProfile"):
                    props["osProfile"] = {
                        "computerName": "[parameters('computerName')]",
                        "adminUsername": "[parameters('adminUsername')]",
                        "adminPassword": "[parameters('adminPassword')]",
                    }
                    if str(os_type).lower() == "linux":
                        props["osProfile"]["linuxConfiguration"] = {"disablePasswordAuthentication": False}
                    additions.append("vm osProfile")

            self._set_text_json(self.deploy_template_text, template)
            self._set_text_json(self.deploy_parameters_text, params_root)
            self.apply_deploy_template_metadata(payload=template)
            summary = self._analyze_vm_template(template).get("summary", "No analysis available.")
            self._set_deploy_analysis(summary)
            self.deploy_info_notebook.select(0)
            self.deploy_status_var.set("Added missing VM pieces: " + (", ".join(additions) if additions else "no changes needed"))
            self.bring_to_front()
            messagebox.showinfo("Template Updated", "Added/checked: " + (", ".join(additions) if additions else "no changes needed"), parent=self.root)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Template Update Failed", str(exc), parent=self.root)

    def _summarize_template_parameters(self, template: Dict[str, Any], parameter_payload: Optional[Dict[str, Any]] = None) -> None:
        template_params = template.get("parameters", {}) if isinstance(template, dict) else {}
        incoming = self._normalize_arm_parameters(parameter_payload or {})
        lines: List[str] = []
        if not template_params:
            lines.append("No template parameters found.")
        for name, definition in template_params.items():
            ptype = definition.get("type", "unknown") if isinstance(definition, dict) else "unknown"
            has_default = isinstance(definition, dict) and "defaultValue" in definition
            supplied = name in incoming and isinstance(incoming.get(name), dict) and "value" in incoming.get(name, {})
            supplied_text = json.dumps(incoming.get(name, {}).get("value")) if supplied else "<not supplied>"
            lines.append(f"{name} | type={ptype} | default={'yes' if has_default else 'no'} | supplied={supplied_text}")
        self._set_text_widget(self.deploy_param_summary_text, "\n".join(lines))

    def import_arm_template_file(self) -> None:
        self.bring_to_front()
        file_path = filedialog.askopenfilename(
            title="Import ARM Template",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        try:
            payload = self._read_json_file(file_path)
            self.deploy_template_path_var.set(file_path)
            self._set_text_json(self.deploy_template_text, payload)
            self.apply_deploy_template_metadata(payload=payload)
            self.deploy_status_var.set(f"Loaded ARM template: {Path(file_path).name}")
            self.notebook.select(self.deploy_tab)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self.root)

    def import_arm_parameters_file(self) -> None:
        self.bring_to_front()
        file_path = filedialog.askopenfilename(
            title="Import ARM Parameters",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            parent=self.root,
        )
        if not file_path:
            return
        try:
            payload = self._read_json_file(file_path)
            self.deploy_parameters_path_var.set(file_path)
            self._set_text_json(self.deploy_parameters_text, payload)
            self.apply_deploy_parameter_metadata(payload=payload)
            self.deploy_status_var.set(f"Loaded parameters: {Path(file_path).name}")
            self.notebook.select(self.deploy_tab)
        except Exception as exc:
            messagebox.showerror("Import Failed", str(exc), parent=self.root)

    def _schedule_deploy_template_sync(self, _event: object = None) -> None:
        try:
            if self._deploy_template_after_id:
                self.root.after_cancel(self._deploy_template_after_id)
        except Exception:
            pass
        self._deploy_template_after_id = self.root.after(450, self._sync_deploy_template_from_editor)

    def _schedule_deploy_parameters_sync(self, _event: object = None) -> None:
        try:
            if self._deploy_parameters_after_id:
                self.root.after_cancel(self._deploy_parameters_after_id)
        except Exception:
            pass
        self._deploy_parameters_after_id = self.root.after(450, self._sync_deploy_parameters_from_editor)

    def _sync_deploy_template_from_editor(self) -> None:
        self._deploy_template_after_id = None
        try:
            payload = self._get_json_from_text(self.deploy_template_text, "ARM Template")
        except Exception:
            return
        self.apply_deploy_template_metadata(payload=payload, from_editor=True)

    def _sync_deploy_parameters_from_editor(self) -> None:
        self._deploy_parameters_after_id = None
        try:
            payload = self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON")
        except Exception:
            return
        self.apply_deploy_parameter_metadata(payload=payload, from_editor=True)

    def _extract_template_hint_value(self, template: Dict[str, Any], parameters: Dict[str, Any], *names: str) -> Optional[str]:
        normalized = self._normalize_arm_parameters(parameters or {})
        lowered = {k.lower(): v for k, v in normalized.items()}
        names_lower = {n.lower() for n in names}
        for name in names:
            value_entry = lowered.get(name.lower())
            if isinstance(value_entry, dict) and "value" in value_entry and value_entry.get("value") not in (None, ""):
                return str(value_entry.get("value"))
        template_params = template.get("parameters", {}) if isinstance(template, dict) else {}
        for key, definition in template_params.items():
            if key.lower() in names_lower and isinstance(definition, dict) and definition.get("defaultValue") not in (None, ""):
                return str(definition.get("defaultValue"))
        return None

    def apply_deploy_template_metadata(self, payload: Optional[Dict[str, Any]] = None, from_editor: bool = False) -> None:
        try:
            template = payload or self._get_json_from_text(self.deploy_template_text, "ARM Template")
        except Exception as exc:
            if not from_editor:
                messagebox.showerror("Template Error", str(exc), parent=self.root)
            return

        if not self.deploy_parameters_text.get("1.0", "end").strip():
            default_params = self._build_default_parameters_from_template(template)
            self._set_text_json(self.deploy_parameters_text, default_params)
            parameter_payload = default_params
        else:
            try:
                parameter_payload = self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON")
            except Exception:
                parameter_payload = self._build_default_parameters_from_template(template)

        stem = (self.deploy_template_path_var.get() and Path(self.deploy_template_path_var.get()).stem.lower().replace("_", "-")) or "vmdeploy"
        if not self.deploy_name_var.get().strip() or self.deploy_name_var.get().startswith("vmdeploy-"):
            self.deploy_name_var.set((stem[:40] or "vmdeploy") + "-deploy")

        self.deploy_template_schema_var.set(str(template.get("$schema", "—")))
        location_hint = self._extract_template_hint_value(template, parameter_payload, "location", "vmLocation", "resourceLocation")
        rg_hint = self._extract_template_hint_value(template, parameter_payload, "resourceGroup", "resourceGroupName", "targetResourceGroup")
        if location_hint and (not self.deploy_location_var.get().strip() or self.deploy_location_var.get().strip().lower() in {"eastus", ""}):
            self.deploy_location_var.set(location_hint)
        if rg_hint and not self.deploy_resource_group_var.get().strip():
            self.deploy_resource_group_var.set(rg_hint)

        resources = template.get("resources", []) if isinstance(template, dict) else []
        if not location_hint:
            for resource in resources:
                if isinstance(resource, dict):
                    loc = resource.get("location")
                    if isinstance(loc, str) and loc and not loc.strip().startswith("["):
                        self.deploy_location_var.set(loc)
                        break

        self._summarize_template_parameters(template, parameter_payload)
        self._set_deploy_analysis(self._analyze_vm_template(template).get("summary", "No analysis available."))
        if from_editor:
            self.deploy_status_var.set("Template pasted/edited — deployment fields refreshed")

    def apply_deploy_parameter_metadata(self, payload: Optional[Dict[str, Any]] = None, from_editor: bool = False) -> None:
        try:
            params = payload or self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON")
        except Exception as exc:
            if not from_editor:
                messagebox.showerror("Parameters Error", str(exc), parent=self.root)
            return

        try:
            template_payload = self._get_json_from_text(self.deploy_template_text, "ARM Template")
        except Exception:
            template_payload = {}

        location_hint = self._extract_template_hint_value(template_payload, params, "location", "vmLocation", "resourceLocation")
        rg_hint = self._extract_template_hint_value(template_payload, params, "resourceGroup", "resourceGroupName", "targetResourceGroup")
        if location_hint:
            self.deploy_location_var.set(location_hint)
        if rg_hint and not self.deploy_resource_group_var.get().strip():
            self.deploy_resource_group_var.set(rg_hint)
        if template_payload:
            self._summarize_template_parameters(template_payload, params)
        if from_editor:
            self.deploy_status_var.set("Parameters pasted/edited — deployment fields refreshed")

    def format_deploy_template_editor(self) -> None:
        try:
            payload = self._get_json_from_text(self.deploy_template_text, "ARM Template")
            self._set_text_json(self.deploy_template_text, payload)
            self.apply_deploy_template_metadata(payload=payload)
        except Exception as exc:
            messagebox.showerror("Format Failed", str(exc), parent=self.root)

    def format_deploy_parameters_editor(self) -> None:
        try:
            payload = self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON")
            self._set_text_json(self.deploy_parameters_text, payload)
            self.apply_deploy_parameter_metadata(payload=payload)
        except Exception as exc:
            messagebox.showerror("Format Failed", str(exc), parent=self.root)

    def _build_arm_deployment_body(self) -> Dict[str, Any]:
        template = self._get_json_from_text(self.deploy_template_text, "ARM Template")
        if not template:
            raise RuntimeError("ARM Template is empty")
        params_payload = self._get_json_from_text(self.deploy_parameters_text, "Parameters JSON")
        parameters = self._normalize_arm_parameters(params_payload)
        return {
            "properties": {
                "mode": self.deploy_mode_var.get().strip() or "Incremental",
                "template": template,
                "parameters": parameters,
            }
        }

    def _get_resource_group_url(self, subscription_id: str, resource_group: str) -> str:
        return (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourcegroups/{resource_group}?api-version={RESOURCE_API}"
        )

    def _resource_group_exists(self, token: str, subscription_id: str, resource_group: str) -> bool:
        url = self._get_resource_group_url(subscription_id, resource_group)
        response = self.send_request("GET", url, token)
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise RuntimeError(f"failed to check resource group {resource_group}: {response.status_code} {response.text}")

    def _ensure_resource_group(self, token: str, subscription_id: str, resource_group: str, location: str) -> bool:
        """Ensure the resource group exists. Returns True when it already existed, False when created."""
        if self._resource_group_exists(token, subscription_id, resource_group):
            self.deploy_rg_status_var.set(f"Using existing resource group: {resource_group}")
            return True

        if not self.deploy_create_rg_if_missing_var.get():
            raise RuntimeError(
                f"resource group '{resource_group}' does not exist. Select an existing resource group or enable 'Create resource group if missing'."
            )

        url = self._get_resource_group_url(subscription_id, resource_group)
        response = self.send_request("PUT", url, token, {"location": location})
        if response.status_code not in (200, 201):
            detail = response.text
            if response.status_code in (401, 403) and "Microsoft.Resources/subscriptions/resourcegroups/write" in detail:
                raise RuntimeError(
                    "You do not have permission to create or update resource groups in this subscription. "
                    "Choose an existing resource group, or request a role that includes "
                    "Microsoft.Resources/subscriptions/resourcegroups/write (such as Contributor or Owner at the appropriate scope)."
                )
            raise RuntimeError(f"failed to create resource group {resource_group}: {response.status_code} {detail}")
        self.deploy_rg_status_var.set(f"Created resource group: {resource_group}")
        return False

    def validate_deploy_template(self) -> None:
        threading.Thread(target=self._validate_deploy_template_worker, daemon=True).start()

    def _validate_deploy_template_worker(self) -> None:
        self.set_status("Validating ARM template...")
        self.deploy_status_var.set("Validating ARM template...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            if not self.deploy_subscription_var.get().strip():
                self._load_subscription_choices_from_azure()
            subscription_id = self.deploy_subscription_var.get().strip()
            resource_group = self.deploy_resource_group_var.get().strip()
            location = self.deploy_location_var.get().strip()
            deployment_name = self.deploy_name_var.get().strip() or "vmdeploy-validate"
            if not subscription_id or not resource_group or not location:
                raise RuntimeError("Subscription, Resource Group, and Location are required")
            template_payload = self._get_json_from_text(self.deploy_template_text, "ARM Template")
            warning = self._preflight_vm_template_warning(template_payload)
            if warning:
                self.bring_to_front()
                proceed = messagebox.askyesno("Template Warning", warning + "\n\nContinue with validation anyway?", parent=self.root)
                if not proceed:
                    self.deploy_status_var.set("Validation cancelled")
                    return
            body = self._build_arm_deployment_body()
            existed = self._ensure_resource_group(self.token, subscription_id, resource_group, location)
            self.log(f"[INF]: {'Using existing' if existed else 'Created'} resource group {resource_group}")
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}/validate"
                f"?api-version={RESOURCE_API}"
            )
            response = self.send_request("POST", url, self.token, body)
            if response.status_code not in (200, 202):
                raise RuntimeError(f"validation failed: {response.status_code} {response.text}")
            payload = response.json() if response.text.strip() else {}
            summary = json.dumps(payload, indent=2)[:6000] or "Validation succeeded."
            self._set_deploy_result("Validation result:\n\n" + summary)
            self.deploy_status_var.set("Validation completed")
            self.log(f"[INF]: ARM validation completed for {deployment_name}")
            self.root.after(0, lambda: messagebox.showinfo("Validation Complete", summary[:4000], parent=self.root))
        except Exception as exc:
            self.deploy_status_var.set("Validation failed")
            self.log(f"[ERR]: ARM validation failed: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Validation Failed", str(exc), parent=self.root))
        finally:
            self.set_status("Ready")

    def deploy_arm_template(self) -> None:
        try:
            template_payload = self._get_json_from_text(self.deploy_template_text, "ARM Template")
            warning = self._preflight_vm_template_warning(template_payload)
        except Exception as exc:
            self.bring_to_front()
            messagebox.showerror("Template Error", str(exc), parent=self.root)
            return
        self.bring_to_front()
        prompt = "Deploy this ARM template now?"
        if warning:
            prompt = warning + "\n\nDeploy anyway?"
        if not messagebox.askyesno("Deploy ARM Template", prompt, parent=self.root):
            return
        threading.Thread(target=self._deploy_arm_template_worker, daemon=True).start()

    def _deploy_arm_template_worker(self) -> None:
        self.set_status("Deploying ARM template...")
        self.deploy_status_var.set("Deploying ARM template...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            if not self.deploy_subscription_var.get().strip():
                self._load_subscription_choices_from_azure()
            subscription_id = self.deploy_subscription_var.get().strip()
            resource_group = self.deploy_resource_group_var.get().strip()
            location = self.deploy_location_var.get().strip()
            deployment_name = self.deploy_name_var.get().strip() or f"vmdeploy-{int(time.time())}"
            if not subscription_id or not resource_group or not location:
                raise RuntimeError("Subscription, Resource Group, and Location are required")
            body = self._build_arm_deployment_body()
            existed = self._ensure_resource_group(self.token, subscription_id, resource_group, location)
            self.log(f"[INF]: {'Using existing' if existed else 'Created'} resource group {resource_group}")
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}"
                f"?api-version={RESOURCE_API}"
            )
            response = self.send_request("PUT", url, self.token, body)
            if response.status_code not in (200, 201, 202):
                raise RuntimeError(f"deployment request failed: {response.status_code} {response.text}")
            self.log(f"[INF]: ARM deployment request submitted for {deployment_name}")
            result = self._poll_deployment_until_complete(self.token, subscription_id, resource_group, deployment_name)
            operations = self._get_deployment_operations(self.token, subscription_id, resource_group, deployment_name)
            summary = self._render_deployment_result_summary(result, operations)
            self._set_deploy_result(summary)
            provisioning = (((result or {}).get("properties") or {}).get("provisioningState", "Unknown"))
            self.deploy_status_var.set(f"Deployment finished with state: {provisioning}")
            self.root.after(0, lambda: messagebox.showinfo("Deployment Result", summary[:4000], parent=self.root))
            self.load_vms()
        except Exception as exc:
            self.deploy_status_var.set("Deployment failed")
            self.log(f"[ERR]: ARM deployment failed: {exc}")
            self.root.after(0, lambda: messagebox.showerror("Deployment Failed", str(exc), parent=self.root))
        finally:
            self.set_status("Ready")

    def _poll_deployment_until_complete(self, token: str, subscription_id: str, resource_group: str, deployment_name: str) -> Dict[str, Any]:
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}"
            f"?api-version={RESOURCE_API}"
        )
        last_payload: Dict[str, Any] = {}
        for _ in range(POLL_ATTEMPTS * 10):
            time.sleep(3)
            response = self.send_request("GET", url, token)
            if response.status_code != 200:
                continue
            payload = response.json()
            last_payload = payload
            state = str((((payload or {}).get("properties") or {}).get("provisioningState", "Unknown")))
            self.deploy_status_var.set(f"Deployment state: {state}")
            if state.lower() in {"succeeded", "failed", "canceled", "cancelled"}:
                if state.lower() != "succeeded":
                    raise RuntimeError(json.dumps(payload, indent=2)[:4000])
                return payload
        return last_payload

    def _load_subscription_choices_from_azure(self) -> None:
        token = self.token or self.get_azure_access_token()
        url = f"https://management.azure.com/subscriptions?api-version={SUBSCRIPTION_API}"
        response = self.send_request("GET", url, token)
        if response.status_code != 200:
            raise RuntimeError(f"failed to load subscriptions: {response.status_code} {response.text}")
        values = sorted([item.get("subscriptionId", "") for item in response.json().get("value", []) if item.get("subscriptionId")])
        self.root.after(0, lambda vals=values: self.deploy_subscription_combo.configure(values=vals))
        if values and not self.deploy_subscription_var.get().strip():
            self.deploy_subscription_var.set(values[0])
            self.root.after(0, self.load_deploy_resource_groups)

    def _on_deploy_subscription_changed(self, _event: object = None) -> None:
        self.load_deploy_resource_groups()

    def load_deploy_resource_groups(self) -> None:
        threading.Thread(target=self._load_deploy_resource_groups_worker, daemon=True).start()

    def _load_deploy_resource_groups_worker(self) -> None:
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            subscription_id = self.deploy_subscription_var.get().strip()
            if not subscription_id:
                self._load_subscription_choices_from_azure()
                subscription_id = self.deploy_subscription_var.get().strip()
            if not subscription_id:
                raise RuntimeError("Select a subscription first")
            url = f"https://management.azure.com/subscriptions/{subscription_id}/resourcegroups?api-version={RESOURCE_API}"
            response = self.send_request("GET", url, self.token)
            if response.status_code != 200:
                raise RuntimeError(f"failed to load resource groups: {response.status_code} {response.text}")
            payload = response.json()
            values = sorted([item.get("name", "") for item in payload.get("value", []) if item.get("name")])
            self.root.after(0, lambda vals=values: self.deploy_resource_group_combo.configure(values=vals))
            self.root.after(0, lambda count=len(values): self.deploy_rg_status_var.set(f"Loaded {count} resource group(s). Select one, or type/paste a name. Creation is optional and off by default."))
        except Exception as exc:
            self.root.after(0, lambda e=str(exc): self.deploy_rg_status_var.set(f"Resource groups not loaded: {e}"))

    def preview_deploy_changes(self) -> None:
        threading.Thread(target=self._preview_deploy_changes_worker, daemon=True).start()

    def _preview_deploy_changes_worker(self) -> None:
        self.set_status("Previewing ARM template changes...")
        self.deploy_status_var.set("Previewing ARM template changes...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            if not self.deploy_subscription_var.get().strip():
                self._load_subscription_choices_from_azure()
            subscription_id = self.deploy_subscription_var.get().strip()
            resource_group = self.deploy_resource_group_var.get().strip()
            location = self.deploy_location_var.get().strip()
            deployment_name = self.deploy_name_var.get().strip() or "vmdeploy-preview"
            if not subscription_id or not resource_group or not location:
                raise RuntimeError("Subscription, Resource Group, and Location are required")
            body = self._build_arm_deployment_body()
            existed = self._ensure_resource_group(self.token, subscription_id, resource_group, location)
            self.log(f"[INF]: {'Using existing' if existed else 'Created'} resource group {resource_group}")
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}/whatIf"
                f"?api-version={RESOURCE_API}"
            )
            response = self.send_request("POST", url, self.token, body)
            if response.status_code not in (200, 201, 202):
                raise RuntimeError(f"preview failed: {response.status_code} {response.text}")
            payload = response.json() if response.text.strip() else {}
            summary = self._render_what_if_summary(payload)
            self._set_deploy_preview(summary)
            self._set_deploy_result("Preview completed. Open the 'Preview Changes' pane to review predicted changes.")
            self.deploy_status_var.set("Preview completed")
            self.log(f"[INF]: ARM preview completed for {deployment_name}")
            self.root.after(0, lambda s=summary[:4000]: messagebox.showinfo("Preview Changes", s, parent=self.root))
        except Exception as exc:
            self.deploy_status_var.set("Preview failed")
            self.log(f"[ERR]: ARM preview failed: {exc}")
            self.root.after(0, lambda e=str(exc): messagebox.showerror("Preview Failed", e, parent=self.root))
        finally:
            self.set_status("Ready")

    def view_deployment_result(self) -> None:
        threading.Thread(target=self._view_deployment_result_worker, daemon=True).start()

    def _view_deployment_result_worker(self) -> None:
        self.set_status("Loading deployment result...")
        self.deploy_status_var.set("Loading deployment result...")
        try:
            if not self.token:
                self.token = self.get_azure_access_token()
            if not self.deploy_subscription_var.get().strip():
                self._load_subscription_choices_from_azure()
            subscription_id = self.deploy_subscription_var.get().strip()
            resource_group = self.deploy_resource_group_var.get().strip()
            deployment_name = self.deploy_name_var.get().strip()
            if not subscription_id or not resource_group or not deployment_name:
                raise RuntimeError("Subscription, Resource Group, and Deployment Name are required")
            url = (
                f"https://management.azure.com/subscriptions/{subscription_id}"
                f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}"
                f"?api-version={RESOURCE_API}"
            )
            response = self.send_request("GET", url, self.token)
            if response.status_code != 200:
                raise RuntimeError(f"failed to load deployment result: {response.status_code} {response.text}")
            result = response.json()
            operations = self._get_deployment_operations(self.token, subscription_id, resource_group, deployment_name)
            summary = self._render_deployment_result_summary(result, operations)
            self._set_deploy_result(summary)
            self.deploy_status_var.set("Deployment result loaded")
            self.log(f"[INF]: Loaded deployment result for {deployment_name}")
        except Exception as exc:
            self.deploy_status_var.set("Deployment result load failed")
            self.log(f"[ERR]: Failed to load deployment result: {exc}")
            self.root.after(0, lambda e=str(exc): messagebox.showerror("Deployment Result Failed", e, parent=self.root))
        finally:
            self.set_status("Ready")

    def _set_deploy_preview(self, content: str) -> None:
        self.deploy_last_preview_text = content
        self.root.after(0, lambda c=content: self._set_text_widget(self.deploy_preview_text, c))

    def _set_deploy_result(self, content: str) -> None:
        self.deploy_last_result_text = content
        self.root.after(0, lambda c=content: self._set_text_widget(self.deploy_result_text, c))

    def _get_deployment_operations(self, token: str, subscription_id: str, resource_group: str, deployment_name: str) -> List[Dict[str, Any]]:
        url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourcegroups/{resource_group}/providers/Microsoft.Resources/deployments/{deployment_name}/operations"
            f"?api-version={RESOURCE_API}"
        )
        response = self.send_request("GET", url, token)
        if response.status_code != 200:
            return []
        payload = response.json() if response.text.strip() else {}
        return payload.get("value", []) if isinstance(payload, dict) else []

    def _render_what_if_summary(self, payload: Dict[str, Any]) -> str:
        properties = payload.get("properties", {}) if isinstance(payload, dict) else {}
        changes = properties.get("changes", []) if isinstance(properties, dict) else []
        status = properties.get("status", "Unknown") if isinstance(properties, dict) else "Unknown"
        lines = [f"What-If Status: {status}", ""]
        if not changes:
            lines.append("No predicted changes were returned.")
        else:
            lines.append(f"Predicted changes: {len(changes)}")
            lines.append("")
            for idx, change in enumerate(changes, start=1):
                resource_id = change.get("resourceId", "")
                change_type = change.get("changeType", "Unknown")
                lines.append(f"{idx}. {change_type}")
                if resource_id:
                    lines.append(f"   Resource: {resource_id}")
                delta = change.get("delta")
                if delta:
                    try:
                        delta_text = json.dumps(delta, indent=2)
                        for line in delta_text.splitlines()[:30]:
                            lines.append(f"   {line}")
                    except Exception:
                        pass
                lines.append("")
        if payload:
            lines.append("Raw payload excerpt:")
            lines.append(json.dumps(payload, indent=2)[:4000])
        return "\n".join(lines)

    def _render_deployment_result_summary(self, result: Dict[str, Any], operations: List[Dict[str, Any]]) -> str:
        properties = result.get("properties", {}) if isinstance(result, dict) else {}
        provisioning = properties.get("provisioningState", "Unknown")
        timestamp = properties.get("timestamp", "")
        mode = properties.get("mode", "")
        outputs = properties.get("outputs", {})
        error = properties.get("error", {})

        lines = [f"Provisioning State: {provisioning}"]
        if timestamp:
            lines.append(f"Timestamp: {timestamp}")
        if mode:
            lines.append(f"Mode: {mode}")
        lines.append("")

        if outputs:
            lines.append("Outputs:")
            try:
                lines.append(json.dumps(outputs, indent=2))
            except Exception:
                lines.append(str(outputs))
            lines.append("")

        if error:
            lines.append("Deployment Error:")
            try:
                lines.append(json.dumps(error, indent=2))
            except Exception:
                lines.append(str(error))
            lines.append("")

        if operations:
            lines.append(f"Operations ({len(operations)}):")
            for idx, op in enumerate(operations, start=1):
                op_props = op.get("properties", {}) if isinstance(op, dict) else {}
                target = op_props.get("targetResource", {}) if isinstance(op_props, dict) else {}
                target_id = target.get("id", "") if isinstance(target, dict) else ""
                target_type = target.get("resourceType", "") if isinstance(target, dict) else ""
                state = op_props.get("provisioningState", "Unknown")
                status_code = op_props.get("statusCode", "")
                lines.append(f"{idx}. State: {state}  Status: {status_code}")
                if target_type:
                    lines.append(f"   Type: {target_type}")
                if target_id:
                    lines.append(f"   Resource: {target_id}")
                op_status = op_props.get("statusMessage")
                if op_status:
                    try:
                        msg = json.dumps(op_status, indent=2) if isinstance(op_status, (dict, list)) else str(op_status)
                        for line in msg.splitlines()[:20]:
                            lines.append(f"   {line}")
                    except Exception:
                        pass
                lines.append("")
        else:
            lines.append("No deployment operations were returned.")

        lines.append("Raw deployment payload excerpt:")
        lines.append(json.dumps(result, indent=2)[:4000])
        return "\n".join(lines)

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
