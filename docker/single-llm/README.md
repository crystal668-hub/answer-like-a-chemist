# single-LLM benchmark image

This image contains the common OpenClaw/Python/Node benchmark runtime only. It
does not contain benchmark skill source or skill-specific dependencies. The
runner mounts skills-on source read-only and mounts no skill source for
skills-off attempts. Both modes use the same image digest and may install
allowlisted registry packages inside the attempt-local environment.
