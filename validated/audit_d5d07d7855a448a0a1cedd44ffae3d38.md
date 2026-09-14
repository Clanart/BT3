## Analysis

The CVE-2017-5196 bug class is: an unhandled decoding error on attacker-supplied bytes that are *assumed* to be valid UTF-8, causing a crash. The closest reachable analog in this codebase is `ProofsChecker.from_program`, which decodes attacker-controlled curried puzzle bytes from an incoming CR-CAT offer spend without exception handling.

### Title
Unhandled `UnicodeDecodeError` on attacker-controlled CR-CAT proof-flag bytes crashes offer/VC processing - ([File: chia/wallet/vc_wallet/cr_cat_drivers.py])

### Summary
`ProofsChecker.from_program` decodes each curried "flag" atom taken directly from a coin's puzzle reveal using `.decode("utf8")` in strict mode, with no error handling. This method is invoked from `VCWallet.add_vc_authorization`, which processes CR-CAT coin spends embedded in an `Offer` object — data supplied by an untrusted offer counterparty — during offer construction/acceptance.

### Finding Description
`ProofsChecker.from_program` builds its `flags` list via:
```
return cls([flag.at("f").as_atom().decode("utf8") for flag in unknown_puzzle.curried_args[0].as_iter()])
``` [1](#0-0) 

This is called from `VCWallet.add_vc_authorization` via `ProofsChecker.from_program(UnknownPuzzle(known_program=crcat_spend.crcat.proofs_checker))`, where `crcat_spend.crcat.proofs_checker` is extracted from a `CRCATSpend` built from a coin spend contained in an `Offer` (`offer.to_valid_spend().coin_spends`) — i.e., puzzle data controlled by the offer counterparty/spend-bundle author. [2](#0-1) [3](#0-2) 

Because the "flags" are raw curried atoms taken from a puzzle reveal, an attacker who crafts an offer/CR-CAT with a proofs-checker curry containing non-UTF-8 bytes (any byte sequence is valid CLVM atom data) causes `.decode("utf8")` to raise an uncaught `UnicodeDecodeError` — directly analogous to Irssi's crash on non-UTF8 strings.

A related unguarded decode exists in `VCProofs.from_program` (`chia/wallet/vc_wallet/vc_store.py:44-55`), which also decodes untrusted atom bytes with `.decode("utf-8")` without error handling, though its more clearly-reachable callers in this codebase appear to be local RPC/DB paths rather than directly from a hostile spend bundle; I was not able to fully confirm during this session whether it is reachable from an unauthenticated offer/spend path without deeper tracing of every VC proof-loading call site.

### Impact Explanation
If a wallet processes an offer (e.g., during take/build of a CR-CAT offer) containing a maliciously crafted proofs-checker puzzle with non-UTF-8 flag bytes, `add_vc_authorization` raises an unhandled exception. This halts the offer-processing/authorization flow for the affected wallet operation. This matches the "spend-triggered transaction-processing halt" impact category permitted by the rules — a wallet user or offer counterparty can supply data that forces a crash/exception in the CR-CAT/VC authorization logic rather than a graceful rejection.

### Likelihood Explanation
Likelihood is high for triggering the crash from the perspective of an offer counterparty: crafting a CR-CAT puzzle reveal with an arbitrary proofs-checker curry (non-UTF-8 byte sequence) requires no special privileges — CLVM atoms are unconstrained byte strings, and offers are exchanged and processed off-chain before/at acceptance time by any wallet that engages with CR-CAT/VC-gated offers.

### Recommendation
Wrap the `.decode("utf8")` call in `ProofsChecker.from_program` (and the analogous calls in `VCProofs.from_program` and `VCProofs.prove_keys`) in exception handling (e.g., `errors="strict"` inside try/except `UnicodeDecodeError`, following the pattern already used in `chia/_tests/util/run_block.py`'s CAT memo decoding), and reject/skip malformed proofs-checker puzzles gracefully rather than allowing the exception to propagate and abort the whole offer-authorization routine.

### Proof of Concept
1. Construct a CR-CAT coin whose puzzle is curried with a `PROOF_FLAGS_CHECKER` argument list containing an atom with invalid UTF-8 bytes (e.g., `b"\xff\xfe"`), via `ProofsChecker` construction bypass or direct `Program.to` curry construction, matching the structure expected by `CRCAT.is_cr_cat`.
2. Embed this coin's spend into an `Offer` bundle as an incomplete CR-CAT spend (`crcat_spend.incomplete == True`).
3. Have a victim wallet call `VCWallet.add_vc_authorization(offer, solver, action_scope)` (invoked as part of normal offer take/build flow involving CR-CATs) on the malicious offer.
4. Observe `ProofsChecker.from_program` raise `UnicodeDecodeError` when decoding the malformed flag atom, propagating out of `add_vc_authorization` and aborting the wallet's offer processing.

### Citations

**File:** chia/wallet/vc_wallet/cr_cat_drivers.py (L654-659)
```python
    @classmethod
    def from_program(cls, unknown_puzzle: UnknownPuzzle) -> ProofsChecker:
        if unknown_puzzle.mod != PROOF_FLAGS_CHECKER or unknown_puzzle.curried_args is None:
            raise ValueError("Puzzle was not a proof checker")  # pragma: no cover

        return cls([flag.at("f").as_atom().decode("utf8") for flag in unknown_puzzle.curried_args[0].as_iter()])
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L454-466)
```python
        # Gather all of the CRCATs being spent and the CRCATs that each creates
        crcat_spends: list[CRCATSpend] = []
        other_spends: list[CoinSpend] = []
        spends_to_fix: dict[bytes32, CoinSpend] = {}
        for spend in offer.to_valid_spend().coin_spends:
            if CRCAT.is_cr_cat(UnknownPuzzle(known_program=spend.puzzle_reveal))[0]:
                crcat_spend: CRCATSpend = CRCATSpend.from_coin_spend(spend)
                if crcat_spend.incomplete:
                    crcat_spends.append(crcat_spend)
                    if spend in offer._bundle.coin_spends:
                        spends_to_fix[spend.coin.name()] = spend
                elif spend in offer._bundle.coin_spends:  # pragma: no cover
                    other_spends.append(spend)
```

**File:** chia/wallet/vc_wallet/vc_wallet.py (L536-539)
```python
                        # It's on my TODO list to fix the below line -Quex
                        vc.proof_hash,  # type: ignore
                        ProofsChecker.from_program(UnknownPuzzle(known_program=crcat_spend.crcat.proofs_checker)).flags,
                    ),
```
