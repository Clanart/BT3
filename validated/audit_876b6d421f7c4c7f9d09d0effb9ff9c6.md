### Title
`resolve`/`find` mis-handle mask `u0`: `population 0` short-circuits via `asserts!`, and `subset 0 mask` is always true, causing resolution to an arbitrary egroup instead of an empty/no-debt result - (File: `mainnet/contracts/registry/v0-egroup.clar`)

### Summary
`population` computes population count via `fold iter-population`, and when the input mask is `u0`, the `asserts!` guard in `iter-population` fires on the very first fold iteration and returns the unchanged accumulator forever, so `population u0` evaluates to `u0` instead of erroring or signaling emptiness. `find`/`resolve` then use this `min = u0` to call `active u0`, which (because `abs = pos + 1 >= 0` is always true) returns every populated bucket, and `find-superset` accepts the first bucket entry because `subset u0 mask` is trivially `true` for any `mask`. The net effect is that `resolve u0` silently returns whatever egroup happens to sit first in the lowest-population bucket, rather than treating mask `0` as a distinguished "no collateral/no debt" case or erroring out.

### Finding Description
Tracing the code in `mainnet/contracts/registry/v0-egroup.clar`:

- `population` (line 81-84) folds `iter-population` (line 86-91) over `ITER-UINT-128` starting from `{c: u0, v: v}`. When `v = u0`, `(asserts! (not (is-eq v u0)) acc)` (line 88) fails immediately, and Clarity's `asserts!` returns the accumulator unchanged as the function's value for that iteration - `c` is never incremented. Because `v` also stays `u0` in that returned accumulator, every subsequent fold iteration hits the same short-circuit, so `population u0` deterministically returns `u0`. [1](#0-0) 
- `find` (line 365-372) sets `min = (population target)`. With `target = u0`, `min = u0`. It then calls `(active min)`. [2](#0-1) 
- `active`/`iter-active` (line 238-260) computes `bounds = (>= abs min)` where `abs = pos + 1`. Since `abs` is always `>= 1 > u0 = min`, `bounds` is always `true`, so every populated bucket (any bucket with its bit set in `popbucket`) is included in the active list, in ascending population-count order. [3](#0-2) 
- `find` folds `iter-find` over that active list; `iter-find` looks up each bucket and calls `find-superset target bucket` (line 226-236, 262-279). `subset` (line 93-95) computes `overlap = bit-and sub super` and checks `is-eq sub overlap`; with `sub = u0`, `overlap` is always `u0`, so `subset u0 mask` is `true` for every `mask`. Thus `find-superset u0 bucket` returns the id of the **first mask entry in the first (lowest-population) non-empty bucket**, an arbitrary/unrelated egroup, not a sentinel for "no collateral, no debt". [4](#0-3) 
- `resolve` (line 360-363) simply wraps `find`, so `resolve u0` returns `(ok (lookup arbitrary-id))` instead of `ERR-NO-EGROUP-FOUND` or any explicit "riskless/no-debt" handling. [5](#0-4) 

This confirms the described mechanism precisely: `population u0 = u0`, `active u0` returns all buckets, and `find-superset u0 _` matches the first entry of the lowest-population bucket unconditionally. Whatever LTV/liquidation parameters belong to that arbitrary first egroup would then be applied to a position whose true state (mask `0`) does not correspond to that egroup's asset/debt composition at all.

What I could **not** fully verify within the available budget is the end-to-end reachability from the market contract: whether `mask-update`/`collateral-remove`/`debt-add-scaled` in `mainnet/contracts/market/v0-4-market.clar` can actually produce and persist a `mask = u0` in the position registry that subsequently gets passed into `v0-egroup`'s `resolve`/`get-egroup`, or whether that call site special-cases a zero/empty mask before ever invoking `resolve`. I confirmed the resolution-path bug in `v0-egroup.clar` itself, but did not trace the full market-side call chain that establishes the precondition (`mask = u0` reaching `resolve`/`find`).

### Impact Explanation
If mask `u0` is genuinely reachable as an input to `resolve`/`find` from a position that has been fully drained of both collateral and debt, the resolved egroup is arbitrary and unrelated to the actual (empty) position state. Depending on which egroup happens to occupy the first slot of the lowest-population bucket, this could apply an incorrect LTV-borrow/liquidation bound to a subsequent same-transaction mutation of that position (e.g., a following `debt-add-scaled` in a composed call), rather than the correct behavior of treating an all-zero mask as trivially healthy/riskless. This would fall under a distorted health-verdict / alignment issue in the resolution logic that the audit scope explicitly includes ("verdict soundness"). Whether it reaches Critical severity depends entirely on the unconfirmed reachability question above - if the market layer never calls `resolve`/`find` with a live mask of exactly `u0` (e.g., it short-circuits health checks when both collateral and debt totals are zero, without consulting the egroup registry), this bug in `v0-egroup.clar` is dead code from an exploitability standpoint despite being a real logic flaw.

### Likelihood Explanation
The `population`/`subset` mishandling of `u0` is deterministic and requires no privileged access - any registered mask value of `u0` will trigger it. The open question is solely whether the market/collateral-registry code path ever calls `v0-egroup`'s `resolve`/`find`/`get-egroup` with a raw mask of `u0` mid-transaction (as opposed to filtering it out beforehand). This could not be confirmed from the files inspected in this session.

### Recommendation
In `v0-egroup.clar`, explicitly special-case mask `u0` in `resolve`/`find` (and in `population`) rather than relying on `asserts!` short-circuit semantics: return an explicit "empty position, always healthy" result (or `ERR-NO-EGROUP-FOUND`) when `target = u0`, instead of letting `population u0` evaluate to `u0` and `subset u0 mask` match the first bucket entry unconditionally.

### Proof of Concept
A Clarinet/vitest simnet test targeting `v0-egroup.clar` directly (without needing to resolve the market-layer reachability question) can demonstrate the internal bug:
1. Insert at least two egroups via `insert` with distinct nonzero `MASK` values, e.g., egroup A with `MASK = 0b0001` and egroup B with `MASK = 0b0010`, both population 1 (same bucket).
2. Call `(contract-call? .v0-egroup resolve u0)` and assert it returns `(ok <egroup A>)` (the first entry inserted into the population-1 bucket) even though mask `0` matches neither A's nor B's actual composition.
3. Separately call `(contract-call? .v0-egroup population u0)` and assert it returns `u0` (not an error, not a distinguished sentinel), confirming the `asserts!` short-circuit path in `iter-population`.
4. To fully substantiate the Critical claim, a follow-up test in `local-testing/tests/**` against `market.clar`/`market-vault.clar` would need to show that a real user's `collateral-remove` (full withdrawal) can leave a stored position mask of exactly `u0`, and that a subsequent health/LTV check on that position calls `v0-egroup`'s `resolve`/`get-egroup` with that raw mask rather than special-casing zero collateral/debt beforehand - this part of the PoC could not be completed within this session's scope.

### Citations

**File:** mainnet/contracts/registry/v0-egroup.clar (L81-91)
```text
(define-private (population (v uint))
  (let ((init { c: u0, v: v })
        (out (fold iter-population ITER-UINT-128 init)))
    (get c out)))

(define-private (iter-population (iter uint) (acc {c: uint, v: uint}))
  (let ((v (get v acc))
        (empty? (asserts! (not (is-eq v u0)) acc))
        (c (+ u1 (get c acc)))
        (trail (bit-and v (- v u1)))) ;; flip all trailing 0s && rightmost to 1
    { c: c, v: trail }))
```

**File:** mainnet/contracts/registry/v0-egroup.clar (L245-260)
```text
(define-private (iter-active (pos uint) (acc {bitmap: uint, min: uint, result: (list 128 uint)}))
  ;; abs is the 1-based rep of pos in the bitmap (represents population count)
  ;; pos is the bucket index (0-based)
  (let ((abs (+ pos u1))
        (bmap (get bitmap acc))
        (min (get min acc))
        (actv (> (bit-and bmap (pow u2 pos)) u0))
        (bounds (>= abs min)))
    (if (and actv bounds)
        ;; Bucket exists AND meets population requirement - include it
        ;; Return bucket INDEX (pos), not population count (abs)
        (let ((result (get result acc))
              (new (as-max-len? (append result pos) u128)))
          (merge acc { result: (unwrap-panic new) }))
        ;; Skip this bucket
        acc)))
```

**File:** mainnet/contracts/registry/v0-egroup.clar (L262-279)
```text
(define-private (find-superset (target uint) (masks (list 128 uint)))
  (let ((init { target: target, result: none })
        (out (fold iter-find-superset masks init)))
    (get result out)))

(define-private (iter-find-superset (mask uint) (acc {target: uint, result: (optional uint)}))
  (let ((res (get result acc)))
    (if (is-some res)
        ;; Already found a match - return early
        acc
        ;; Check if this mask matches
        (let ((target (get target acc)))
          (if (subset target mask)
              ;; Match found - return egroup ID
              (let ((id (unwrap-panic (map-get? reverse mask))))
                (merge acc { result: (some (buff-to-uint-be id)) }))
              ;; Not a match - skip
              acc)))))
```

**File:** mainnet/contracts/registry/v0-egroup.clar (L360-363)
```text
(define-read-only (resolve (mask uint))
  (match (find mask)
    id (ok (lookup id))
    ERR-NO-EGROUP-FOUND))
```

**File:** mainnet/contracts/registry/v0-egroup.clar (L365-372)
```text
(define-read-only (find (target uint))
  (let ((min (population target))
        (actv (active min))
        (init {target: target, result: none})
        (out (fold iter-find
                      actv
                      init)))
  (get result out)))
```
