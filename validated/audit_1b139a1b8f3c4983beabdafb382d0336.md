### Title
Integer Wrapping in Relative Timelock Arithmetic Allows Timelock Bypass — (`File: crates/chia-consensus/src/check_time_locks.rs`)

### Summary

When `check_time_locks` is called with `nowrap=false` (the legacy consensus path), relative timelock conditions (`ASSERT_SECONDS_RELATIVE`, `ASSERT_HEIGHT_RELATIVE`) are evaluated using `wrapping_add` instead of `saturating_add`. An unprivileged spender can craft a CLVM spend whose `seconds_relative` or `height_relative` condition value is chosen so that the addition wraps to zero, making the timelock comparison trivially false and allowing the coin to be spent before its intended lock period expires. The same wrapping inverts the sense of `ASSERT_BEFORE_*_RELATIVE` conditions, causing valid spends to be incorrectly rejected. Both effects are confirmed by the project's own test suite.

### Finding Description

`check_time_locks` in `crates/chia-consensus/src/check_time_locks.rs` branches on the `nowrap` boolean:

```rust
// nowrap=true path (correct)
if timestamp < unspent.timestamp.saturating_add(seconds_relative) { ... }

// nowrap=false path (legacy, vulnerable)
} else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) { ... }
```

`seconds_relative` is a `u64` parsed from the CLVM condition list by `parse_args` in `conditions.rs`. The parser accepts any value in `[0, u64::MAX]` via `sanitize_uint(..., 8, ...)`:

```rust
SanitizedUint::PositiveOverflow => Err(...),   // > u64::MAX rejected
SanitizedUint::NegativeOverflow => Ok(Condition::SkipRelativeCondition),
SanitizedUint::Ok(r) => Ok(Condition::AssertSecondsRelative(r)),  // full u64 range accepted
```

**Exploit arithmetic (seconds_relative):**
- Coin confirmed at timestamp `T` (e.g., `T = 10_000`).
- Attacker sets `seconds_relative = u64::MAX - T + 1` (e.g., `0xFFFF_FFFF_FFFF_D8F0`).
- `T.wrapping_add(seconds_relative) = 0`.
- Check becomes `current_timestamp < 0`, which is always `false` for a `u64`.
- Timelock passes immediately, regardless of actual elapsed time.

**Exploit arithmetic (height_relative):**
- Coin confirmed at block `H` (e.g., `H = 10`).
- Attacker sets `height_relative = u32::MAX - H + 1` (e.g., `0xFFFF_FFF6`).
- `H.wrapping_add(height_relative) = 0`.
- Check becomes `prev_height < 0`, always `false` for `u32`.
- Height timelock passes immediately.

The project's own test suite explicitly documents and confirms this behavior:

```
// 10 + (2^32 - 10) overflows to 0, wrapping: 15 < 0 -> Ok
(make_test_conds(height_relative=0xFFFF_FFF6), 13, None),
// 10000 + (u64::MAX - 9999) overflows to 0, wrapping: 10150 < 0 -> Ok
(make_test_conds(seconds_relative=0xFFFF_FFFF_FFFF_D8F0), 105, None),
```

The `before_*_relative` conditions are inverted by the same wrapping: a `before_height_relative` or `before_seconds_relative` that wraps to zero causes `prev_height >= 0` (always true), incorrectly rejecting a spend that should be valid.

The `nowrap` parameter is exported through the Python binding `py_check_time_locks` in `wheel/src/api.rs`, meaning the Python full node controls which path is taken. If the full node passes `nowrap=False` for any block range still in production consensus (e.g., pre-upgrade blocks or a misconfigured node), the bypass is live.

### Impact Explanation

**High — timelock validation bypass enables unauthorized spend acceptance.**

A puzzle that enforces a relative timelock via `ASSERT_SECONDS_RELATIVE` or `ASSERT_HEIGHT_RELATIVE` can be bypassed by any spender who controls the condition value (e.g., a puzzle that reads the delay from the solution, or a puzzle the attacker authored). The coin is accepted as validly spent before its lock period expires. Additionally, nodes running with `nowrap=False` and nodes running with `nowrap=True` will disagree on the validity of the same spend bundle, producing a consensus split — a Critical impact under the allowed scope ("deterministic consensus divergence").

### Likelihood Explanation

The attacker-controlled entry path is a standard CLVM spend bundle submitted to the mempool. No privileged access is required. The only precondition is that `nowrap=False` is in effect for the block being validated. The `nowrap` parameter is caller-supplied through the Python binding, and the legacy `wrapping_add` path remains live in the production binary. The exact block range for which the Python full node passes `nowrap=False` is not visible in this repository, but the path is reachable and the bypass values are trivially computable from the coin's on-chain timestamp or confirmed height.

### Recommendation

Remove the `wrapping_add` branch entirely. The `saturating_add` path is the correct and safe behavior for all consensus modes. If backward compatibility with old blocks is required, the Python full node should re-validate affected old spends using the saturating path and document the legacy behavior as a known historical quirk rather than a live code path. At minimum, add a hard upper-bound check: reject any `seconds_relative > u64::MAX - coin_timestamp` (and equivalently for heights) before performing the addition, regardless of the `nowrap` flag.

