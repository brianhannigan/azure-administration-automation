# Azure VM Manager

<p align="center">
  <img src="assets/azure-vm-manager-header.svg" alt="Azure VM Manager Header" width="100%" />
</p>

<p align="center">
  <strong>Desktop management for Azure virtual machines with lifecycle actions, encrypted config management, and local + Azure-backed scheduling.</strong>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white">
  <img alt="Tkinter" src="https://img.shields.io/badge/UI-Tkinter-1F6FEB">
  <img alt="Azure" src="https://img.shields.io/badge/Azure-VM%20Management-0078D4?logo=microsoftazure&amp;logoColor=white">
  <img alt="Encryption" src="https://img.shields.io/badge/Config-Encrypted-2EA043">
  <img alt="Scheduling" src="https://img.shields.io/badge/Scheduling-Local%20%2B%20Azure-F59E0B">
</p>

---

## Overview

**Azure VM Manager** is a Windows-friendly Python desktop application for managing Azure VMs from a single interface.

It combines:

- VM discovery across subscriptions
- Start, stop, and delete actions
- Live status monitoring with visual indicators
- Detailed per-VM inspection panels
- Encrypted JSON config export and editing
- Local Windows Task Scheduler automation
- Azure-side schedule deployment from inside the app

The goal is simple: manage VM operations and scheduling **without constantly jumping into the Azure portal**.

---

## Main Capabilities

### VM Operations
- Load VMs across accessible Azure subscriptions
- Start selected VM
- Stop selected VM
- Delete selected VM
- Poll Azure until status changes are reflected in the UI
- View VM details in a dedicated detail panel instead of a single long row

### Status Visibility
- Visual VM status icons
- Running / stopped / transition / unknown states
- Live updates after start, stop, and delete operations

### Config Management
- Save encrypted JSON files for:
  - start actions
  - stop actions
  - VM inventory
  - full VM export
- Browse saved config files in a dedicated tab
- Open, view, edit, validate, and format JSON
- Save as plain JSON or encrypted JSON

### Scheduling
- Create start/stop schedules in the app
- Save schedules as encrypted files
- Register or remove local Windows Task Scheduler jobs
- Run saved schedules on demand
- Deploy schedules to Azure from the app UI
- Enable, disable, or remove Azure schedules

---

## Why This Project Exists

Azure gives you powerful APIs, but real day-to-day operations often still turn into repetitive portal clicks, scattered scripts, or half-documented automation.

This project turns those tasks into a single operational desktop tool for:

- admins who want a faster UI than the portal for basic VM actions
- engineers who want encrypted config snapshots and reusable exports
- operators who need scheduled VM power management
- teams who want a local scheduling option first, then Azure-native scheduling later

---

## Application Tabs

### 1. VMs
The main operations workspace.

Features include:
- VM list with status icons
- details panel for selected VM
- log panel
- start / stop / delete actions
- export options for inventory and full VM metadata

### 2. Config Files
A built-in JSON workspace.

Features include:
- browse config folder
- list `.json` and `.encjson` files
- decrypt/open encrypted configs
- edit JSON directly in the app
- validate and format JSON
- save changes back to disk

### 3. Schedules
A scheduling workspace for recurring VM actions.

Features include:
- create start/stop schedules
- assign frequency, days, time, and timezone
- save encrypted schedule files
- register local tasks in Windows Task Scheduler
- deploy the same schedule to Azure

---

## Architecture Summary

```text
Tkinter Desktop UI
├── Azure Authentication (DefaultAzureCredential)
├── Azure REST API Operations
│   ├── Subscriptions
│   ├── Virtual Machines
│   ├── Instance View / Power State
│   └── Azure Schedule Deployment
├── Encrypted Config Layer
│   ├── JSON serialization
│   ├── Password-based encryption
│   └── Config file browser/editor
└── Scheduling Layer
    ├── Local Windows Task Scheduler
    └── Azure-side schedule deployment
```

---

## File Structure Suggestion

```text
repo-root/
├── autostart_ui_phase2_schedule.py
├── README.md
├── assets/
│   └── azure-vm-manager-header.svg
├── vm_configs/
│   ├── *.json
│   └── *.encjson
└── screenshots/
```

---

## Requirements

- Windows machine for the full desktop + Task Scheduler workflow
- Python 3.11+
- Azure access with permission to read and manage VMs
- Credentials available through `DefaultAzureCredential`

### Python packages

```bash
pip install requests azure-identity cryptography
```

---

## Authentication

The app uses:

```python
DefaultAzureCredential()
```

That means it can work with whichever Azure identity source is available in your environment, such as:
- Azure CLI login
- Visual Studio / VS Code login
- environment variables
- managed identity

---

## Running the App

```bash
python autostart_ui_phase2_schedule.py
```

---

## Typical Workflow

### Manage a VM manually
1. Launch the app
2. Go to the **VMs** tab
3. Click **Load VMs**
4. Select a VM
5. Start, stop, or delete it
6. Watch the icon and status text update as Azure reports the new state

### Save an encrypted VM export
1. Select a VM
2. Click **Save Full VM Export**
3. Enter a password
4. Save the encrypted `.encjson` file

### Create a local schedule
1. Open the **Schedules** tab
2. Select the VM and action
3. Set days/time/timezone
4. Save the schedule
5. Register it with Windows Task Scheduler

### Create an Azure schedule from the app
1. Open the **Schedules** tab
2. Load or create a schedule
3. Click the Azure deployment action
4. Enable or disable it later from the same app

---

## Security Notes

- Config files can be saved in encrypted form
- Password prompts are parented to the main window to avoid off-screen dialog issues on multi-monitor setups
- Full exports do **not** include secrets such as admin passwords
- Deleting a VM deletes the VM resource, but associated resources may still need separate cleanup

---

## Operational Notes

- Azure-side scheduling support is designed so you can manage schedules from the app instead of manually going through the portal
- Local task scheduling is useful when you want quick control from a Windows workstation
- Azure scheduling is better when you need the schedule to continue running even if your local PC is off

---

## Good Future Enhancements

- Create VM from exported config
- Rehydrate network and disk settings into a creation wizard
- Search and filter for large VM inventories
- Azure schedule status dashboard
- Row color-coding and history/audit trail
- Multi-VM bulk actions
- Role-based safety checks for destructive operations

---

## Who This Is For

This project is a good fit for:
- Azure administrators
- lab operators
- Dev/Test environment owners
- engineers managing cost-saving start/stop schedules
- operations teams that want a local UI plus Azure-backed automation

---

## Repository Notes

Recommended primary script name:

```text
autostart_ui_phase2_schedule.py
```

Recommended asset path for the header image:

```text
assets/azure-vm-manager-header.svg
```

If you place the SVG in that location, the README image link will work immediately.

---

## License

Add the license of your choice here.

A common choice for internal tools or public utility repos is:
- MIT License

---

## Credits

Built as a practical Azure VM operations tool with:
- Python
- Tkinter
- Azure REST APIs
- Azure Identity
- encrypted JSON config workflows

