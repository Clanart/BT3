### Title
Internal upstream-archive endpoint error leaked to unprivileged RPC caller - (File: networks/rpc/handler.go)

### Summary
When a Kaia execution node (EN) is configured with a fallback upstream archive endpoint (`UpstreamArchiveEN`), any unauthenticated JSON-RPC caller can trigger a state-trie lookup failure and receive back, in the JSON-RPC error response, the raw dial/transport error produced while the node attempted to relay the request to that internal upstream endpoint. This is directly analogous to CVE-2022-31143 (GLPI), where an unauthenticated error response leaked private setup information (internal host/connection details) that should never reach an untrusted caller.

### Finding Description
`runMethod` in `networks/rpc/handler.go` calls the requested RPC callback and, on failure, checks `shouldRequestUpstream(err)`: [1](#0-0) 

`shouldRequestUpstream` matches any error that wraps a `statedb.MissingNodeError` — i.e. any state/trie read whose backing node is missing locally (a very common, cheaply-triggerable condition for older/pruned state on a non-archive EN): [2](#0-1) [3](#0-2) 

When that condition is met, `requestUpstream` dials `UpstreamArchiveEN` (an internal, operator-configured URL not otherwise exposed to RPC clients) and, if the dial or the relayed call fails, returns the error to the original caller wrapped only with a generic prefix: [4](#0-3) 

The wrapped `err` here is whatever Go's `DialContext`/`CallContext` machinery in `networks/rpc/client.go` produces for a failed connection (e.g., DNS/TCP dial failures, TLS errors, connection-refused messages), which in Go's standard error formatting typically embeds the literal host:port or URL that was dialed. That value is `UpstreamArchiveEN`, an internal infrastructure endpoint configured by the node operator (analogous to GLPI's SMTP/CAS "setup" hosts), not something end users are meant to see. `msg.errorResponse(err)` then serializes `err.Error()` verbatim into the JSON-RPC `error.message` field returned over the public RPC transport: [5](#0-4) 

Thus, any transaction sender or plain RPC caller — with no special privileges — can force this code path (e.g., by calling `eth_getBalance`/`eth_call`/`eth_getStorageAt` for old/pruned state on a fallback-configured EN) and read back internal network/infrastructure details about the upstream archive endpoint whenever that upstream is unreachable or misconfigured.

### Impact Explanation
Disclosure of the internal `UpstreamArchiveEN` address (host, port, sometimes auth-bearing URL components if operators embed credentials in the URL) to any public, unauthenticated RPC caller is an information disclosure that can materially aid further attacks against the node's internal infrastructure (network reconnaissance, targeting the archive node directly, bypassing intended network segmentation). This mirrors the GLPI CVE class (Medium severity, CWE information exposure) — no direct funds/consensus impact, but a legitimate confidentiality violation of operator-configured internal topology reachable purely through a public RPC call.

### Likelihood Explanation
This requires an EN operator to have set `UpstreamArchiveEN` (a supported, documented Kaia flag) and for the upstream to be occasionally unreachable/erroring — a routine operational condition (network blips, upstream restarts, misconfiguration). Triggering the code path itself only requires an ordinary read RPC call against pruned/older state, which is trivially available to any public caller with no authentication or fee payment required.

### Recommendation
Do not propagate the raw underlying dial/transport error from `requestUpstream` to the client. Log the detailed error internally (as already done elsewhere via `logger.Error`) and return a generic, sanitized error (e.g., "upstream request failed") to the RPC caller, stripping any host/URL details before wrapping with `fmt.Errorf`.

### Proof of Concept
1. Configure a Kaia EN with `--rpc.upstream-archive-en` (or the corresponding `UpstreamArchiveEN` flag) pointing at an internal archive host, and ensure that host is temporarily unreachable (firewalled/down).
2. As an unauthenticated caller, send a standard JSON-RPC call referencing pruned/old state, e.g. `eth_getBalance(addr, "0x1")` for a block whose trie node has since been pruned, so that `TryGet`/`TryUpdate` returns a `statedb.MissingNodeError`.
3. Observe that `shouldRequestUpstream` matches and `requestUpstream` attempts to dial `UpstreamArchiveEN`; because the dial fails, the JSON-RPC response's `error.message` field contains the raw Go network error string, which includes the internal upstream host:port literally in the response body returned to the caller.

Note: I could not directly inspect the exact wording produced by `client/kaia_client.go`'s `DialContext` implementation (source was not fully retrievable in this session), so the precise error string format is inferred from Go's standard `net`/`http` error conventions rather than confirmed byte-for-byte; a Devin session with full file access would be needed to confirm the exact leaked substring.

### Citations

**File:** networks/rpc/handler.go (L480-497)
```go
func (h *handler) runMethod(ctx context.Context, msg *jsonrpcMessage, callb *callback, args []reflect.Value) *jsonrpcMessage {
	result, err := callb.call(ctx, msg.Method, args)
	if err != nil {
		// TODO-Kaia:
		// 1. The single URL may be extended to the list of URL.
		// 2. The URL (list or single) seems not modifiable at runtime.
		// 3. Any request can be relayed as well as the state lack.
		// 4. Make a new rpcErrorResponse struct.
		if UpstreamArchiveEN != "" && shouldRequestUpstream(err) {
			return requestUpstream(ctx, msg, args)
		}
		rpcErrorResponsesCounter.Inc(1)
		return msg.errorResponse(err)
	}

	rpcSuccessResponsesCounter.Inc(1)
	return msg.response(result)
}
```

**File:** networks/rpc/handler.go (L499-505)
```go
// shouldRequestUpstream is a function that determines whether must be requested upstream.
func shouldRequestUpstream(err error) bool {
	// Checks if the error contains MissingNodeError in the wrapped error chain.
	// MissingNodeError is a strong evidence that the node has no state and worth dialing the upstream.
	var missingNodeError *statedb.MissingNodeError
	return errors.As(err, &missingNodeError)
}
```

**File:** networks/rpc/handler.go (L507-529)
```go
// requestUpstream is the function to request upstream archive en
func requestUpstream(ctx context.Context, msg *jsonrpcMessage, args []reflect.Value) *jsonrpcMessage {
	ctx, cancel := context.WithTimeout(ctx, DefaultHTTPTimeouts.ExecutionTimeout)
	defer cancel()

	var result interface{}
	c, err := DialContext(ctx, UpstreamArchiveEN)
	if err == nil {
		defer c.Close()

		var params []interface{}
		for _, a := range args {
			params = append(params, a.Interface())
		}
		if err = c.CallContext(ctx, &result, msg.Method, params...); err == nil {
			rpcSuccessResponsesCounter.Inc(1)
			return msg.response(result)
		}
	}

	rpcErrorResponsesCounter.Inc(1)
	return msg.errorResponse(fmt.Errorf("from upstream rpc endpoint: %w", err))
}
```

**File:** storage/statedb/errors.go (L32-42)
```go
// MissingNodeError is returned by the trie functions (TryGet, TryUpdate, TryDelete)
// in the case where a trie node is not present in the local database. It contains
// information necessary for retrieving the missing node.
type MissingNodeError struct {
	NodeHash common.Hash // hash of the missing node
	Path     []byte      // hex-encoded path to the missing node
}

func (err *MissingNodeError) Error() string {
	return fmt.Sprintf("missing trie node %x (path %x)", err.NodeHash, err.Path)
}
```

**File:** networks/rpc/json.go (L120-149)
```go
func (msg *jsonrpcMessage) errorResponse(err error) *jsonrpcMessage {
	resp := errorMessage(err)
	resp.ID = msg.ID
	return resp
}

func (msg *jsonrpcMessage) response(result interface{}) *jsonrpcMessage {
	enc, err := json.Marshal(result)
	if err != nil {
		// TODO: wrap with 'internal server error'
		return msg.errorResponse(err)
	}
	return &jsonrpcMessage{Version: vsn, ID: msg.ID, Result: enc}
}

func errorMessage(err error) *jsonrpcMessage {
	msg := &jsonrpcMessage{Version: vsn, ID: null, Error: &jsonError{
		Code:    defaultErrorCode,
		Message: err.Error(),
	}}
	ec, ok := err.(Error)
	if ok {
		msg.Error.Code = ec.ErrorCode()
	}
	de, ok := err.(DataError)
	if ok {
		msg.Error.Data = de.ErrorData()
	}
	return msg
}
```