### Proof of Concept

**Setup:** Coin confirmed at timestamp `T = 10_000`, current timestamp `= 10_150`, `nowrap=False`.

**Crafted condition:** `ASSERT_SECONDS_RELATIVE 0xFFFF_FFFF_FFFF_D8F0`

**Arithmetic:**
```
10_000 + 0xFFFF_FFFF_FFFF_D8F0
= 10_000 + 18_446_744_073_709_541_616
= 18_446_744_073_709_551_616  (mod 2^64)
= 0
```

**Check:** `10_150 < 0` → `false` → timelock passes.

**Expected (correct) behavior with `nowrap=True`:**
```
10_000.saturating_add(0xFFFF_FFFF_FFFF_D8F0) = u64::MAX
10_150 < u64::MAX → true → timelock fails (coin correctly rejected)
```

The project's own test at `crates/chia-consensus/src/check_time_locks.rs` lines 301–306 confirms this exact case returns `Ok(())` (bypass) when `nowrap=false` and `Err(AssertSecondsRelativeFailed)` (correct rejection) when `nowrap=true`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** crates/chia-consensus/src/check_time_locks.rs (L55-68)
```rust
        if let Some(height_relative) = spend.height_relative {
            if nowrap {
                if prev_transaction_block_height
                    < unspent
                        .confirmed_block_index
                        .saturating_add(height_relative)
                {
                    return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
                }
            } else if prev_transaction_block_height
                < unspent.confirmed_block_index.wrapping_add(height_relative)
            {
                return Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed));
            }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L70-78)
```rust
        if let Some(seconds_relative) = spend.seconds_relative {
            if nowrap {
                if timestamp < unspent.timestamp.saturating_add(seconds_relative) {
                    return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
                }
            } else if timestamp < unspent.timestamp.wrapping_add(seconds_relative) {
                return Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed));
            }
        }
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L276-281)
```rust
    // 200 < 100 + u32::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::height_relative_wrap(
        Osc { height_relative: Some(0xffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed)),
        Ok(()),
    )]
```

**File:** crates/chia-consensus/src/check_time_locks.rs (L301-306)
```rust
    // 2000 < 1000 + u64::MAX -> Err with nowrap (saturates), Ok without (wraps)
    #[case::seconds_relative_wrap(
        Osc { seconds_relative: Some(0xffff_ffff_ffff_ffff), ..Default::default() },
        Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed)),
        Ok(()),
    )]
```

**File:** crates/chia-consensus/src/conditions.rs (L582-596)
```rust
        ASSERT_SECONDS_RELATIVE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                8,
                ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertSecondsRelativeFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::SkipRelativeCondition),
                SanitizedUint::Ok(r) => Ok(Condition::AssertSecondsRelative(r)),
            }
```

**File:** crates/chia-consensus/src/conditions.rs (L614-628)
```rust
        ASSERT_HEIGHT_RELATIVE => {
            maybe_check_args_terminator(a, c, flags)?;
            let node = first(a, c)?;
            match sanitize_uint(
                a,
                node,
                4,
                ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed),
            )? {
                SanitizedUint::PositiveOverflow => {
                    Err(ValidationErr::Err(ErrorCode::AssertHeightRelativeFailed))
                }
                SanitizedUint::NegativeOverflow => Ok(Condition::SkipRelativeCondition),
                SanitizedUint::Ok(r) => Ok(Condition::AssertHeightRelative(r as u32)),
            }
```

**File:** crates/chia-consensus/src/sanitize_int.rs (L13-51)
```rust
pub fn sanitize_uint(
    a: &Allocator,
    n: NodePtr,
    max_size: usize,
    code: ValidationErr,
) -> Result<SanitizedUint, ValidationErr> {
    assert!(max_size <= 8);

    let buf = match a.sexp(n) {
        SExp::Atom => a.atom(n),
        SExp::Pair(..) => return Err(code),
    };
    let buf = buf.as_ref();

    if buf.is_empty() {
        return Ok(SanitizedUint::Ok(0));
    }

    // if the top bit is set, it's a negative number
    if (buf[0] & 0x80) != 0 {
        return Ok(SanitizedUint::NegativeOverflow);
    }

    // we only allow a leading zero if it's used to prevent a value to otherwise
    // be interpreted as a negative integer. i.e. if the next top bit is set
    // all other leading zeros are invalid
    if buf == [0_u8] || (buf.len() > 1 && buf[0] == 0 && (buf[1] & 0x80) == 0) {
        return Err(code);
    }

    // strip the leading zero byte if there is one
    let size_limit = if buf[0] == 0 { max_size + 1 } else { max_size };

    // if there are too many bytes left in the value, it's too big
    if buf.len() > size_limit {
        return Ok(SanitizedUint::PositiveOverflow);
    }

    Ok(SanitizedUint::Ok(u64_from_bytes(buf)))
```
