# March MT5 execution bridge

`MarchExecutionBridge.mq5` is a thin execution adapter. The March strategy,
signals, risk configuration, account routing, and command persistence remain in
the Rust backend. The EA only polls for a command, executes it in its currently
logged-in MT5 account, and reports fills and positions.

## Install

1. In MT5, open **File → Open Data Folder** and copy
   `MarchExecutionBridge.mq5` into `MQL5/Experts/`.
2. Compile it in MetaEditor and attach it to one chart. Enable Algo Trading.
3. Add the backend origin (normally `http://127.0.0.1:8080`) under
   **Tools → Options → Expert Advisors → Allow WebRequest for listed URL**.
4. Ensure the terminal login exactly matches an account configured on the March
   page. Use one terminal instance and one EA per account.
5. Set the same random secret in the backend environment and EA input:

   ```text
   MT5_BRIDGE_TOKEN=<secret>
   BridgeToken=<secret>
   ```

The EA retries result delivery before requesting another command. Every MT5 deal
is tagged `march:<command-id>`, allowing it to recover an already-executed
command after a lost HTTP response without submitting the entry twice.

## Backend settings

- `MT5_BRIDGE_TOKEN` — shared EA secret; empty only for local development
- `MT5_MAGIC_NUMBER` — position/deal magic number, default `26032026`
- `MT5_DEVIATION_POINTS` — maximum order deviation, default `20`

If MT5 is on another machine, set `BIND_HOST` to a reachable trusted interface,
set `BackendUrl` accordingly, and protect the connection with a private network
or TLS reverse proxy. The backend refuses a non-loopback bind when
`MT5_BRIDGE_TOKEN` is empty.
