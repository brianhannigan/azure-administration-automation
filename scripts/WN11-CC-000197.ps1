<#
.SYNOPSIS
Checks or sets the Windows policy:
Computer Configuration >> Administrative Templates >> Windows Components >> Cloud Content >>
"Turn off Microsoft consumer experiences"

.DESCRIPTION
This script manages the registry-backed policy value:
HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent\DisableWindowsConsumerFeatures

Meaning:
- ON  = STIG-compliant state (registry value = 1)
- OFF = non-compliant / test state (registry value = 0)

PARAMETERS
-CheckOnly : Only check and report the current state
-On        : Set the policy to ON / compliant
-Off       : Set the policy to OFF / non-compliant test state

EXAMPLES
.\Set-ConsumerExperiences.ps1 -CheckOnly
.\Set-ConsumerExperiences.ps1 -On
.\Set-ConsumerExperiences.ps1 -Off
.\Set-ConsumerExperiences.ps1 -On -Verbose

.NOTES
Run in an elevated PowerShell session.
#>

[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Check')]
param(
    [Parameter(ParameterSetName = 'Check')]
    [switch]$CheckOnly,

    [Parameter(ParameterSetName = 'On')]
    [switch]$On,

    [Parameter(ParameterSetName = 'Off')]
    [switch]$Off
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PolicyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'
$PolicyName = 'DisableWindowsConsumerFeatures'

function Test-IsAdmin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-PolicyPath {
    if (-not (Test-Path $PolicyPath)) {
        if ($PSCmdlet.ShouldProcess($PolicyPath, 'Create registry path')) {
            New-Item -Path $PolicyPath -Force | Out-Null
        }
    }
}

function Get-PolicyValue {
    try {
        return (Get-ItemProperty -Path $PolicyPath -Name $PolicyName -ErrorAction Stop).$PolicyName
    }
    catch {
        return $null
    }
}

function Set-PolicyValue {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet(0,1)]
        [int]$Value
    )

    Ensure-PolicyPath

    $currentValue = Get-PolicyValue
    if ($null -ne $currentValue -and [int]$currentValue -eq $Value) {
        Write-Verbose "No change needed. $PolicyName is already $Value."
        return
    }

    if ($PSCmdlet.ShouldProcess("$PolicyPath\$PolicyName", "Set to $Value")) {
        New-ItemProperty -Path $PolicyPath -Name $PolicyName -Value $Value -PropertyType DWord -Force | Out-Null
    }
}

function Get-FriendlyState {
    param(
        [Parameter(Mandatory = $true)]
        $Value
    )

    if ($null -eq $Value) {
        return [pscustomobject]@{
            RegistryValue = '(missing)'
            State         = 'UNKNOWN / NOT CONFIGURED'
            Meaning       = 'Policy value is missing'
            StigStatus    = 'FAIL'
        }
    }

    switch ([int]$Value) {
        1 {
            return [pscustomobject]@{
                RegistryValue = '1'
                State         = 'ON'
                Meaning       = 'Turn off Microsoft consumer experiences is ENABLED'
                StigStatus    = 'PASS'
            }
        }
        0 {
            return [pscustomobject]@{
                RegistryValue = '0'
                State         = 'OFF'
                Meaning       = 'Turn off Microsoft consumer experiences is DISABLED'
                StigStatus    = 'FAIL'
            }
        }
        default {
            return [pscustomobject]@{
                RegistryValue = [string]$Value
                State         = 'UNKNOWN'
                Meaning       = 'Unexpected registry value'
                StigStatus    = 'FAIL'
            }
        }
    }
}

if (-not (Test-IsAdmin)) {
    throw 'Please run this script in an elevated PowerShell window (Run as Administrator).'
}

switch ($PSCmdlet.ParameterSetName) {
    'On' {
        Write-Host 'Requested action: TURN ON (STIG-compliant state)'
        Set-PolicyValue -Value 1
    }
    'Off' {
        Write-Host 'Requested action: TURN OFF (non-compliant test state)'
        Set-PolicyValue -Value 0
    }
    default {
        Write-Host 'Requested action: CHECK ONLY'
    }
}

$finalValue = Get-PolicyValue
$state = Get-FriendlyState -Value $finalValue

Write-Host ''
Write-Host '=== FINAL POLICY STATE ==='
Write-Host "Registry Path : $PolicyPath"
Write-Host "Value Name    : $PolicyName"
Write-Host "Registry Value: $($state.RegistryValue)"
Write-Host "State         : $($state.State)"
Write-Host "Meaning       : $($state.Meaning)"
Write-Host "STIG Status   : $($state.StigStatus)"
Write-Host ''

# Also emit an object for easy scripting / logging
[pscustomobject]@{
    RegistryPath  = $PolicyPath
    ValueName     = $PolicyName
    RegistryValue = $state.RegistryValue
    State         = $state.State
    Meaning       = $state.Meaning
    StigStatus    = $state.StigStatus
}