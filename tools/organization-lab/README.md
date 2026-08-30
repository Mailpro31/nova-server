# Nova Organization Lab bootstrapper

This Windows-first tool turns a supported NVIDIA workstation into a local Nova
Organization server lab without changing the validated production Compose.

Run from an elevated PowerShell prompt when installing prerequisites:

```powershell
.\tools\organization-lab\NovaOrganizationServerSetup.ps1 analyze
.\tools\organization-lab\NovaOrganizationServerSetup.ps1 install
```

Other commands are `configure`, `start`, `status`, `update`, `repair` and
`reconfigure`. Add `-Json` to `analyze` or `status` for machine-readable output.

Generated configuration and secrets live below `tools/organization-lab/` in
Git-ignored directories. The PostgreSQL password is generated locally, mounted
as a Docker secret and never written to a tracked environment or Compose file.

The bootstrapper never writes Desktop Organization configuration. Managed
Desktop discovery continues to use its compiled, pinned Control Plane origin.

`LAB_CPU` is intentionally diagnostic-only in V1. It does not silently select a
cloud runtime. GPU profiles are selected from `profiles.manifest.json`.
