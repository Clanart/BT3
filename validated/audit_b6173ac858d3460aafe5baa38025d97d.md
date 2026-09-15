### Title
Unbounded Ecrecover loop in the `VALIDATE_SENDER` precompile allows computational DoS before signature-count is bounded - ([File: blockchain/vm/contracts.go])

### Summary
The `validateSender` precompiled contract (`VALIDATE_SENDER`) parses caller-supplied calldata into an arbitrary number of 65-byte signature chunks and performs an expensive `secp256k1` ECDSA public-key recovery (`crypto.Ecrecover`) for **every** chunk *before* the account-key bound (`accountkey.MaxNumKeysForMultiSig = 10`) is ever consulted. The number of signatures processed is derived purely from `len(input)`, with no upper bound enforced prior to the recovery loop, mirroring the notation bug class where a party controls the number of "signatures" that get iterated/verified without a cap, causing disproportionate CPU work relative to the (attacker-controlled) size of a single reachable call.

### Finding Description
`validateSender` computes `numSigs := len(ptr) / common.SignatureLength` from the raw calldata length and then loops `numSigs` times calling `crypto.Ecrecover` (an expensive elliptic-curve point-recovery operation) for each chunk: [1](#0-0) 

Only *after* this loop completes does the code look up the account's `AccountKey` and call `accountkey.ValidateAccountKey`, which is where the `MaxNumKeysForMultiSig` (10) bound is enforced (inside `AccountKeyWeightedMultiSig.Validate`/`SigValidationGas`): [2](#0-1) [3](#0-2) 

This is structurally the same bug class as the `notation` advisory: the code performs unit work (cryptographic verification) proportional to an attacker/caller-supplied count *before* applying the bound that is supposed to cap that count. In `notation`, the unbounded count was "signatures returned by a malicious registry"; here it is "signature chunks packed into a single precompile call's input," reachable directly from EVM execution triggered by any contract call (deployable/callable by an unprivileged contract deployer or transaction sender) — there is no cap on `input` length inside `validateSender` itself (the only external limits are the generic transaction-data-size/gas-limit constraints, not a limit tied to `MaxNumKeysForMultiSig`).

### Impact Explanation
Every `Ecrecover` call is a nontrivial secp256k1 computation. Because `numSigs` is derived from raw calldata length with no early bound check against `MaxNumKeysForMultiSig`, a caller can supply calldata containing far more than 10 signature-sized chunks, forcing the node to execute many superfluous elliptic-curve recoveries for a call that can never validate successfully (since `ValidateAccountKey`/`AccountKeyWeightedMultiSig.Validate` will reject once `numSigs > len(a.Keys)` for Istanbul-fork accounts, or otherwise never reach a legitimate success state for the excess signatures). If the precompile's gas metering does not charge proportionally to the actual number of `Ecrecover` invocations performed (this could not be fully confirmed from available `RequiredGas` source for `validateSender`), this becomes an underpriced/CPU-amplification vector: a single transaction can force disproportionate computation on every validating/re-executing node relative to the gas paid, which is a classic EVM computation-cost-metering DoS impacting block processing across the network — within scope as a "computation-cost metering" analog.

### Likelihood Explanation
Likelihood is Medium: the path is trivially reachable — any address (a plain unprivileged contract deployer/caller) can invoke the `VALIDATE_SENDER` precompile via a `CALL` with attacker-chosen `input` length, requiring no privileged role, validator status, or peer compromise. The main open question (not fully verifiable from the retrieved code) is whether `RequiredGas` for this precompile prices gas linearly and sufficiently per `Ecrecover` call; if it does so accurately, exploitation is limited to "the attacker pays for their own DoS" (still a real, if self-limiting, resource waste per the notation-style bug class), whereas if pricing under-accounts for the number of signature chunks relative to the true CPU cost, the DoS amplification is much more severe.

### Recommendation
Enforce the `MaxNumKeysForMultiSig` (or the actual number of keys registered for `from`'s applicable role) as an upper bound on `numSigs` in `validateSender` **before** entering the `Ecrecover` loop, mirroring the check already present in `AccountKeyWeightedMultiSig.Validate`/`SigValidationGas`. Additionally, verify/align `RequiredGas` for the `VALIDATE_SENDER` precompile so gas cost scales precisely with `numSigs` (number of `Ecrecover` calls actually performed), consistent with `params.TxValidationGasPerKey` used elsewhere for multisig validation gas accounting.

### Proof of Concept
1. Deploy a trivial contract (or use an EOA-initiated call) that invokes the `VALIDATE_SENDER` precompile address with calldata: `from (20 bytes) || msg (32 bytes) || N * 65-byte signature chunks`, where `N` is set arbitrarily large (e.g., thousands), each chunk filled with garbage/invalid-but-well-formed signature bytes.
2. `validateSender` computes `numSigs = len(ptr) / common.SignatureLength = N` with no upper-bound check, then loops `N` times calling `crypto.Ecrecover` on each chunk. [4](#0-3) 
3. Only after all `N` (potentially thousands of) expensive recoveries complete does the code call `accountkey.ValidateAccountKey`, which will reject the call once it observes `numSigs > len(a.Keys)` (bounded at 10) — meaning all the recovery work performed beyond the first ~10 signatures was wasted CPU cycles paid for by a single call.
4. Compare the actual gas charged for this call (`RequiredGas`, not confirmed in this investigation) against the real CPU cost of `N` `Ecrecover` invocations to determine the degree of underpricing/amplification.

### Citations

**File:** blockchain/vm/contracts.go (L896-921)
```go
	// Parse remaining bytes. The length should be divided by common.SignatureLength.
	if len(ptr)%common.SignatureLength != 0 {
		return errWrongSignatureLength
	}

	numSigs := len(ptr) / common.SignatureLength
	if numSigs == 0 {
		return errNoSignatures
	}
	pubs := make([]*ecdsa.PublicKey, numSigs)
	for i := range numSigs {
		p, err := crypto.Ecrecover(msg, ptr[0:common.SignatureLength])
		if err != nil {
			return err
		}
		pubs[i], err = crypto.UnmarshalPubkey(p)
		if err != nil {
			return err
		}
		ptr = ptr[common.SignatureLength:]
	}

	k := picker.GetKey(from)
	if err := accountkey.ValidateAccountKey(currentBlockNumber, from, k, pubs, accountkey.RoleTransaction); err != nil {
		return err
	}
```

**File:** blockchain/types/accountkey/account_key_weighted_multi_sig.go (L86-97)
```go
func (a *AccountKeyWeightedMultiSig) Validate(currentBlockNumber uint64, r RoleType, recoveredKeys []*ecdsa.PublicKey, from common.Address) bool {
	if a.Threshold == 0 {
		return false
	}
	isIstanbul := fork.Rules(new(big.Int).SetUint64(currentBlockNumber)).IsIstanbul

	// Validation 1. if isIstanbul is true, check whether the signature number exceeds key number
	if isIstanbul && len(recoveredKeys) > len(a.Keys) {
		logger.Debug("AccountKeyWeightedMultiSig validation failed and number of signatures exceeds key number",
			"numSigs", len(recoveredKeys), "numKeys", len(a.Keys))
		return false
	}
```
