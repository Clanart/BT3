### Title
Wildcard CORS default on the EVM JSON-RPC server combined with locally-hosted-key signing endpoints (`eth_sendTransaction`/`eth_signTransaction`) enables cross-origin fund theft - ([File: evmrpc/send.go])

### Summary
The EVM JSON-RPC HTTP server ships with a default CORS policy of `*` (`cors_origins = "*"`), and separately exposes `eth_sendTransaction`/`eth_signTransaction` handlers that sign transactions using a locally-hosted keyring rather than requiring the caller to supply a pre-signed raw transaction. This is architecturally the same bug class as CVE-2017-14460: Parity's JSON-RPC server allowed any browser-origin site to reach wallet-signing RPC methods because CORS was not properly restricted, letting a malicious webpage silently move funds out of a node-hosted account.

### Finding Description
`evmrpc/config/config.go` defines the default EVM RPC configuration with an overly permissive CORS origin list: [1](#0-0) 

This value feeds directly into the CORS middleware applied to the JSON-RPC HTTP handler: [2](#0-1) 

Separately, `SendAPI` implements `eth_sendTransaction` and `eth_signTransaction`, which locate a private key for the `From` address in a locally hosted keyring and sign the transaction on the caller's behalf, instead of requiring the client to submit an already-signed raw transaction (as `eth_sendRawTransaction` does): [3](#0-2) 

When both conditions hold on a node — wildcard CORS (the shipped default) and the local-keyring signing endpoints reachable — any website a user visits in a browser that also has network access to the node's JSON-RPC port (e.g. `localhost:8545`, or a publicly exposed RPC endpoint) can issue a cross-origin `fetch()`/`XMLHttpRequest` that the browser will happily deliver because the server's CORS response grants access to any origin. The request causes the node to sign and broadcast a transaction using its hosted key without any additional authentication or explicit user consent — exactly the CORS/wallet-signing combination flagged in CVE-2017-14460 for Parity.

### Impact Explanation
If an operator or CI/dev harness runs a node with the local signing key(s) funded (a `SendAPI`-eligible keyring on the RPC host), a malicious website visited by anyone with network reach to that RPC port can trigger fund transfers or arbitrary contract calls signed by the hosted key — concrete fund loss without any transaction being explicitly authorized by the key owner outside of visiting a page. This matches the "unauthorized transfer" and "fund loss" impact categories in scope.

### Likelihood Explanation
Exploitation requires: (1) the default wildcard CORS (`cors_origins = "*"`) to remain unmodified — which is the shipped default, and (2) the node to have `eth_sendTransaction`/`eth_signTransaction` reachable with a populated hosted keyring. This is likely gated behind test/dev-only configuration (`getTestKeyring`, `EnableTestAPI`), which defaults to `false`, so exploitability under a fully vanilla production deployment is uncertain — I could not fully confirm from the available index whether `SendAPI`'s signing methods are registered on public RPC listeners by default or only when `EnableTestAPI`/similar dev flags are explicitly turned on. Where such flags are enabled (e.g. devnets, CI, or misconfigured RPC nodes), likelihood is high because the wildcard CORS default requires no additional attacker action beyond luring a visit to a malicious page.

### Recommendation
- Change the default `cors_origins` for the EVM RPC server from `"*"` to an empty list (disabling CORS by default), matching the safer defaults used by the Tendermint RPC (`cors-allowed-origins = []`) shown in `sei-tendermint/config/config.go`.
- Ensure `eth_sendTransaction`/`eth_signTransaction` (and any other method that signs with a node-hosted key) are strictly disabled outside of explicit dev/test flags, and are never exposed on the default HTTP/WS listeners regardless of CORS configuration.
- Document and enforce that any hosted-keyring signing endpoints require a companion allow-list (Vhosts) and non-wildcard CORS configuration before they can be enabled.

### Proof of Concept
1. Start a node with default EVM RPC config (`cors_origins = "*"`) and with `EnableTestAPI`/hosted-keyring signing enabled (dev/test configuration).
2. Fund the hosted test key associated with the keyring returned by `getTestKeyring` (`evmrpc/send.go:244`).
3. Host a malicious webpage that issues a cross-origin `fetch()` POST to `http://<node>:8545` with body `{"jsonrpc":"2.0","method":"eth_sendTransaction","params":[{"from":"<hosted-key-addr>","to":"<attacker-addr>","value":"0x..."}],"id":1}`.
4. Because CORS returns `Access-Control-Allow-Origin: *` (per `newCorsHandler`, `evmrpc/rpcstack.go:464-476`), the browser delivers the response and the node signs+broadcasts the transaction using the hosted key, transferring funds to the attacker without further authorization.

### Citations

**File:** evmrpc/config/config.go (L335-336)
```go
	CORSOrigins:                  "*",
	WSOrigins:                    "*",
```

**File:** evmrpc/rpcstack.go (L464-476)
```go
func newCorsHandler(srv http.Handler, allowedOrigins []string) http.Handler {
	// disable CORS support if user has not specified a custom CORS configuration
	if len(allowedOrigins) == 0 {
		return srv
	}
	c := cors.New(cors.Options{
		AllowedOrigins: allowedOrigins,
		AllowedMethods: []string{http.MethodPost, http.MethodGet},
		AllowedHeaders: []string{"*"},
		MaxAge:         600,
	})
	return c.Handler(srv)
}
```

**File:** evmrpc/send.go (L223-255)
```go
func (s *SendAPI) SendTransaction(ctx context.Context, args export.TransactionArgs) (result common.Hash, returnErr error) {
	startTime := time.Now()
	defer func() {
		recordMetricsWithError(ctx, "eth_sendTransaction", s.connectionType, startTime, returnErr, recover())
	}()
	if err := args.SetDefaults(ctx, s.backend, false); err != nil {
		return common.Hash{}, err
	}
	var unsignedTx = args.ToTransaction(ethtypes.LegacyTxType)
	signedTx, err := s.signTransaction(unsignedTx, args.From.Hex())
	if err != nil {
		return common.Hash{}, err
	}
	data, err := signedTx.MarshalBinary()
	if err != nil {
		return common.Hash{}, err
	}
	return s.SendRawTransaction(ctx, data)
}

func (s *SendAPI) signTransaction(unsignedTx *ethtypes.Transaction, from string) (*ethtypes.Transaction, error) {
	kb, err := getTestKeyring(s.homeDir)
	if err != nil {
		return nil, err
	}
	privKey, ok := getAddressPrivKeyMap(kb)[from]
	if !ok {
		return nil, errors.New("from address does not have hosted key")
	}
	chainId := s.keeper.ChainID(s.ctxProvider(LatestCtxHeight))
	signer := ethtypes.LatestSignerForChainID(chainId)
	return ethtypes.SignTx(unsignedTx, signer, privKey)
}
```
