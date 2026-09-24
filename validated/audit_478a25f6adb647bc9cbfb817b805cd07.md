### Title
Panic-inducing `.try_into().unwrap()` on closed-enum field accessors in Rust protobuf bindings - (File: `src/google/protobuf/compiler/rust/accessors/singular_scalar.cc`)

### Summary
The Rust protobuf runtime models proto2 "closed" enums via a `TryFrom<i32>` implementation that returns `Err(UnknownEnumValue)` for any raw wire value outside the declared enum range [1](#0-0) , and the `Enum` trait explicitly documents this closed/open distinction and the `UnknownEnumValue` error type [2](#0-1) . Despite this fallible conversion existing, the generated getter for a plain (non-extension, non-map) singular enum field on the upb backend discards the `Result` and calls `.unwrap()` directly on the conversion, with a code comment acknowledging this is a trust assumption rather than a proven invariant:

```rust
self.inner.ptr().get_$type_name$_at_index(
  $upb_mt_field_index$, ($default_value$).into()
).try_into().unwrap()
``` [3](#0-2) 

This is structurally the same bug class as the Steamworks advisory: a raw integer/enum value originating from attacker-influenced input is converted with an infallible-looking operation (`.unwrap()`), and if the underlying validation that the code *assumes* happened elsewhere didn't actually happen for this code path, the process panics — a denial of service.

### Finding Description
`k_EAuthSessionResponseAuthTicketNetworkIdentityFailure` in the Steamworks report is a value the raw C API can legitimately deliver, but which the trusted wrapper's callback-processing code didn't handle, causing a panic when converting/matching. The invariant that failed was "every possible raw discriminant value has a corresponding handled Rust variant."

In this Protobuf Rust codebase, the analogous invariant is: "by the time a closed-enum field's stored `int32` value reaches the field accessor, it is guaranteed to be one of the enum's known values." The generated accessor code in `singular_scalar.cc` bakes in exactly this assumption via an unconditional `.unwrap()` on the `TryFrom<i32>` conversion, and the surrounding comment makes the assumption explicit: "even for closed enums we trust upb to only return one of the named values" [4](#0-3) .

Notably, other accessor code paths in the same codebase do **not** make this same trust assumption and instead defensively fall back to a default value instead of panicking:
- Extension getters (both upb and cpp kernels) use `E::try_from(val).unwrap_or(default)` rather than `.unwrap()` [5](#0-4) [6](#0-5) .
- Even the low-level `from_message_value` conversion used by the upb type-conversion machinery only asserts the invariant in debug builds (`std::debug_assert!`) before doing an `unwrap_unchecked()` in release builds [7](#0-6) , meaning in release builds an invalid value is silently treated as valid (UB) rather than checked at all — while the plain field accessor instead performs a *checked* conversion but panics loudly.

This inconsistency across extension/oneof/plain-field paths is the crux of the analog: the codebase itself demonstrates awareness that "closed enum values are always known" is not a universally-safe invariant to rely on unconditionally (hence the defensive `unwrap_or` used elsewhere and the open TODO `b/361751487` in the affected code marking this as technical debt), yet the plain singular-field getter still performs an unchecked `.unwrap()`.

### Impact Explanation
If any code path allows a closed-enum field's backing `int32` storage to hold a value outside the declared enum set at the time this accessor is called — for example, via unknown-field promotion/re-linking after a message has already been parsed and partially typed (the codebase's `upb/message/promote.c` exists specifically to promote previously-unknown fields into typed storage once a fuller descriptor/extension registry becomes available), or via any other write path that doesn't route through the parser's own `ValidateEnum`/closed-enum check — calling the generated getter will panic via `Result::unwrap()`. In a game/server or any application embedding this generated code, a single crafted or malformed message reaching that accessor causes an unrecoverable panic, i.e., denial of service, directly mirroring the impact of the Steamworks advisory.

### Likelihood Explanation
Under upb's normal single-pass wire decode, closed-enum values are validated at parse time and out-of-range values are diverted to unknown fields, so this specific getter is not reachable from a simple `ParseFrom` call on an ordinary bounded message under that path alone — this is the reason the code's authors state they "trust" the invariant. However, this trust is explicitly called out as unenforced/unverified in the source itself (see the TODO and the differing, more defensive handling used for extensions), and the existence of the separate unknown-field-promotion machinery (`upb/message/promote.c`) means the "value is always validated before it reaches typed storage" assumption is not obviously true for every code path that can populate this storage. I was not able to fully trace `promote.c`'s enum-validation behavior in the time available, so I cannot confirm with certainty that promotion bypasses `ValidateEnum`; this is the key remaining uncertainty in fully proving end-to-end reachability from an ordinary public parse call.

### Recommendation
- Replace the unconditional `.try_into().unwrap()` in the generated getter for closed enums with the same defensive pattern already used for extensions (`unwrap_or(default)`), or return the raw fallible result via a `try_`-prefixed accessor, so a mismatch produces a documented fallback instead of a panic.
- Audit `upb/message/promote.c` and any other writer of typed closed-enum storage to confirm they invoke the same `ValidateEnum`/closed-enum check that the primary wire decoder (`upb/wire/decode.c`) applies, and add a debug/release-safe assertion or explicit validation at the boundary rather than relying on an implicit cross-module invariant.
- Resolve the outstanding TODO (`b/361751487`) referenced directly in the vulnerable code, since it already flags this exact concern.

### Proof of Concept
I could not produce a runnable, minimal reproduction confirming end-to-end reachability from an ordinary bounded `ParseFrom` call, because doing so requires exercising the unknown-field-promotion path in `upb/message/promote.c` together with a closed-enum field and inspecting whether `ValidateEnum` is invoked there — verification I was unable to complete with the available tools/iterations. The concrete unsafe code construct itself is confirmed and cited above (`singular_scalar.cc:91-108`), including the author's own comment acknowledging the trust assumption; what remains unproven is a fully wire-triggerable path that violates the "upb always returns a known value" invariant for this specific generated accessor. This should be validated with actual repository/build access (e.g., a Devin session) by writing a UPB-based Rust test that: (1) defines a closed proto2 enum message, (2) serializes an out-of-range enum value as an *unknown* field, (3) parses it, (4) triggers promotion into the typed field (e.g. via a second parse/linking step or reflection API), and (5) calls the generated getter to see if it panics.

### Citations

**File:** src/google/protobuf/compiler/rust/enum.cc (L207-222)
```text
          {"impl_from_i32",
           [&] {
             if (desc.is_closed()) {
               ctx.Emit(R"rs(
              impl $std$::convert::TryFrom<i32> for $name$ {
                type Error = $pb$::UnknownEnumValue<Self>;

                fn try_from(val: i32) -> $Result$<$name$, Self::Error> {
                  if <Self as $pbi$::Enum>::is_known(val) {
                    Ok(Self(val))
                  } else {
                    Err($pb$::UnknownEnumValue::new($pbi$::Private, val))
                  }
                }
              }
            )rs");
```

**File:** rust/enum.rs (L26-54)
```rust
pub unsafe trait Enum:
    TryFrom<i32>
    + Into<i32>
    + Copy
    + for<'a> Proxied<View<'a> = Self>
    + EntityType<Tag = entity_tag::EnumTag>
    + Singular
    + SealedInternal
{
    /// The name of the enum.
    const NAME: &'static str;

    /// Returns `true` if the given numeric value matches one of the `Self`'s
    /// defined values.
    ///
    /// If `Self` is a closed enum, then `TryFrom<i32>` for `value` succeeds if
    /// and only if this function returns `true`.
    fn is_known(value: i32) -> bool;
}

/// An integer value wasn't known for an enum while converting.
pub struct UnknownEnumValue<T>(i32, PhantomData<T>);

impl<T> UnknownEnumValue<T> {
    #[doc(hidden)]
    pub fn new(_private: Private, unknown_value: i32) -> Self {
        Self(unknown_value, PhantomData)
    }
}
```

**File:** src/google/protobuf/compiler/rust/accessors/singular_scalar.cc (L91-108)
```text
             } else {
               ctx.Emit({Sub("getter_fn_name", RsSafeName(field_name))
                             .AnnotatedAs(&field)},
                        R"rs(
                    pub fn $getter_fn_name$($view_self$) -> $Scalar$ {
                      unsafe {
                        // TODO: b/361751487: This .into() and .try_into() is only
                        // here for the enum<->i32 case, we should avoid it for
                        // other primitives where the types naturally match
                        // perfectly (and do an unchecked conversion for
                        // i32->enum types, since even for closed enums we trust
                        // upb to only return one of the named values).
                        self.inner.ptr().get_$type_name$_at_index(
                          $upb_mt_field_index$, ($default_value$).into()
                        ).try_into().unwrap()
                      }
                    }
                  )rs");
```

**File:** rust/upb_kernel/extension.rs (L390-398)
```rust
        let val = unsafe {
            upb_Message_GetExtensionInt32(
                msg.get_ptr(Private).raw(),
                self.inner.mini_table().as_ptr(),
                i32::from(default),
            )
        };
        E::try_from(val).unwrap_or(default)
    }
```

**File:** rust/cpp_kernel/extension.rs (L462-470)
```rust
        let val = unsafe {
            proto2_rust_Message_get_extension_int32(
                msg.get_raw_message(Private),
                self.number() as i32,
                i32::from(default),
            )
        };
        E::try_from(val).unwrap_or(default)
    }
```

**File:** rust/upb_kernel/conversions.rs (L121-128)
```rust
    unsafe fn from_message_value<'msg>(val: upb_MessageValue) -> View<'msg, Self> {
        // SAFETY: The caller guarantees that `val` is the correct variant.
        let result = Self::try_from(unsafe { val.int32_val });
        std::debug_assert!(result.is_ok());
        // SAFETY:
        // - The caller guarantees that `val.int32_val` is valid for this enum.
        unsafe { result.unwrap_unchecked() }
    }
```
