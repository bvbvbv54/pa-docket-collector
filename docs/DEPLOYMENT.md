# Deployment

1. Provision a client-owned U.S. VPS with at least 2 GB RAM, persistent storage, firewall rules, and backups.
2. Install Docker Engine and the Compose plugin.
3. Copy `.env.example` to `.env`; set unique database, operator, and session secrets. Keep this file private.
4. Run `docker compose up --build -d`.
5. Put the loopback-only app behind a maintained HTTPS proxy. Do not expose port 8000 directly.
6. Confirm `/health`, sign in, and run a synthetic collection before importing authorized PDFs.
7. Back up PostgreSQL and the `docket_data` volume together. Test restore before production use.

The compose file gives the database 768 MB, app 256 MB, and each worker 512 MB. One worker is the 2 GB starting configuration. Two workers plus the database and app approach the 2 GB ceiling; use 4 GB before increasing worker replicas. Chromium is not installed because the UJS live adapter remains unimplemented.

