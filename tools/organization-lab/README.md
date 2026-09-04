# Nova Organization Lab bootstrapper

This Windows-first tool turns a supported NVIDIA workstation into a local Nova
Organization server lab without changing the validated production Compose.

Run from an elevated PowerShell prompt when installing prerequisites:

```powershell
.\tools\organization-lab\NovaOrganizationServerSetup.ps1 analyze
.\tools\organization-lab\NovaOrganizationServerSetup.ps1 install -OrganizationDomains exemple.fr -FirstAdminEmail alex@exemple.fr
```

`-OrganizationDomains` names the e-mail domains whose addresses may sign in. It
has no default: the server's own fallback belongs to an earlier single-tenant
deployment, and it fails quietly -- the administrator still signs in, because
their account already exists and identity linking precedes the domain check,
while every other member is turned away at first login. The installer therefore
refuses to write a configuration without it, and `-Headless` requires it.

`-FirstAdminEmail` names the organization's first `organization_admin`. Without
it, the stack comes up and answers `/api/readyz` while nobody can open Nova
Admin, so `-Headless` requires it and fails immediately when it is missing;
interactive runs ask for it before anything is downloaded. `-SecondAdminEmail`
is optional and never imposed -- a single administrator on holiday blocks the
organization, so the final screen says so and gives the exact command to add
another. Re-running `install`, `update` or `reconfigure` replays the promotion
with no side effect. The address is stored with the same access restrictions as
the database password so a resume after the WSL2 reboot promotes the same
person -- along with the organization name and type, which had no such net and
were silently reset to their defaults after a resume -- and it is kept out of
the log.

The launcher forwards every argument to the script. It used to pass only the
first one, so named parameters were dropped in silence.

Other commands are `configure`, `start`, `status`, `update`, `repair` and
`reconfigure`. Add `-Json` to `analyze` or `status` for machine-readable output.

Generated configuration and secrets live below `tools/organization-lab/` in
Git-ignored directories. The PostgreSQL password is generated locally, mounted
as a Docker secret and never written to a tracked environment or Compose file.

The bootstrapper never writes Desktop Organization configuration. Managed
Desktop discovery continues to use its compiled, pinned Control Plane origin.

`LAB_CPU` is intentionally diagnostic-only in V1. It does not silently select a
cloud runtime. GPU profiles are selected from `profiles.manifest.json`.
