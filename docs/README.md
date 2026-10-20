# Documentation

The documents for koloda.

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | How the service is put together: the modules, the data flow and the boundaries. |
| [../README.md](../README.md) | The front door: what the service does and how to run it. |
| [../AGENTS.md](../AGENTS.md) | The commands, the traps and the rails for a session that opens the repository. |
| [../PRODUCT.md](../PRODUCT.md) | The product truth the code is measured against. |
| [../DESIGN.md](../DESIGN.md) | The palette, the type, the spacing and the components of the interface. |
| [screenshots/README.md](screenshots/README.md) | The pictures of the interface and where each one comes from. |

## Reading order

New here: [../README.md](../README.md), then
[screenshots/README.md](screenshots/README.md) for the pictures, then
[architecture.md](architecture.md).

Changing the interface: [../DESIGN.md](../DESIGN.md) first, because the
tokens in `index.html` are the design system, then
[../AGENTS.md](../AGENTS.md) for the rails: the contrast test, the check of
the markup, and the sizes of the screenshots.

## What is not here

There is no separate guide for the deployment. The Compose file, the
`Dockerfile` and the environment variables are described in the
[README](../README.md), and the machine that runs the service has a
`docker-compose.override.yml` of its own that is not in the repository.
