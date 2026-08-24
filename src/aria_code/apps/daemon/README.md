# Daemon App

Target role: local-first gateway process for background jobs and external
clients.

Responsibilities:

- manage long-running sessions and task queues;
- expose local MCP or HTTP control surfaces;
- coordinate channel adapters;
- keep user data local unless explicit opt-in allows upload.

The daemon should not implement market data, reports, broker logic, or model
reasoning directly. Those belong behind package services.
