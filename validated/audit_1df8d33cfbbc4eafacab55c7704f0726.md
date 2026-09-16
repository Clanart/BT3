### Title
Plaintext RPC passwords logged to third-party APM traces via `newDatadogHTTPHandler` - (File: networks/rpc/http_datadog.go)

### Summary
When Datadog APM tracing is enabled on a Kaia node's JSON-RPC HTTP server, the `newDatadogHTTPHandler` middleware captures the raw JSON-RPC request parameters and attaches them as a Datadog span tag (`request.params`) for every request that hits the HTTP endpoint, with no filtering of sensitive fields.

### Finding Description
`newDatadogHTTPHandler` parses each incoming HTTP RPC request, extracts `reqs[0].Params`, marshals it back to JSON, and stores it verbatim as `reqParam`: [1](#0-0) 

This `reqParam` is sent as a plaintext `tracer.Tag("request.params", reqParam)` to the Datadog APM backend for every RPC call served through this handler, with no redaction logic for known-sensitive parameters. `PersonalAPI` methods such as `SendTransaction`, `SignTransaction`, and `SendTransactionAsFeePayer` accept a plaintext `passwd string` argument that is used to decrypt the account key: [2](#0-1) 

The password argument is a normal positional parameter in the JSON-RPC call (e.g. `personal.unlockAccount`, `personal.sign`, `personal.sendTransaction`), so it appears inside `Params` and is captured unfiltered by the tracer. Similarly, `personal_unlockAccount` accepts the password directly over RPC, as documented by the console bridge wrapper (which itself only exists to avoid echoing the password on a terminal, not to protect it from other logging paths): [3](#0-2) 

The Datadog handler is wired into both the standard and fasthttp JSON-RPC HTTP servers whenever `DD_TRACE_ENABLED` is set: [4](#0-3) 

This mirrors the CVE-2020-24804 bug class: a password submitted through a normal request path ends up verbatim in a logging/observability/audit sink not intended to hold secrets, exposing it to anyone with access to that sink (here, Datadog APM) rather than the account owner.

### Impact Explanation
Any operator who runs a node with the `personal` RPC namespace exposed over HTTP (a supported, documented configuration, e.g., for KAS-style deployments given the KAS header handling in the same tracer file) and who enables Datadog tracing for observability will have every account passphrase submitted to `personal_unlockAccount`, `personal_sign`, `personal_sendTransaction`, or `personal_signTransaction` written in plaintext into Datadog trace spans. Anyone with read access to that APM data (a broader and less trusted set of personnel/systems than node operators) can recover account passphrases and subsequently decrypt keystores and steal funds — a direct violation of confidentiality of a credential that leads to unauthorized value movement once combined with keystore file access.

### Likelihood Explanation
This requires (a) the `personal` API namespace to be exposed on the HTTP RPC endpoint and (b) `DD_TRACE_ENABLED` to be set — both are supported, non-default-but-documented configurations rather than universal defaults, so likelihood is conditional on deployment choices rather than guaranteed on every node. However, given that this middleware directly plumbs raw RPC parameters to an external third-party service with no allow/deny list for sensitive methods, any deployment matching that configuration is trivially and reliably affected on every single call.

### Recommendation
Redact or omit parameters for RPC methods known to carry secrets (`personal_unlockAccount`, `personal_sign`, `personal_signTransaction`, `personal_sendTransaction`, `personal_signTransactionAsFeePayer`, `personal_sendTransactionAsFeePayer`, etc.) before setting `tracer.Tag("request.params", reqParam)` in `newDatadogHTTPHandler` (and the equivalent NewRelic handler path), or disable the `personal` namespace whenever tracing is enabled. Prefer maintaining an explicit denylist/allowlist of methods and parameter positions before echoing request bodies into third-party telemetry.

### Proof of Concept
1. Start a Kaia node with the `personal` API namespace enabled on the HTTP RPC transport and set environment variable `DD_TRACE_ENABLED=true` (plus `DD_SERVICE`), triggering `newDatadogTracer()`/`newDatadogHTTPHandler` registration in `NewHTTPServer`. [5](#0-4) 
2. As a public RPC caller, send: `curl -X POST -d '{"jsonrpc":"2.0","method":"personal_unlockAccount","params":["0xabc...","MySecretPass123",300],"id":1}' http://node:8551`.
3. `newDatadogHTTPHandler` parses the request body, extracts `reqs[0].Params` (containing `"MySecretPass123"`), marshals it, and attaches it as `tracer.Tag("request.params", reqParam)`. [6](#0-5) 
4. The password now appears in cleartext within the Datadog APM trace for that request, viewable by anyone with Datadog access, independent of the node's own access controls on the keystore/passphrase.

### Citations

**File:** networks/rpc/http_datadog.go (L94-123)
```go
		reqMethod := ""
		reqParam := ""

		// parse RPC requests
		reqs, isBatch, err := getRPCRequests(r)
		if err != nil || len(reqs) < 1 {
			// The error will be handled in `handler.ServeHTTP()` and printed with `printRPCErrorLog()`
			logger.Debug("failed to parse RPC request", "err", err, "len(reqs)", len(reqs))
		} else {
			reqMethod = reqs[0].Method
			if isBatch {
				reqMethod += "_batch"
			}
			encoded, _ := json.Marshal(reqs[0].Params)
			reqParam = string(encoded)
		}

		// datadog transaction name contains the first API method of the request
		resource := fmt.Sprintf("%s %s %s", r.Method, r.URL.String(), reqMethod)

		// duplicate writer
		dupW := &dupWriter{
			ResponseWriter: w,
			body:           bytes.NewBufferString(""),
		}

		spanOpts := []ddtrace.StartSpanOption{
			tracer.Tag("request.method", reqMethod),
			tracer.Tag("request.params", reqParam),
		}
```

**File:** api/api_personal.go (L236-251)
```go
// SendTransaction will create a transaction from the given arguments and try to
// sign it with the key associated with args.From. If the given password isn't
// able to decrypt the key it fails.
func (s *PersonalAPI) SendTransaction(ctx context.Context, args SendTxArgs, passwd string) (common.Hash, error) {
	if args.AccountNonce == nil {
		// Hold the addresse's mutex around signing to prevent concurrent assignment of
		// the same nonce to multiple accounts.
		s.nonceLock.LockAddr(args.From)
		defer s.nonceLock.UnlockAddr(args.From)
	}
	signedTx, err := s.SignTransaction(ctx, args, passwd)
	if err != nil {
		return common.Hash{}, err
	}
	return submitTransaction(ctx, s.b, signedTx.Tx)
}
```

**File:** console/bridge.go (L148-177)
```go
// UnlockAccount is a wrapper around the personal.unlockAccount RPC method that
// uses a non-echoing password prompt to acquire the passphrase and executes the
// original RPC method (saved in jeth.unlockAccount) with it to actually execute
// the RPC call.
func (b *bridge) UnlockAccount(call jsre.Call) (goja.Value, error) {
	if len(call.Arguments) < 1 {
		return nil, errors.New("usage: unlockAccount(account, [ password, duration ])")
	}

	account := call.Argument(0)
	// Make sure we have an account specified to unlock.
	if goja.IsUndefined(account) || goja.IsNull(account) || account.ExportType().Kind() != reflect.String {
		return nil, errors.New("first argument must be the account to unlock")
	}

	// If password is not given or is the null value, prompt the user for it.
	var passwd goja.Value
	if goja.IsUndefined(call.Argument(1)) || goja.IsNull(call.Argument(1)) {
		fmt.Fprintf(b.printer, "Unlock account %s\n", account)
		input, err := b.prompter.PromptPassword("Passphrase: ")
		if err != nil {
			return nil, err
		}
		passwd = call.VM.ToValue(input)
	} else {
		if call.Argument(1).ExportType().Kind() != reflect.String {
			return nil, errors.New("password must be a string")
		}
		passwd = call.Argument(1)
	}
```

**File:** networks/rpc/http.go (L278-296)
```go
	// If os environment variables for NewRelic exist, register the NewRelicHTTPHandler
	nrApp := newNewRelicApp()
	if nrApp != nil {
		handler = newNewRelicHTTPHandler(nrApp, handler)
	}

	// If os environment variables for Datadog exist, register the NewDatadogHTTPHandler
	ddTracer := newDatadogTracer()
	if ddTracer != nil {
		handler = newDatadogHTTPHandler(ddTracer, handler)
	}

	return &http.Server{
		Handler:      handler,
		ReadTimeout:  timeouts.ReadTimeout,
		WriteTimeout: timeouts.WriteTimeout,
		IdleTimeout:  timeouts.IdleTimeout,
	}
}
```
