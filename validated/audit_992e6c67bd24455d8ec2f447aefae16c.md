### Title
Unprotected concurrent read of `handler.serverSubs` map races with locked writes, causing an unhandled Go runtime panic (RPC-reachable DoS) - ([File: networks/rpc/handler.go])

### Summary
`handler.handleSubscribe` reads the `h.serverSubs` map (`len(h.serverSubs)`) without holding `h.subLock`, while every other accessor of that same map (`addSubscriptions`, `cancelServerSubscriptions`, `unsubscribe`) always takes `h.subLock` before reading/writing it. Because each incoming JSON-RPC/WebSocket message is dispatched to its own goroutine via `h.startCallProc`, a client that sends concurrent `*_subscribe` calls together with `*_unsubscribe` calls (or triggers connection teardown, which calls `cancelServerSubscriptions`) can cause a genuine, unsynchronized concurrent map read/write on `h.serverSubs` — the exact bug class described in the referenced CometBFT advisory (`redoSubscriptionsAfter` iterating the subscriptions map without the protecting mutex while another goroutine mutates it).

### Finding Description
The `handler` struct declares `subLock sync.Mutex` specifically to protect `serverSubs map[ID]*Subscription`: [1](#0-0) 

All the write/delete paths correctly acquire `subLock`:
- `addSubscriptions` (installs new subscriptions after a call completes): [2](#0-1) 
- `cancelServerSubscriptions` (invoked on connection close): [3](#0-2) 
- `unsubscribe` (the `*_unsubscribe` RPC callback): [4](#0-3) 

However, `handleSubscribe`, invoked for every `*_subscribe` RPC call, reads `len(h.serverSubs)` with **no lock held** to enforce `MaxSubscriptionPerWSConn`: [5](#0-4) 

Each inbound message (whether from `handleMsg` or `handleBatch`) is processed in its own goroutine launched by `startCallProc`, which is only synchronized via a `sync.WaitGroup`, not by any per-connection serialization of map access: [6](#0-5) [7](#0-6) 

Consequently, if a WebSocket client issues multiple concurrent `*_subscribe`/`*_unsubscribe` requests (or if the connection is closing concurrently with an in-flight `*_subscribe` call, triggering `close` → `cancelServerSubscriptions`), one goroutine can be iterating/reading `h.serverSubs` in `handleSubscribe` while another goroutine concurrently deletes/inserts entries in `h.serverSubs` under `subLock`. Because the reader does not take the lock, this is an unsynchronized concurrent map access, which Go's runtime detects and terminates the process with a fatal error ("fatal error: concurrent map read and map write"), or corrupts internal map state under `-race`.

### Impact Explanation
Any unauthenticated public RPC/WebSocket caller connected to a Kaia node can trigger this race purely by issuing ordinary `*_subscribe`/`*_unsubscribe` calls concurrently on the same WS connection, or by racing subscription requests against connection close. Go's runtime treats concurrent unsynchronized map access as a fatal, unrecoverable condition — the entire node process crashes, not just the connection. This is a remote, unauthenticated denial-of-service against a core Kaia component (the public RPC endpoint), and is reachable exactly as described by any public-RPC caller allowed by the rules.

### Likelihood Explanation
Likelihood is high given how easy it is to trigger: no special privileges, funds, or contract deployment are required — only the ability to open a WebSocket RPC connection and fire concurrent subscribe/unsubscribe requests (something any legitimate client library naturally does when it manages several subscriptions or reconnects rapidly). Because `MaxSubscriptionPerWSConn` checking happens on every subscribe call, high-frequency subscription churn under load or a purposely crafted client can reliably hit the race window.

### Recommendation
Protect the read at `networks/rpc/handler.go:439` with `h.subLock`, mirroring the CometBFT fix referenced in the report: acquire `h.subLock` (or use `atomic`/a length counter maintained under the same lock) before reading `len(h.serverSubs)` in `handleSubscribe`, ensuring every access path to `serverSubs` is consistently synchronized.

### Proof of Concept
1. Connect to a Kaia node's WebSocket RPC endpoint.
2. In a tight loop, concurrently fire many `*_subscribe` (e.g. `eth_subscribe`) and `*_unsubscribe` requests on the same connection from multiple goroutines (or simply close the connection while a subscribe call is in flight, triggering `handler.close` → `cancelServerSubscriptions`).
3. Run the client/server under `go test -race`, or simply repeat the load at scale in production; the concurrent unsynchronized access to `h.serverSubs` between `handleSubscribe` (unlocked read) and `addSubscriptions`/`unsubscribe`/`cancelServerSubscriptions` (locked writes) will eventually trigger Go's "fatal error: concurrent map read and map write", crashing the node process.

### Citations

**File:** networks/rpc/handler.go (L61-77)
```go
type handler struct {
	reg                  *serviceRegistry
	unsubscribeCb        *callback
	idgen                func() ID                      // subscription ID generator
	respWait             map[string]*requestOp          // active client requests
	clientSubs           map[string]*ClientSubscription // active client subscriptions
	callWG               sync.WaitGroup                 // pending call goroutines
	rootCtx              context.Context                // canceled by close()
	cancelRoot           func()                         // cancel function for rootCtx
	conn                 jsonWriter                     // where responses will be sent
	allowSubscribe       bool
	batchRequestLimit    int // max items per batch; 0 disables
	batchResponseMaxSize int // max total response bytes per batch; 0 disables

	subLock    sync.Mutex
	serverSubs map[ID]*Subscription
}
```

**File:** networks/rpc/handler.go (L199-226)
```go
// handleMsg handles a single message.
func (h *handler) handleMsg(msg *jsonrpcMessage) {
	rpcTotalRequestsCounter.Inc(1)
	if ok := h.handleImmediate(msg); ok {
		return
	}

	if atomic.LoadInt64(&pendingRequestCount) > pendingRequestLimit {
		rpcErrorResponsesCounter.Inc(1)
		err := &invalidRequestError{"server requests exceed the limit"}
		logger.Debug(fmt.Sprintf("request error %v\n", err))
		h.startCallProc(func(cp *callProc) {
			h.conn.writeJSON(cp.ctx, errorMessage(err))
		})
		return
	}

	h.startCallProc(func(cp *callProc) {
		answer := h.handleCallMsg(cp, msg)
		h.addSubscriptions(cp.notifiers)
		if answer != nil {
			h.conn.writeJSON(cp.ctx, answer)
		}
		for _, n := range cp.notifiers {
			n.activate()
		}
	})
}
```

**File:** networks/rpc/handler.go (L274-283)
```go
func (h *handler) addSubscriptions(nn []*Notifier) {
	h.subLock.Lock()
	defer h.subLock.Unlock()

	for _, n := range nn {
		if sub := n.takeSubscription(); sub != nil {
			h.serverSubs[sub.ID] = sub
		}
	}
}
```

**File:** networks/rpc/handler.go (L286-295)
```go
func (h *handler) cancelServerSubscriptions(err error) {
	h.subLock.Lock()
	defer h.subLock.Unlock()

	for id, s := range h.serverSubs {
		s.err <- err
		close(s.err)
		delete(h.serverSubs, id)
	}
}
```

**File:** networks/rpc/handler.go (L297-310)
```go
// startCallProc runs fn in a new goroutine and starts tracking it in the h.calls wait group.
func (h *handler) startCallProc(fn func(*callProc)) {
	atomic.AddInt64(&pendingRequestCount, 1)
	rpcPendingRequestsCount.Inc(1)
	h.callWG.Add(1)
	go func() {
		ctx, cancel := context.WithCancel(h.rootCtx)
		defer h.callWG.Done()
		defer cancel()
		defer atomic.AddInt64(&pendingRequestCount, -1)
		defer rpcPendingRequestsCount.Dec(1)
		fn(&callProc{ctx: ctx})
	}()
}
```

**File:** networks/rpc/handler.go (L432-445)
```go
// handleSubscribe processes *_subscribe method calls.
func (h *handler) handleSubscribe(cp *callProc, msg *jsonrpcMessage) *jsonrpcMessage {
	if !h.allowSubscribe {
		rpcErrorResponsesCounter.Inc(1)
		return msg.errorResponse(ErrNotificationsUnsupported)
	}

	if int32(len(h.serverSubs)) >= MaxSubscriptionPerWSConn {
		rpcErrorResponsesCounter.Inc(1)
		return msg.errorResponse(&callbackError{
			fmt.Sprintf("Maximum %d subscriptions are allowed for a websocket connection. "+
				"The limit can be updated with 'admin_setMaxSubscriptionPerWSConn' API", MaxSubscriptionPerWSConn),
		})
	}
```

**File:** networks/rpc/handler.go (L531-543)
```go
// unsubscribe is the callback function for all *_unsubscribe calls.
func (h *handler) unsubscribe(ctx context.Context, id ID) (bool, error) {
	h.subLock.Lock()
	defer h.subLock.Unlock()

	s := h.serverSubs[id]
	if s == nil {
		return false, ErrSubscriptionNotFound
	}
	close(s.err)
	delete(h.serverSubs, id)
	return true, nil
}
```
