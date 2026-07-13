### Title
Password Parameter Silently Discarded in `personal_sign` and `personal_sendTransaction`, Enabling Unauthorized Signing of Arbitrary Data and Transactions - (File: `rpc/namespaces/ethereum/personal/api.go`)

### Summary
The `personal_sign` and `personal_sendTransaction` JSON-RPC methods accept a `password` parameter per the Ethereum `personal_` namespace specification, but Ethermint's implementation silently discards it via Go blank identifiers (`_ string`). No password verification is performed before signing. Any caller who can reach the JSON-RPC endpoint can sign arbitrary data or broadcast transactions from any account held in the node's keyring without knowing the account password.

### Finding Description
The `PrivateAccountAPI.Sign` and `PrivateAccountAPI.SendTransaction` methods in `rpc/namespaces/ethereum/personal/api.go` declare the password argument as `_ string`, meaning it is accepted over the wire but never forwarded to any authentication layer.

`Sign` at line 145:
```go
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
    api.logger.Debug("personal_sign", "data", data, "address", addr.String())
    return api.backend.Sign(addr, data)   // password never passed
}
``` [1](#0-0) 

`SendTransaction` at line 131:
```go
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
    api.logger.Debug("personal_sendTransaction", "address", args.To.String())
    return api.backend.SendTransaction(args)   // password never passed
}
``` [2](#0-1) 

The docstring for `Sign` explicitly states *"The key used to calculate the signature is decrypted with the given password"*, but the backend `Backend.Sign` and `Backend.SendTransaction` do not accept a password parameter at all. [3](#0-2) [4](#0-3) 

Compounding this, `UnlockAccount` is a permanent no-op that always returns `(false, nil)` without error, so the account-locking mechanism that is supposed to gate signing is entirely non-functional. [5](#0-4) 

The analog to the original report is direct: just as the iOS wallet keeps the mnemonic accessible without requiring re-authentication, Ethermint keeps every keyring-managed private key permanently accessible for signing without ever verifying the caller's password.

### Impact Explanation
An unprivileged caller who can reach the JSON-RPC port can:

1. Call `personal_sign(data, <victim_address>, "")` with any arbitrary payload and receive a valid ECDSA signature from the victim's key — enabling phishing, permit-style approvals, or EIP-712 typed-data forgeries.
2. Call `personal_sendTransaction({from: <victim_address>, to: attacker, value: ...}, "")` to drain funds from any account whose key is stored in the node's keyring, without knowing the password.

This is a direct authentication bypass on the signing path, matching the allowed High impact: *"Ethereum transaction … signer verification bypass enabling … forged execution, or unauthorized account/code mutation."*

### Likelihood Explanation
The JSON-RPC server is commonly exposed on port 8545. Operators who use `personal_importRawKey` or `personal_newAccount` to manage hot-wallet keys on the node (a documented and supported workflow) are directly affected. Any process or user with network access to the RPC port — including dApps, scripts, or a compromised co-located service — can exploit this without any privilege escalation.

### Recommendation
- Pass the `password` string through to the backend and use it to decrypt the key before signing, matching the Ethereum `personal_` namespace specification.
- Implement `UnlockAccount` / `LockAccount` properly, or reject signing requests for locked accounts.
- Until fixed, document that the `personal_` namespace provides no password protection and should not be exposed on any network-accessible interface.

### Proof of Concept
```bash
# 1. Import a key with a strong password via personal_importRawKey
curl -X POST http://localhost:8545 -d '{
  "jsonrpc":"2.0","method":"personal_importRawKey",
  "params":["<hex_privkey>","strongpassword"],"id":1}'

# 2. Sign arbitrary data with a WRONG / empty password — succeeds
curl -X POST http://localhost:8545 -d '{
  "jsonrpc":"2.0","method":"personal_sign",
  "params":["0xdeadbeef","<address>","WRONG_PASSWORD"],"id":2}'
# Returns a valid 65-byte ECDSA signature — password was never checked.

# 3. Send a transaction draining funds — no password required
curl -X POST http://localhost:8545 -d '{
  "jsonrpc":"2.0","method":"personal_sendTransaction",
  "params":[{"from":"<address>","to":"<attacker>","value":"0x..."},""],"id":3}'
# Transaction is signed and broadcast — password was never checked.
```

The root cause is confirmed at:
- `rpc/namespaces/ethereum/personal/api.go` lines 131–134 (`SendTransaction`, password `_ string` discarded) [2](#0-1) 
- `rpc/namespaces/ethereum/personal/api.go` lines 145–148 (`Sign`, password `_ string` discarded) [1](#0-0) 
- `rpc/backend/sign_tx.go` lines 128–148 (`Backend.Sign` takes no password) [3](#0-2)

### Citations

**File:** rpc/namespaces/ethereum/personal/api.go (L122-126)
```go
func (api *PrivateAccountAPI) UnlockAccount(_ context.Context, addr common.Address, _ string, _ *uint64) (bool, error) {
	api.logger.Debug("personal_unlockAccount", "address", addr.String())
	// TODO: Not supported. See underlying issue  https://github.com/99designs/keyring/issues/85
	return false, nil
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L131-134)
```go
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
	api.logger.Debug("personal_sendTransaction", "address", args.To.String())
	return api.backend.SendTransaction(args)
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L145-148)
```go
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
	api.logger.Debug("personal_sign", "data", data, "address", addr.String())
	return api.backend.Sign(addr, data)
}
```

**File:** rpc/backend/sign_tx.go (L37-79)
```go
// SendTransaction sends transaction based on received args using Node's key to sign it
func (b *Backend) SendTransaction(args evmtypes.TransactionArgs) (common.Hash, error) {
	// Look up the wallet containing the requested signer
	_, err := b.clientCtx.Keyring.KeyByAddress(sdk.AccAddress(args.GetFrom().Bytes()))
	if err != nil {
		b.logger.Error("failed to find key in keyring", "address", args.GetFrom(), "error", err.Error())
		return common.Hash{}, fmt.Errorf("failed to find key in the node's keyring; %s; %s", keystore.ErrNoMatch, err.Error())
	}

	if args.ChainID != nil && (b.chainID).Cmp((*big.Int)(args.ChainID)) != 0 {
		return common.Hash{}, fmt.Errorf("chainId does not match node's (have=%v, want=%v)", args.ChainID, (*hexutil.Big)(b.chainID))
	}

	args, err = b.SetTxDefaults(args)
	if err != nil {
		return common.Hash{}, err
	}

	msg := args.ToTransaction()
	if err := msg.ValidateBasic(); err != nil {
		b.logger.Debug("tx failed basic validation", "error", err.Error())
		return common.Hash{}, err
	}

	bn, err := b.BlockNumber()
	if err != nil {
		b.logger.Debug("failed to fetch latest block number", "error", err.Error())
		return common.Hash{}, err
	}

	header, err := b.CurrentHeader()
	if err != nil {
		b.logger.Debug("failed to fetch latest block header", "error", err.Error())
		return common.Hash{}, err
	}

	signer := ethtypes.MakeSigner(b.ChainConfig(), new(big.Int).SetUint64(uint64(bn)), header.Time)

	// Sign transaction
	if err := msg.Sign(signer, b.clientCtx.Keyring); err != nil {
		b.logger.Debug("failed to sign tx", "error", err.Error())
		return common.Hash{}, err
	}
```

**File:** rpc/backend/sign_tx.go (L128-148)
```go
// Sign signs the provided data using the private key of address via Geth's signature standard.
func (b *Backend) Sign(address common.Address, data hexutil.Bytes) (hexutil.Bytes, error) {
	from := sdk.AccAddress(address.Bytes())

	_, err := b.clientCtx.Keyring.KeyByAddress(from)
	if err != nil {
		b.logger.Error("failed to find key in keyring", "address", address.String())
		return nil, fmt.Errorf("%s; %s", keystore.ErrNoMatch, err.Error())
	}

	// Apply EIP-191 signed-message prefix to domain-separate personal
	// signatures from transaction signatures (matching Geth's eth_sign).
	signature, _, err := b.clientCtx.Keyring.SignByAddress(from, accounts.TextHash(data), signingtypes.SignMode_SIGN_MODE_TEXTUAL)
	if err != nil {
		b.logger.Error("keyring.SignByAddress failed", "address", address.Hex())
		return nil, err
	}

	signature[crypto.RecoveryIDOffset] += 27 // Transform V from 0/1 to 27/28 according to the yellow paper
	return signature, nil
}
```
