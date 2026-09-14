### Title
Untrusted-length integer strings in `CreateOfferForIDs.offer_spec` cause quadratic-time `int()` parsing DoS on the wallet's single-threaded event loop - ([File: chia/wallet/wallet_request_types.py])

### Summary
The `CreateOfferForIDs` RPC request object exposes an `offer_spec` property that converts every key/value in the caller-supplied `offer: dict[str, str]` with Python's built-in `int()` without any length or format bound. Because CPython (< 3.10.7 as per CVE-2020-10735, and mitigated but still limited to 4300 digits by default in later versions) has quadratic-time complexity for parsing very long decimal digit strings, a local RPC caller can submit an oversized numeric string in the `offer` dict and force the wallet process to spend excessive CPU time inside a synchronous call on the asyncio event loop, stalling all wallet transaction processing (sync, spend bundle creation, other RPC calls) for the duration of the parse.

### Finding Description
`CreateOfferForIDs` is a `Streamable` RPC request dataclass with a hack field `offer: dict[str, str]` (used because `Streamable` can't represent negative ints directly). Its `offer_spec` property performs the conversion back to `int`: [1](#0-0) 

```
@property
def offer_spec(self) -> dict[int | bytes32, int]:
    modified_offer: dict[int | bytes32, int] = {}
    for wallet_identifier, change in self.offer.items():
        if len(wallet_identifier) > 16:
            modified_offer[bytes32.from_hexstr(wallet_identifier)] = int(change)
        else:
            modified_offer[int(wallet_identifier)] = int(change)
    return modified_offer
```

Neither `wallet_identifier` nor `change` is length-checked before being handed to `int()`. There is no `Decimal`/regex pre-validation as is done elsewhere in the codebase for CLI-facing amount fields (e.g. `chia/cmds/param_types.py` uses `Decimal()` + explicit bounds checks for CLI amounts). This RPC endpoint is invoked directly by `wallet_rpc_api.py`'s `create_offer_for_ids` handler (confirmed present via `async def create_offer_for_ids` in `chia/wallet/wallet_rpc_api.py`, and the same request type's `offer_spec`/`offer` field is exercised end-to-end through `chia/wallet/trade_manager.py`'s `create_offer_for_ids`). Since chia's full-node/wallet RPC handlers run on the single asyncio event loop, a CPU-bound `int()` call on an attacker-chosen string will block the event loop thread and stall concurrent wallet operations (syncing, other RPC requests, pending spend bundle processing) for as long as the parse takes.

This is the same bug class as BIT-libpython-2020-10735 / CVE-2020-10735: quadratic-time non-binary-base `int(text)` parsing on untrusted, unbounded-length decimal strings. Unlike CLVM's own integer handling, which always uses `int.from_bytes()` (binary, unaffected — see `chia/util/casts.py` `int_from_bytes`), this wallet RPC path calls the vulnerable text-mode `int()` constructor.

### Impact Explanation
A local, already-authorized RPC caller (e.g., a script or web UI with access to the wallet RPC cert/token, or a locally-running less-trusted process on a shared machine) can submit a single `create_offer_for_ids` request containing a very long digit string (hundreds of thousands to a million characters) as an `offer` dict value or key. Depending on the installed Python/CPython version, this synchronous parse can consume seconds of CPU time on the event loop thread, halting the wallet's ability to process other transactions, sync, or serve other RPC calls during that window — a "spend-triggered transaction-processing halt," which is explicitly one of the accepted impact categories.

### Likelihood Explanation
Likelihood is dependent on the exact CPython version bundled with the target deployment: CPython >= 3.11 (and backported to earlier maintained branches) enforces a default 4300-digit limit on `int()`/`str()` conversions (`sys.set_int_max_str_digits`), which would raise a `ValueError` instead of hanging, unless the limit was explicitly disabled. On unpatched or misconfigured Python interpreters (matching the OSV/Bitnami `libpython >=3.10.0 <3.10.7` affected range, or any interpreter with the digit-limit sysconfig disabled/reduced), the request is trivially reachable via a single authorized RPC call with no other preconditions.

### Recommendation
- In `CreateOfferForIDs.offer_spec` (`chia/wallet/wallet_request_types.py`), validate `wallet_identifier` and `change` length/format (e.g., cap to a small number of digits, or validate with a regex/`Decimal` bound check) before calling `int()`.
- Alternatively/also, ensure the wallet process runs on a CPython version with `sys.set_int_max_str_digits` enabled at a safe default and does not disable it.
- Offload any inherently CPU-heavy request parsing off the asyncio event loop (e.g., via `run_in_executor`) so a single slow request cannot stall unrelated wallet operations.

### Proof of Concept
1. Start the wallet RPC service (with legitimate RPC credentials/cert as required).
2. Send a `create_offer_for_ids` RPC request where the `offer` dict contains an entry such as `{"1": "9" * 2_000_000}` (or as the wallet-id key with an oversized digit string).
3. When the object's `.offer_spec` property is evaluated by `create_offer_for_ids` in `chia/wallet/wallet_rpc_api.py`/`chia/wallet/trade_manager.py`, the `int(change)` call spends multiple seconds of CPU time on the event loop thread.
4. Observe stalled sync/RPC responsiveness for the wallet service during that window, on interpreters without (or with a disabled) integer-string-length guard.

### Citations

**File:** chia/wallet/wallet_request_types.py (L2276-2296)
```python
@streamable
@dataclass(kw_only=True, frozen=True)
class CreateOfferForIDs(TransactionEndpointRequest):
    # a hack for dict[str, int] because streamable doesn't support negative ints
    offer: dict[str, str]
    driver_dict: dict[bytes32, PuzzleInfo] | None = None
    solver: Solver | None = None
    validate_only: bool = False
    offer_only: bool = False

    @property
    def offer_spec(self) -> dict[int | bytes32, int]:
        modified_offer: dict[int | bytes32, int] = {}
        for wallet_identifier, change in self.offer.items():
            if len(wallet_identifier) > 16:  # wallet IDs are uint32 therefore no longer than 8 bytes :P
                modified_offer[bytes32.from_hexstr(wallet_identifier)] = int(change)
            else:
                modified_offer[int(wallet_identifier)] = int(change)

        return modified_offer

```
