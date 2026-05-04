<p align="center">
  <img src="assets/azure-vm-manager-header.svg" alt="Azure VM Manager" width="100%" />
</p>

# Azure VM Manager

Desktop-first Azure VM operations and scheduling from a single Tkinter app.

This project started as a small Python script that enumerated Azure subscriptions and started VMs, then evolved into a fuller desktop management tool with a VM dashboard, detailed VM view, config-file management, encrypted JSON exports, and local/Azure scheduling workflows. The current direction is consistent with the uploaded codebase’s use of a Tkinter GUI, Azure REST calls through `requests`, Azure authentication through `DefaultAzureCredential`, VM status polling, and encrypted config storage using `cryptography`. fileciteturn6file0 fileciteturn6file1

## Highlights

- Load VMs across accessible Azure subscriptions
- See VM status with colored status icons
- Start, stop, and delete selected VMs
- View VM details in a side panel instead of a single long row
- Export:
  - start config
  - stop config
  - VM inventory
  - full VM export
- Save configs as plain JSON or encrypted `.encjson`
- Browse, open, edit, validate, and save JSON config files in-app
- Keep dialogs anchored to the main app window for better multi-monitor behavior
- Create local schedule definitions for start/stop workflows
- Extend toward Azure-side schedule deployment from the app

## Screenshot-Free Architecture

```text
Tkinter UI
├── VMs Tab
│   ├── VM list with status icons
│   ├── Detail pane
│   ├── Start / Stop / Delete actions
│   └── Export actions
├── Config Files Tab
│   ├── Browse config folder
│   ├── Open / edit JSON
│   ├── Encrypt / decrypt config files
│   └── Save updated configs
└── Schedules Tab
    ├── Build start/stop schedules
    ├── Save encrypted schedule definitions
    ├── Register local task automation
    └── Deploy Azure-side schedules
```

## Core Features

### 1. VM Operations
The app authenticates with Azure using `DefaultAzureCredential`, enumerates subscriptions, queries VMs, checks `instanceView`, and maps Azure power states into friendly UI states such as running, stopped, and transition. That behavior is already visible in the uploaded codebase. fileciteturn6file0

### 2. Better VM Visibility
Instead of packing all data into one row, the UI separates the VM list from the detail view:
- status
- name
- resource group
- location
- size
- OS type
- subscription
- full resource ID in its own panel

### 3. Encrypted Config Files
The app uses PBKDF2 + Fernet-based encrypted JSON wrappers for `.encjson` files, with password prompts and config-file persistence. That encrypted-config pattern is present in the uploaded code. fileciteturn5file1 fileciteturn5file5

### 4. Config File Management
The config tab provides:
- config folder browsing
- file listing
- selected file metadata
- open/decrypt
- edit/save
- save as encrypted
- new JSON file creation
- delete config file

The uploaded code clearly includes a dedicated config management tab and editor. fileciteturn6file0

### 5. Scheduling Direction
The project now supports a practical two-phase scheduling approach:
- **Phase 1:** local scheduling and encrypted schedule definitions
- **Phase 2:** deploy Azure-side schedules from the app

This keeps the day-to-day workflow inside the desktop application instead of bouncing between local scripts, Task Scheduler, and the Azure portal.

## Why This Project Exists

Managing Azure VMs often means juggling:
- the Azure portal
- ad hoc scripts
- separate export files
- local automation
- schedule setup in multiple places

This project pulls those actions into one operator-focused desktop tool that is easier to use, easier to repeat, and easier to extend.

## Tech Stack

- **Python 3**
- **Tkinter / ttk**
- **requests**
- **azure-identity**
- **cryptography**
- **Azure REST APIs**
- **Windows Task Scheduler** for local schedule execution
- **Azure scheduling workflow integration** for cloud-side schedule deployment

## Repository Layout

```text
.
├── autostart_ui_phase2_schedule.py
├── README.md
└── assets/
    └── azure-vm-manager-header.svg
```

## Installation

```bash
pip install requests azure-identity cryptography
```

## Run

```bash
python autostart_ui_phase2_schedule.py
```

## Typical Workflow

### Load and operate a VM
1. Open the app
2. Click **Load VMs**
3. Select a VM from the list
4. Review the details pane
5. Start, stop, or delete the VM

### Save an encrypted config
1. Select a VM
2. Choose **Save Start Config**, **Save Stop Config**, **Save VM Inventory**, or **Save Full VM Export**
3. Enter an encryption password
4. Save the `.encjson` file

### Edit a saved JSON config
1. Open the **Config Files** tab
2. Browse to the config folder
3. Select a `.json` or `.encjson` file
4. Open or decrypt it
5. Edit in the built-in editor
6. Save as JSON or encrypted JSON

### Create a schedule
1. Open the **Schedules** tab
2. Select the target VM
3. Choose **start** or **stop**
4. Set the time and recurrence
5. Save the schedule
6. Register locally or deploy to Azure

## Security Notes

- Azure credentials are obtained from `DefaultAzureCredential`; tokens are not stored in exported config files. The uploaded code’s payload builders already reflect that intent. fileciteturn5file5
- Encrypted config files protect saved automation metadata, but your password handling practices still matter.
- A “full VM export” is useful for recreation and reference, but it should not be treated as a complete secret-bearing infrastructure backup.

## Roadmap

- Azure schedule status dashboard
- import schedule/config → recreate VM wizard
- richer form-based config editing
- disk / NIC / public IP cleanup workflows
- filtering and search in the VM grid
- ARM/Bicep export helpers
- optional dark theme

## Who This Is For

- Azure admins
- engineers managing dev/test VMs
- operators who want a desktop tool instead of scattered scripts
- anyone who wants encrypted exportable VM metadata and scheduling in one place

## License

Choose the license that fits how you want to share or commercialize the project.

A simple starting option is:

```text
MIT License
```

---

## Header Asset

The repository includes a custom SVG header:

`assets/azure-vm-manager-header.svg`

If you want, you can also add a matching dark-mode version later.
