### Title
Missing Domain Separation in `associate()` / `MsgAssociate` Signature Verification Allows Cross-Context Signature Replay to Force Non-Consensual EVM↔Sei Address Association - (File: `precompiles/addr/addr.go`, `utils/helpers/address.go`, `x/evm/types/message_associate.go`)

### Summary
The `addr` precompile's `associate` method and the equivalent `MsgAssociate` Cosmos message accept a raw `v/r/s` signature over an arbitrary, caller-supplied `customMessage` string and recover the signer purely from `keccak256(customMessage)`, with no chain ID, contract address, or Sei-specific domain separator baked into the hash that is actually verified on-chain.

### Finding Description
`associate()` computes `customMessageHash := crypto.Keccak256Hash([]byte(customMessage))` and recovers `evmAddr`/`seiAddr`/`pubkey` directly from that hash with no additional binding. [1](#0-0) 

`GetAddresses`/`RecoverPubkey` simply `ecrecover` the pubkey from whatever hash is passed in, without checking that the hash corresponds to a Sei-specific or chain-specific payload. [2](#0-1) 

The intended client convention wraps `customMessage` in the standard EIP-191 `personal_sign` envelope (`"\x19Ethereum Signed Message:\n<len><message>"`) before hashing, as shown in the test/tooling code, but this envelope is a client-side convention only — the precompile hashes exactly the bytes it is given, with no requirement that they contain any Sei-specific marker, chain ID, or purpose string. [3](#0-2) 

Because standard wallets (MetaMask, etc.) compute `personal_sign` signatures by hashing exactly this same EIP-191 envelope internally, **any** `personal_sign` signature a user has ever produced for **any** unrelated dApp/message (logins, NFT allowlist proofs, "Sign-In-With-Ethereum" challenges, etc.) is, byte-for-byte, a valid `(v, r, s, customMessage)` tuple that can be replayed into Sei's `associate()`/`MsgAssociate` to force that user's true (pubkey-derived) Sei address to be permanently and unconditionally linked to their EVM address — without the user ever intending to interact with Sei at all. This is structurally identical to the reported CSRF bug class: a security-relevant token (the signature) is not scoped to the security context it authorizes (chain / purpose), so it can be captured in one context and replayed in a completely different one to trigger a state-changing action.

The `MsgAssociate` proto/message layer carries the same unscoped `custom_message` field with no additional validation beyond a length check. [4](#0-3) [5](#0-4) 

Notably, the codebase already recognizes this exact class of root cause elsewhere: the EIP-7702 `SetCode` authority pre-association logic was hardened specifically because forcing early/unintended association "migrat[es] their direct-cast balance and orphan[s] staking/distribution state created under the direct-cast identity," which "can then halt the chain via the distribution validator-removal hook," and chain-ID scoping was added there as the fix. [6](#0-5) 
The `associate()`/`MsgAssociate` path exercises the very same `AssociateAddresses` state transition but has no equivalent scoping of the credential that triggers it — the credential (signature) can be sourced from a completely different, non-Sei context.

### Impact Explanation
Forcing an unassociated user's true Sei address to be associated with their EVM address without their consent removes user control over the timing of a permanent, irreversible state transition (`AssociateAddresses`). Per the project's own documented root-cause analysis for the structurally identical SetCode case, premature/forced association can migrate direct-cast balances and orphan in-flight staking/distribution state built under the direct-cast identity, which is described as being able to halt the chain via the distribution validator-removal hook. Any account that has accumulated direct-cast-identity state (balances, delegations) prior to intending to associate is exposed to this same class of state corruption the moment an attacker replays an unrelated, publicly-observable `personal_sign` signature through `associate()`/`MsgAssociate`.

### Likelihood Explanation
The attack requires no cooperation from the victim beyond having produced any ordinary `personal_sign` signature anywhere (an extremely common, low-friction wallet interaction), and the replay transaction is a normal, unprivileged EVM/Cosmos transaction that any address can submit calling the public `addr` precompile at `0x0000000000000000000000000000000000001004` or broadcasting `MsgAssociate`. No special privileges, timing races, or validator cooperation are needed, making this readily reachable by any public-RPC client or CosmWasm/EVM caller.

### Recommendation
Bind the association signature to a Sei-specific, chain-scoped domain: include the chain ID, a fixed contract/module domain tag (e.g. "sei-associate"), and ideally the target Sei address itself in the hashed payload (or adopt EIP-712 typed data with a proper `domain.chainId`/`verifyingContract`), and reject `customMessage` values that do not match this required, Sei-specific template. This mirrors the fix already applied to the EIP-7702 authority pre-association path (`AuthorityToPreAssociate`'s chain ID check) and eliminates cross-context signature reuse.

### Proof of Concept
1. Victim signs any ordinary `personal_sign` message `M` for an unrelated dApp with their EVM wallet, producing `(v, r, s)` over `keccak256("\x19Ethereum Signed Message:\n" + len(M) + M)`.
2. This `(v, r, s, "\x19Ethereum Signed Message:\n" + len(M) + M)` tuple is publicly observable (e.g., posted on the third-party dApp, or captured off-chain).
3. Attacker submits it as `associate(v-27, r, s, "\x19Ethereum Signed Message:\n" + len(M) + M)` to the Sei `addr` precompile, per `precompiles/addr/addr.go:160-202`, or wraps it into `MsgAssociate`.
4. `crypto.Keccak256Hash` reproduces the exact same hash the victim's wallet signed, `GetAddresses` recovers the victim's true pubkey/EVM/Sei addresses, and `associateAddresses` permanently links them — even though the victim never intended to interact with Sei.

### Citations

**File:** precompiles/addr/addr.go (L193-202)
```go
	// Derive addresses
	vBig = new(big.Int).Add(vBig, utils.Big27)

	customMessageHash := crypto.Keccak256Hash([]byte(customMessage))
	evmAddr, seiAddr, pubkey, err := helpers.GetAddresses(vBig, rBig, sBig, customMessageHash)
	if err != nil {
		return nil, 0, err
	}

	return p.associateAddresses(ctx, method, evmAddr, seiAddr, pubkey)
```

**File:** utils/helpers/address.go (L44-61)
```go
func GetAddresses(V *big.Int, R *big.Int, S *big.Int, data common.Hash) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	pubkey, err := RecoverPubkey(data, R, S, V, true)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}

	return GetAddressesFromPubkeyBytes(pubkey)
}

func GetAddressesFromPubkeyBytes(pubkey []byte) (common.Address, sdk.AccAddress, cryptotypes.PubKey, error) {
	evmAddr, err := PubkeyToEVMAddress(pubkey)
	if err != nil {
		return common.Address{}, sdk.AccAddress{}, nil, err
	}
	seiPubkey := PubkeyBytesToSeiPubKey(pubkey)
	seiAddr := sdk.AccAddress(seiPubkey.Address())
	return evmAddr, seiAddr, &seiPubkey, nil
}
```

**File:** utils/helpers/address.go (L149-156)
```go
// already associated. Mirroring validateAuthorization is essential to security: the
// authorization sig hash is computed from the auth's own ChainID, so recovery and
// auth.Authority() succeed for an authorization signed for ANY chain. Without these checks a
// publicly-visible authorization a user signed for another chain (e.g. Ethereum mainnet)
// could be replayed in a sponsored Sei SetCode tx to force-associate them — migrating their
// direct-cast balance and orphaning staking/distribution state — even though the EVM skips
// the wrong-chain authorization and installs no delegation.
func AuthorityToPreAssociate(ctx sdk.Context, k AuthorizationStateReader, auth ethtypes.SetCodeAuthorization) (common.Address, sdk.AccAddress, cryptotypes.PubKey, bool) {
```

**File:** contracts/test/EVMPrecompileTest.js (L70-77)
```javascript
            const message = `Please sign this message to link your EVM and Sei addresses. No SEI will be spent as a result of this signature.\n\n`;
            const messageLength = Buffer.from(message, 'utf8').length;
            const signatureHex = await unassociatedWallet.signMessage(message);

            const sig = hre.ethers.Signature.from(signatureHex);
            
            const appendedMessage = `\x19Ethereum Signed Message:\n${messageLength}${message}`;
            const associatedAddrs = await addr.associate(`0x${sig.v-27}`, sig.r, sig.s, appendedMessage)
```

**File:** proto/evm/tx.proto (L82-87)
```text
message MsgAssociate {
  string sender = 1;
  string custom_message = 2;
}

message MsgAssociateResponse {}
```

**File:** x/evm/types/message_associate.go (L38-48)
```go
func (msg *MsgAssociate) ValidateBasic() error {
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return sdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "Invalid sender address (%s)", err)
	}
	if len(msg.CustomMessage) > MaxAssociateCustomMessageLength {
		return sdkerrors.Wrapf(sdkerrors.ErrTxTooLarge, "custom message can have at most 64 characters")
	}

	return nil
}
```
