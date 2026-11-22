# Developer setup

You need Claude Code, Python 3.10 or later, and a personal Fusion gateway invitation from the administrator. Your GitHub credential stays on the server. This setup does not use Claude subscription authentication.

```sh
./setup developer join --invite /path/to/personal-invitation.json
./setup doctor
./setup models
claude
```

Join validates the personal gateway key before changing local settings, saves a private backup, and configures Claude Code to use the gateway. All model selectors point to the invitation's Fusion tier. The installer disables thinking, effort overrides, experimental betas and structured outputs outside the supported adapter subset, and disables client retries. Existing unrelated permissions and plugins are retained. Obsolete provider endpoints, credential helpers and old routing settings are removed from the managed configuration.

Start a new terminal or clear inherited provider environment variables if doctor reports a shell override. A process-level variable can override the settings file. Do not paste keys into shell history or chat.

The available service model names are `fusion-standard`, `fusion-high` and `fusion-max`. Your invitation grants the tier selected by your administrator. Ask for a new authorized invitation to change it; no alternative-provider mode exists.

Text streams back while Fusion runs. Tool execution remains in Claude Code on your machine. A service or authentication failure is reported directly and does not switch providers. Restarting a failed task can repeat model work, so review any local tool effects before resending.

Use `./setup developer restore` to restore the saved configuration. Read [SDK limitations](SDK-MIGRATION.md) before relying on advanced Claude Code features. Doctor verifies configuration and authorization, not live Fusion entitlement or model quality.
