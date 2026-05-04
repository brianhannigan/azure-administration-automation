# Azure VM Bulk Start Script

## Overview

This script authenticates to Azure using `DefaultAzureCredential`, enumerates all Azure subscriptions visible to the authenticated identity, lists all virtual machines in each subscription, and sends a **start** request to every VM it finds.

In practical terms, the script does this:

1. Authenticates against Azure Resource Manager.
2. Requests a list of subscriptions.
3. For each subscription:
   1. Requests a list of virtual machines.
   2. Extracts the resource group for each VM from the VM resource ID.
   3. Sends a `POST` request to the Azure VM `start` endpoint.
4. Prints informational, debug, and error messages to the console.

This is a bulk-operation administrative utility intended for environments where the authenticated principal has permission to read subscriptions, read VM metadata, and start virtual machines.

---

## Primary Purpose

The script is designed to automate the startup of all Azure virtual machines accessible to the current credentials across all visible subscriptions.

Typical use cases include:

- Starting non-production lab environments at the beginning of a workday
- Rehydrating test environments after scheduled shutdown
- Administrative automation across multiple subscriptions
- Operating in CI/CD, automation jobs, containers, developer workstations, or Azure-hosted compute with managed identity

---

## High-Level Flow

```text
main()
 ├─ get_azure_access_token()
 ├─ send_request(GET, /subscriptions)
 ├─ parse subscription list JSON
 ├─ for each subscription:
 │   ├─ send_request(GET, /subscriptions/{id}/providers/Microsoft.Compute/virtualMachines)
 │   ├─ parse VM list JSON
 │   ├─ for each VM:
 │   │   ├─ parse_resource_group(vm["id"])
 │   │   ├─ build VM start URL
 │   │   ├─ send_request(POST, /start)
 │   │   └─ report success or failure
 └─ exit
Dependencies

The script requires:

Python 3.10+ recommended
requests
azure-identity

Install with:

pip install requests azure-identity
Authentication Model

The script uses:

from azure.identity import DefaultAzureCredential

DefaultAzureCredential is a chained credential provider. Depending on where the script runs, it may authenticate through:

Environment variables
Managed Identity
Azure CLI login
Visual Studio Code / developer credentials
Other Azure-supported identity sources

The token is requested for this scope:

https://management.azure.com/.default

That scope is used for Azure Resource Manager APIs.

Required Permissions

The authenticated identity must have enough permissions to:

List subscriptions
List virtual machines in each subscription
Start each target VM

At minimum, the principal needs Azure RBAC permissions that allow:

Microsoft.Resources/subscriptions/read
Microsoft.Compute/virtualMachines/read
Microsoft.Compute/virtualMachines/start/action

A role such as Virtual Machine Contributor may be sufficient for VM operations, but subscription listing and cross-subscription access depend on the identity’s scope and assignments.

Script Constants
SUBSCRIPTION_API
SUBSCRIPTION_API = "2022-12-01"

This constant defines the API version used for the Azure subscriptions endpoint:

GET https://management.azure.com/subscriptions?api-version=2022-12-01

Purpose:

Ensures the request uses a known Azure Resource Manager API contract for subscriptions.
VM_API
VM_API = "2025-04-01"

This constant defines the API version used for VM listing and VM start operations.

Used in:

VM list endpoint
VM start endpoint

Purpose:

Controls the request contract for Microsoft.Compute resource operations.

Note:

If that API version is unsupported in your environment or no longer preferred, this may need updating.
AZURE_RESOURCE
AZURE_RESOURCE = "https://management.azure.com/.default"

This is the OAuth scope used when requesting the access token.

Purpose:

Tells Azure Identity to obtain a token for Azure Resource Manager.
Function Reference
get_azure_access_token() -> str
Purpose

Authenticates to Azure and returns an OAuth bearer token string.

Implementation Summary
credential = DefaultAzureCredential()
token = credential.get_token(AZURE_RESOURCE)
return token.token
Inputs

None.

Output
Returns: str
Meaning: a bearer token suitable for use in the Authorization header
Behavior
Instantiates DefaultAzureCredential.
Requests an access token for Azure Resource Manager.
Returns the raw token string.
Failure Modes

Raises RuntimeError if:

The credential chain cannot initialize
No credential source succeeds
Token acquisition fails
Notes

This is one of the most important functions in the script because all later API calls depend on the token it returns.

Functional Type Spec
get_azure_access_token : () -> str

More explicitly:

get_azure_access_token : Unit -> BearerToken

Where:

Unit means “no input”
BearerToken means “string token for ARM authentication”
send_request(method: str, url: str, token: str, body: dict | None = None) -> requests.Response
Purpose

Sends an HTTP request to Azure Resource Manager using bearer token authentication.

Inputs
method: str

HTTP method, typically:

"GET"
"POST"
url: str

Fully qualified Azure Resource Manager URL.

Examples:

subscription listing URL
VM listing URL
VM start URL
token: str

Azure bearer token returned by get_azure_access_token().

body: dict | None = None

Optional request payload.

In the current script:

not used for subscription listing
not used for VM listing
not used for VM start
included for generality / future expansion
Output
Returns: requests.Response
Behavior
Creates headers:
Authorization: Bearer <token>
Content-Type: application/json
Calls requests.request(...)
Returns the HTTP response object
Failure Modes

Raises RuntimeError if the underlying HTTP request fails, such as:

DNS failure
connection error
timeout
network interruption
Important Detail

Although the function accepts body, Azure VM start in this script uses None, which is valid because the endpoint does not require a JSON payload.

Functional Type Spec
send_request : (str, str, str, dict | None) -> requests.Response

More semantically:

send_request : (HttpMethod, Url, BearerToken, JsonBody?) -> HttpResponse
parse_resource_group(resource_id: str) -> str
Purpose

Extracts the Azure resource group name from a full Azure resource ID string.

Example Input
/subscriptions/123/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm-01
Example Output
my-rg
Why This Exists

The VM list response includes the full resource ID. The script uses that ID to derive the resource group name needed to build the start endpoint URL.

Logic
Search for the substring:
/resourceGroups/
If not found, return an empty string.
Otherwise, capture the text immediately after that marker.
Stop at the next /, if present.
Return the captured resource group name.
Inputs
resource_id: str

A full Azure resource ID.

Output
Returns: str
The extracted resource group name, or empty string if parsing fails
Failure Modes

This function does not raise an exception by itself.
If the expected pattern is missing, it returns "".

Risk Note

If the resource ID format changes or is malformed, the result may be empty, causing the generated start URL to be invalid.

Functional Type Spec
parse_resource_group : str -> str

Semantically:

parse_resource_group : ResourceId -> ResourceGroupName
main() -> None
Purpose

Coordinates the entire workflow.

Major Steps
1. Authenticate
token = get_azure_access_token()

If authentication fails:

logs error to stderr
exits with status code 1
2. List Subscriptions

Builds this URL:

https://management.azure.com/subscriptions?api-version=2022-12-01

Sends:

GET /subscriptions

If the request fails or returns a non-200 response:

logs error
exits with status code 1
3. Parse Subscription Response

Expected response shape:

{
  "value": [
    {
      "subscriptionId": "..."
    }
  ]
}

The code loops through:

subs_resp.get("value", [])

For each item:

reads subscriptionId
skips entries missing that field
4. List VMs Per Subscription

For each subscription, builds a URL like:

https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines?api-version=2025-04-01

Sends:

GET /subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines

If it fails:

logs error
continues to the next subscription
5. Parse VM List

Expected response shape:

{
  "value": [
    {
      "id": "/subscriptions/.../resourceGroups/.../providers/Microsoft.Compute/virtualMachines/...",
      "name": "vm-name"
    }
  ]
}

For each VM:

reads id
reads name
calls parse_resource_group(id)
6. Build Start URL

For each VM, constructs:

https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Compute/virtualMachines/{vm_name}/start?api-version=2025-04-01

This is the Azure Resource Manager action endpoint to start a VM.

7. Start the VM

Sends:

POST /start

Expected success status:

202 Accepted

This is important because Azure start operations are typically asynchronous. 202 Accepted means the request was accepted for processing, not necessarily that the VM is already fully running.

If status is not 202:

logs detailed error
continues to next VM

If status is 202:

logs success message
Functional Type Spec
main : () -> None

Semantically:

main : Unit -> ProgramExit

Where ProgramExit represents either:

successful completion
early termination with exit code 1
Data Contracts
Subscription Response Contract

The script expects a JSON payload with this general shape:

{
  "value": [
    {
      "subscriptionId": "00000000-0000-0000-0000-000000000000"
    }
  ]
}

Relevant field used:

value[].subscriptionId

Unused fields are ignored.

VM List Response Contract

The script expects a JSON payload with this general shape:

{
  "value": [
    {
      "id": "/subscriptions/.../resourceGroups/.../providers/Microsoft.Compute/virtualMachines/vm-name",
      "name": "vm-name"
    }
  ]
}

Relevant fields used:

value[].id
value[].name

Unused fields are ignored.

Start VM Response Contract

The script expects:

HTTP 202 Accepted for success

It does not inspect:

response body
operation status URL
Azure async operation headers

That means it only confirms request acceptance, not final completion.

Variables and Their Specific Meanings
Global Constants
SUBSCRIPTION_API

Subscriptions endpoint API version.

VM_API

Virtual machine list/start API version.

AZURE_RESOURCE

OAuth scope used to request an ARM token.

Local Variables in get_azure_access_token
credential

An instance of DefaultAzureCredential.

token

An access token object returned by Azure Identity.

Local Variables in send_request
headers

Dictionary containing request headers:

bearer authorization
content type
response

The raw HTTP response object from requests.

Local Variables in parse_resource_group
marker

Literal string:

"/resourceGroups/"
idx

Position of the marker within resource_id.

remainder

The substring after /resourceGroups/.

slash_idx

Index of the next / within remainder.

Local Variables in main
token

Bearer token for all ARM requests.

subscription_url

Full URL for subscription enumeration.

resp

HTTP response for the subscription request.

subs_resp

Parsed JSON object for subscription data.

sub

Loop variable representing one subscription object.

subscription_id

Azure subscription GUID-like identifier extracted from sub.

vm_url

Full URL used to request all VMs in a subscription.

vm_resp

HTTP response for the VM list request.

vms_resp

Parsed JSON object for VM list data.

vm

Loop variable representing one VM object.

vm_id

The full Azure resource ID for the VM.

vm_name

The Azure VM name.

resource_group

Resource group extracted from vm_id.

start_url

Full ARM action endpoint URL for starting the VM.

start_resp

HTTP response returned from the VM start request.

HTTP Endpoints Used
1. List Subscriptions
GET https://management.azure.com/subscriptions?api-version=2022-12-01

Purpose:

discover all subscriptions accessible to the principal

Expected status:

200 OK
2. List VMs in a Subscription
GET https://management.azure.com/subscriptions/{subscription_id}/providers/Microsoft.Compute/virtualMachines?api-version=2025-04-01

Purpose:

enumerate VMs in the given subscription

Expected status:

200 OK
3. Start a VM
POST https://management.azure.com/subscriptions/{subscription_id}/resourceGroups/{resource_group}/providers/Microsoft.Compute/virtualMachines/{vm_name}/start?api-version=2025-04-01

Purpose:

request startup of a VM

Expected status:

202 Accepted
Logging Behavior

The script writes to two output streams:

Standard Output (stdout)

Used for informational and debug logging:

subscription processing started
start request details
VM start request accepted

Examples:

[INF]: Processing subscription ...
[DBG]: Sending POST request to start VM ...
[INF]: VM ... start request accepted
Standard Error (stderr)

Used for failure conditions:

token acquisition failure
subscription fetch failure
non-OK API status codes
JSON parse failure
VM start failure

Examples:

[ERR]: Failed to get Azure token: ...
[ERR]: Unexpected status for subscriptions: ...
Error Handling Strategy

The script uses a mixed strategy:

Fatal Errors

Cause immediate exit with status code 1:

cannot get token
cannot fetch subscriptions
subscription response is invalid
subscription endpoint returns non-200
Non-Fatal Errors

Logged, then processing continues:

one subscription’s VMs cannot be listed
one VM list payload is invalid
one VM start request fails
one VM start returns unexpected status

This means the script is resilient at the per-subscription and per-VM level, but not at the initial authentication and subscription discovery level.

Important Behavioral Notes
1. The Script Starts All Visible VMs

There is no filtering by:

tag
naming convention
region
resource group
power state
subscription allowlist
environment
size
VM type

If the identity can see the VM, the script attempts to start it.

2. No Pagination Handling

Azure ARM list APIs may return paginated results using a nextLink.

This script does not follow nextLink.

Consequence:

If there are many subscriptions or many VMs, the script may only process the first page.
3. No Power State Check

The script does not inspect current VM power state before calling start.

Consequence:

It may send start requests for already running VMs.
Azure may still accept or handle the request gracefully, but this adds unnecessary API calls.
4. Start Completion Is Not Verified

The script treats 202 Accepted as success.

Consequence:

It confirms request acceptance, not completed startup.
A later failure during the Azure async operation would not be detected by this script.
5. Resource Group Parsing Assumes Standard ARM ID Format

The script depends on the resource ID containing:

/resourceGroups/{name}/

Consequence:

malformed or unexpected IDs can produce empty or incorrect resource group names.
6. Request Body Parameter Is Unused

The helper function accepts body, but all current calls pass None.

This makes the helper reusable, but the current script does not need request payloads.

Example Execution
python start_all_vms.py

Possible output:

[INF]: Processing subscription 11111111-1111-1111-1111-111111111111
[DBG]: Sending POST request to start VM.
    SubscriptionID: 11111111-1111-1111-1111-111111111111
    ResourceGroup: prod-rg
    VM Name: vm-app-01
    URL: https://management.azure.com/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/prod-rg/providers/Microsoft.Compute/virtualMachines/vm-app-01/start?api-version=2025-04-01
[INF]: VM vm-app-01 start request accepted
Functional Type Spec Summary

This section describes the script in a more formal function-signature style.

Primitive Semantic Types
BearerToken        = str
Url                = str
HttpMethod         = str
JsonBody           = dict
ResourceId         = str
ResourceGroupName  = str
SubscriptionId     = str
VmName             = str
HttpResponse       = requests.Response
Function Signatures
get_azure_access_token : () -> BearerToken
send_request           : (HttpMethod, Url, BearerToken, JsonBody | None) -> HttpResponse
parse_resource_group   : ResourceId -> ResourceGroupName
main                   : () -> None
Workflow Composition
main
  = token <- get_azure_access_token()
  ; subscriptions <- send_request(GET, subscriptions_url, token, None)
  ; for each subscriptionId in subscriptions.value:
      vms <- send_request(GET, vm_list_url(subscriptionId), token, None)
      for each vm in vms.value:
          rg <- parse_resource_group(vm.id)
          send_request(POST, vm_start_url(subscriptionId, rg, vm.name), token, None)
Inputs and Outputs
Inputs

Direct explicit inputs:

none from CLI arguments
none from environment parsing in this script itself

Indirect runtime inputs:

Azure credential sources available to DefaultAzureCredential
Azure subscription inventory
Azure VM inventory
network connectivity to Azure Resource Manager
RBAC permissions of the authenticated identity
Outputs
Console logs to stdout/stderr
Side effect: requests Azure to start virtual machines

This is primarily a side-effect-driven automation script.

Side Effects

The script has significant external side effects:

sends authenticated ARM requests
initiates VM start operations
may incur Azure compute cost if VMs begin running
may affect production or non-production environments

Because of that, it should be used carefully.

Operational Risks

Before running this script in real environments, consider:

unexpected cost increase from starting many VMs
accidentally starting production systems
insufficient access causing partial execution
throttling in large environments
asynchronous start acceptance without completion tracking
first-page-only processing if pagination occurs
Recommended Improvements

Potential enhancements include:

Filtering

Allow filtering by:

subscription
resource group
VM name pattern
tag
region
Pagination

Support nextLink for:

subscriptions
VMs
State Awareness

Query instance view or power state before calling start.

Async Tracking

Poll Azure operation status until complete.

Retry Logic

Retry transient failures such as:

429
5xx
timeouts
Structured Logging

Use Python logging with levels and optionally JSON output.

CLI Arguments

Add flags such as:

--subscription
--resource-group
--name-prefix
--dry-run
Safer Execution

Add a dry-run mode to print actions without performing them.

Example File Name

A suitable filename for the script would be:

start_all_azure_vms.py
Summary

This script is a bulk Azure VM startup utility. It authenticates with DefaultAzureCredential, discovers accessible subscriptions, lists VMs in each subscription, derives each VM’s resource group from its ARM resource ID, and sends a start request for each VM. It is simple and effective, but intentionally minimal: it does not paginate, filter, verify VM state, or track completion of async operations.

Use it when you want broad startup automation and understand the permissions, cost, and operational impact.


I can also turn this into a downloadable `README.md` file.


What changed:

creates a dedicated wrapper folder under LOCALAPPDATA\AzureVmManager\task_wrappers
generates a short .cmd launcher per scheduled task
registers Task Scheduler with the wrapper path instead of a long /TR command
shows the wrapper path after successful task registration
removes the wrapper file when the scheduled task is removed