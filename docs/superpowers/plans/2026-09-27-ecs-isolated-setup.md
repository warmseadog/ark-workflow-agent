# ECS isolated setup

> Execution: perform in this session under the user's authorization to configure what is available. Public domain selection remains pending.

**Goal:** Connect the current project to ECS-YRXT and provision an independent, loopback-only instance without changing director-prompt-h5.

**Architecture:** Dedicated /opt/ark-video-workflow directory, Unix user, Python environment, systemd unit and port 18080. Access through SSH local forwarding on 18081 until public access and application authentication are implemented. No changes to existing Nginx sites, certificates or service.

**Constraints:** Never read/display/copy SSH private keys. Preserve existing application PID and configuration hashes. Transfer only application source, bundled processing models and the user's existing application settings; no local job database or media/history replay. Secrets travel over SSH directly, not inside release archives or Git. Bound resource consumption of the new service.

- [x] Read-only SSH login, OS/resources, listeners and Nginx inspection.
- [x] Create dedicated connection/tunnel scripts, non-secret deployment files and baseline record.
- [x] Provision isolated runtime, release, settings and service on 127.0.0.1:18080.
- [x] Validate Python dependencies, page/API access, bundled media tools and SSH tunnel.
- [x] Verify original service/PID/config hashes and HTTPS response remain unchanged.
- [x] Document deployed state and remaining domain/authentication/portrait automation requirements.

**Review focus:** Existing workload untouched; no accidental public bind; secrets excluded from archive/logs; startup does not replay local jobs; durable directories survive service restart; limits apply to subprocesses too.
