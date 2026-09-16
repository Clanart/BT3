### Title
gRPC RPC endpoint bypasses the `Public`/module access-control enforced by HTTP and WebSocket endpoints - (File: `networks/grpc/gServer.go`, `node/node.go`)

### Summary
Apache Traffic Server's CVE-2024-56195 stems from intercept plugins being invoked outside the normal access-control path applied to regular requests. The analogous pattern in this codebase is that Kaia's `rpc.API` access-control model (the `Public` flag and per-transport module whitelist) is enforced only by the HTTP and WebSocket transports, while the network-facing gRPC transport is started with the full, unfiltered API set.

### Finding Description
Kaia's RPC framework marks each `rpc.API` with a `Public` flag and an `IPCOnly` flag: [1](#0-0) 

`StartHTTPEndpoint` and `StartWSEndpoint` both explicitly gate registration on `!api.IPCOnly && (whitelist[api.Namespace] || (len(whitelist)==0 && api.Public))`, so unless an operator opts a namespace into the whitelist, only APIs flagged `Public: true` are exposed over HTTP/WS: [2](#0-1) [3](#0-2) 

By contrast, `StartIPCEndpoint` registers every API in the list unconditionally, which is safe by design because IPC is a local, trusted-only channel: [4](#0-3) 

`node.startRPC` builds one combined `apis` slice (containing privileged `admin`/`debug` services such as `AdminNetworkNodeAPI`, `AdminChainCNAPI`, and debug tracers) and passes it, filtered appropriately, to `startHTTP`/`startWS` (which forward `n.config.HTTPModules`/`WSModules` for whitelist filtering), but passes the exact same *unfiltered* `apis` slice to `n.startgRPC(apis)`, with no module/whitelist argument at all — mirroring the IPC call pattern rather than the HTTP/WS pattern: [5](#0-4) 

The gRPC service (`kaiaServer.Call`/`BiCall` in `networks/grpc/gServer.go`) accepts arbitrary JSON-RPC payloads from any network peer and dispatches them through `kns.handler`, which is the `rpc.Server` built from that unfiltered API set: [6](#0-5) [7](#0-6) 

Privileged APIs registered without `Public: true` (e.g., `admin` namespace network/chain-management methods, `debug` methods with `IPCOnly` intended to keep them off remote transports) are defined here: [8](#0-7) [9](#0-8) 

Because `startgRPC` receives and (based on the call-site symmetry with `startIPC`) registers this same unfiltered list, any caller able to reach the gRPC listener over the network can invoke methods that were deliberately restricted to IPC-only/non-public transports on HTTP/WS — an access-control bypass structurally identical to ATS's unauthenticated intercept-plugin invocation.

### Impact Explanation
If the gRPC transport is enabled and reachable, a remote unauthenticated caller can invoke node-management (`admin.*`) and debug (`debug.*`) methods intended to be protected (peer management, chain export/import, unsafe tracers, node config disclosure) that were never meant to be exposed off the local machine. This can lead to information disclosure of node/chain internals, node-state manipulation (peer add/remove, RPC endpoint start/stop), and potential downstream state-divergence or denial-of-service against a public node.

### Likelihood Explanation
Medium: exploitation requires the operator to have enabled the gRPC listener (`startgRPC`), but once enabled it listens on the network and accepts arbitrary JSON-RPC-shaped payloads via `Call`/`BiCall` with no authentication layer visible in `gServer.go`, so any caller with network access to the port can attempt to invoke restricted methods.

### Recommendation
Apply the same `Public`/module-whitelist/`IPCOnly` filtering used in `StartHTTPEndpoint`/`StartWSEndpoint` to the API set registered for the gRPC transport, so `startgRPC` only exposes APIs flagged `Public: true` (or explicitly whitelisted), and never registers `IPCOnly` services.

### Proof of Concept
Conceptual PoC (cannot be fully executed without live node config confirming gRPC is enabled and IPC-only APIs are indeed forwarded unfiltered to `startgRPC`, since the exact body of `startgRPC` was not retrievable in this review):
1. Start a Kaia node with the gRPC endpoint enabled.
2. From a remote host, open a gRPC connection to the node and call `kaiaServer.Call` with a JSON-RPC payload such as `{"jsonrpc":"2.0","method":"admin_removePeer","params":["<victim-kni-url>"],"id":1}`.
3. If `admin` namespace (non-`Public`) methods are registered on the gRPC handler the same way they are excluded from public HTTP/WS, the call succeeds despite the method never being intended for unauthenticated remote callers, confirming the access-control bypass.

Note: I was unable to directly view the body of `n.startgRPC` in `node/node.go` (only the call site was retrievable), so the exact registration logic inside `startgRPC` should be verified to confirm whether it indeed omits the `Public`/whitelist check present in `StartHTTPEndpoint`/`StartWSEndpoint`. This should be validated in a full session with complete file access before treating this as a confirmed, exploitable finding.

### Citations

**File:** networks/rpc/types.go (L37-44)
```go
// API describes the set of methods offered over the RPC interface
type API struct {
	Namespace string      // namespace under which the rpc methods of Service are exposed
	Version   string      // api version for DApp's
	Service   interface{} // receiver instance which holds the methods
	Public    bool        // indication if the methods must be considered safe for public use
	IPCOnly   bool        // only accessible to IPC
}
```

**File:** networks/rpc/endpoints.go (L42-53)
```go
	for _, api := range apis {
		if api.Namespace == "klay" {
			api.Namespace = "kaia"
		}

		if !api.IPCOnly && (whitelist[api.Namespace] || (len(whitelist) == 0 && api.Public)) {
			if err := handler.RegisterName(api.Namespace, api.Service); err != nil {
				return nil, nil, err
			}
			logger.Debug("HTTP registered", "namespace", api.Namespace)
		}
	}
```

**File:** networks/rpc/endpoints.go (L79-90)
```go
	for _, api := range apis {
		if api.Namespace == "klay" {
			api.Namespace = "kaia"
		}

		if !api.IPCOnly && (exposeAll || whitelist[api.Namespace] || (len(whitelist) == 0 && api.Public)) {
			if err := handler.RegisterName(api.Namespace, api.Service); err != nil {
				return nil, nil, err
			}
			logger.Debug("WebSocket registered", "service", api.Service, "namespace", api.Namespace)
		}
	}
```

**File:** networks/rpc/endpoints.go (L106-116)
```go
	handler := NewServer()
	for _, api := range apis {
		if api.Namespace == "klay" {
			api.Namespace = "kaia"
		}

		if err := handler.RegisterName(api.Namespace, api.Service); err != nil {
			return nil, nil, err
		}
		logger.Debug("IPC registered", "namespace", api.Namespace)
	}
```

**File:** node/node.go (L341-375)
```go
func (n *Node) startRPC(services map[reflect.Type]Service) error {
	apis := n.apis()
	for _, service := range services {
		apis = append(apis, service.APIs()...)
	}
	// Start the various API endpoints, terminating all in case of errors
	if err := n.startInProc(apis); err != nil {
		return err
	}
	if err := n.startIPC(apis); err != nil {
		n.stopInProc()
		return err
	}

	if err := n.startHTTP(n.httpEndpoint, apis, n.config.HTTPModules, n.config.HTTPCors, n.config.HTTPVirtualHosts, n.config.HTTPTimeouts); err != nil {
		n.stopIPC()
		n.stopInProc()
		return err
	}
	if err := n.startWS(n.wsEndpoint, apis, n.config.WSModules, n.config.WSOrigins, n.config.WSExposeAll); err != nil {
		n.stopHTTP()
		n.stopIPC()
		n.stopInProc()
		return err
	}

	// start gRPC server
	if err := n.startgRPC(apis); err != nil {
		n.stopHTTP()
		n.stopIPC()
		n.stopInProc()
		return err
	}
	// All API endpoints started successfully
	n.rpcAPIs = apis
```

**File:** node/node.go (L729-758)
```go
func (n *Node) apis() []rpc.API {
	rpcApi := []rpc.API{
		{
			Namespace: "admin",
			Version:   "1.0",
			Service:   NewAdminNetworkNodeAPI(n),
		}, {
			Namespace: "admin",
			Version:   "1.0",
			Service:   NewAdminNodeAPI(n),
			Public:    true,
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   NewDebugNodeAPI(n),
		}, {
			Namespace: "kaia",
			Version:   "1.0",
			Service:   NewKaiaNodeAPI(n),
			Public:    true,
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   debug.Handler,
			IPCOnly:   n.config.DisableUnsafeDebug,
		},
	}

	return rpcApi
}
```

**File:** networks/grpc/gServer.go (L64-67)
```go
// kaiaServer is an implementation of KlaytnNodeServer.
type kaiaServer struct {
	handler *rpc.Server
}
```

**File:** networks/grpc/gServer.go (L217-259)
```go
// general RPC call, such as one-to-one communication
func (kns *kaiaServer) Call(ctx context.Context, request *RPCRequest) (*RPCResponse, error) {
	if isJSONRPCNotification(request.Params) {
		return nil, status.Error(codes.InvalidArgument, "JSON-RPC notifications are not supported over gRPC unary Call")
	}

	var (
		err      error
		writeErr = make(chan error, 1)
		readErr  = make(chan error, 1)
		writeOk  = make(chan []byte, 1)
	)

	preader := bytes.NewReader(request.Params)

	var res bytes.Buffer
	writer := &bufWriter{&res, writeErr, writeOk}

	// Create a custom encode/decode pair to enforce payload size and number encoding
	encoder := func(v interface{}) error {
		msg, err := json.Marshal(v)
		if err != nil {
			return err
		}
		_, err = writer.Write(msg)
		if err != nil {
			writeErr <- err
			return err
		}
		return err
	}
	decoder := func(v interface{}) error {
		dec := json.NewDecoder(preader)
		dec.UseNumber()
		err := dec.Decode(v)
		if err != nil {
			readErr <- err
		}
		return err
	}

	reader := bufio.NewReaderSize(preader, common.MaxRequestContentLength)
	kns.handler.ServeSingleRequest(ctx, rpc.NewFuncCodec(&grpcReadWriteNopCloser{reader, writer}, encoder, decoder))
```

**File:** node/cn/backend.go (L789-812)
```go
			Namespace: "admin",
			Version:   "1.0",
			Service:   kaiaDownloaderSyncAPI,
		}, {
			Namespace: "admin",
			Version:   "1.0",
			Service:   NewAdminChainCNAPI(s),
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   NewDebugCNAPI(s),
			Public:    false,
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   tracers.NewAPI(s.APIBackend),
			Public:    false,
		}, {
			Namespace: "debug",
			Version:   "1.0",
			Service:   tracers.NewUnsafeAPI(s.APIBackend),
			Public:    false,
			IPCOnly:   s.config.DisableUnsafeDebug,
		}, {
```
