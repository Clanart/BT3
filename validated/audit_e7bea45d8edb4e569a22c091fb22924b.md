## Title
JsonFormat quoted-integer parsing falls back to `BigDecimal(String)`, an O(N²) numeric-string constructor, for attacker-controlled JSON number literals - ([File: java/util/src/main/java/com/google/protobuf/util/JsonFormat.java])

## Summary
The OpenSSL report's failed invariant is: text↔number conversion routines that use naive/quadratic algorithms on attacker-controlled digit strings of unbounded length, with no length cap before the expensive conversion runs. The Protobuf analog is `JsonFormat.Parser`'s handling of `int64`/`uint32`/`uint64` fields supplied as JSON strings (or numeric literals that don't fit into a `long`): when `Long.parseLong`/`Long.parseUnsignedLong` fails, the code falls back to `parseBigDecimal(json.getAsString())`, which constructs a `java.math.BigDecimal` directly from the attacker-supplied string.

## Finding Description
`BigDecimal(String)` on JDK versions prior to JDK 18 has documented O(N²) time complexity for very long digit strings (this is the same general bug class as `OBJ_obj2txt()`/OpenSSL's CVE-2023-2650: an unbounded-length numeric string is fed into an algorithm whose cost is quadratic in the number of digits). In `JsonFormat.java`, `parseInt64`, `parseUint32`, and `parseUint64` all defer to `parseBigDecimal()` whenever the primitive `Long.parse*` fails (which happens for any numeric string longer than ~19-20 digits, a value trivially reachable via a public `int64`/`uint64` JSON field in any proto schema): [1](#0-0) [2](#0-1) 

Notably, this exact code path already contains a mitigating check — `parseBigDecimal` enforces `MAX_NUMERIC_STRING_LENGTH = 1000` before calling `new BigDecimal(value)`, with an explicit comment referencing the O(N²) `BigDecimal(String)` DoS: [3](#0-2) 

And a regression test exercises exactly this scenario (10,000-digit numeric string rejected before expensive parsing): [4](#0-3) 

This shows the invariant does transfer from the OpenSSL bug class to Protobuf's ProtoJSON parsing surface (unbounded attacker-controlled numeric-string length fed to a quadratic conversion routine), but in this checkout the check is already present and enforced ahead of the vulnerable call, so the specific `BigDecimal` path is not currently exploitable.

By contrast, all other numeric text-conversion sites checked in this repo do not exhibit the flaw at all, because they never operate on unbounded-length digit strings:
- Binary wire varints are hard-capped at 10 bytes (64 bits), so binary-to-text conversion of integers (C++ `absl::StrCat`, Java `Long.toString`/`BigInteger` for unsigned 64-bit, Objective-C `%lld`/`%llu`) is always O(1) per value: [5](#0-4) 
- upb's JSON decoder (`php-upb.c`, `ruby-upb.c`, `upb/json/decode.c`) copies numeric literals into a fixed 64-byte stack buffer and explicitly rejects "excessively long number" before calling `strtod`, and delegates integer parsing to `upb_BufToInt64`/`upb_BufToUint64`, which operate over the length-bounded literal directly rather than an arbitrary-precision library: [6](#0-5) 
- The C++ JSON lexer/parser path (`src/google/protobuf/json/internal/lexer.cc`, `parser.cc`) uses `absl::SimpleAtoi`/`absl::SimpleAtod` (linear-time) and never invokes an arbitrary-precision integer/decimal type: [7](#0-6) 
- PHP's `GPBUtil` uses BCMath (`bccomp`, `bcsub`, `bcdiv`, `bcmod`) only on already-validated numeric values within the 64-bit range, not on arbitrary-length attacker strings: [8](#0-7) 

I was not able to fully audit every language binding's ProtoJSON numeric-parsing code path (e.g., the full call chain feeding user JSON strings into `GPBUtil::checkInt64`/`checkUint64` in PHP, or the Ruby JRuby native binding) within the available search budget, so I cannot rule out an unguarded quadratic-conversion path elsewhere with full certainty — but nothing surfaced in the areas inspected.

## Impact Explanation
If the `MAX_NUMERIC_STRING_LENGTH` guard were absent or bypassable, an attacker who can submit a bounded (but not necessarily huge) ProtoJSON payload containing a single `int64`/`uint64` field with a very long digit string could trigger O(N²) CPU consumption in `JsonFormat.Parser.merge()`, causing denial of service for the consuming application — the same class of impact as the OpenSSL advisory. Because the guard is present, the reachable impact in this snapshot is effectively closed: `parseBigDecimal` fails fast with `InvalidProtocolBufferException` once the string exceeds 1000 characters, well before invoking the quadratic constructor.

## Likelihood Explanation
Low, given the current code. The vulnerable pattern from the OpenSSL report (unbounded attacker string → quadratic text/number conversion) does structurally transfer to `JsonFormat`'s BigDecimal fallback, and this is a genuine, ordinary-client-reachable public parse API path (`JsonFormat.parser().merge(json, builder)`) requiring only a schema with an `int64`/`uint64` field — no privileged access or hostile schema needed. But the length check already forecloses exploitation. This finding is best treated as "confirmed-fixed instance of the analog bug class," worth flagging so the guard is not regressed, rather than an exploitable Medium-severity vulnerability today.

## Recommendation
- Preserve `MAX_NUMERIC_STRING_LENGTH` in `JsonFormat.java` and ensure any future refactor of `parseInt64`/`parseUint32`/`parseUint64`/`parseBigDecimal` keeps the length check ahead of `new BigDecimal(value)`.
- Audit other language bindings' ProtoJSON quoted-integer/float parsing (PHP `GPBJsonWire`, Ruby, C#, Objective-C) to confirm none pass unbounded attacker strings into an arbitrary-precision constructor without an equivalent pre-check.
- Add/keep a conformance-suite or fuzz test that submits maximal-length (e.g., 10⁴–10⁶ digit) quoted integers to each language's ProtoJSON parser and asserts fast rejection.

## Proof of Concept
Using the existing regression test as the reproduction: submitting `{"optionalInt64":"1000...0"}` (10,000 digits) to `JsonFormat.parser().merge(...)` is expected to throw `InvalidProtocolBufferException` quickly (rejected by the length check) rather than hang, exactly as asserted by: [4](#0-3) 

If this test were removed or the length check deleted, the same payload would instead flow into `new BigDecimal(value)` at: [9](#0-8) 
reproducing the OpenSSL-class O(N²) DoS in Protobuf's JSON parsing surface. I did not execute this test in this session; the assertion is based on static reading of the code and test, not an observed run.

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2255-2273)
```java
    private long parseInt64(JsonElement json) throws InvalidProtocolBufferException {
      try {
        return Long.parseLong(json.getAsString());
      } catch (RuntimeException e) {
        // Fall through.
      }
      // JSON doesn't distinguish between integer values and floating point values so "1" and
      // "1.000" are treated as equal in JSON. For this reason we accept floating point values for
      // integer fields as well as long as it actually is an integer (i.e., round(value) == value).
      try {
        BigDecimal value = parseBigDecimal(json.getAsString());
        return value.longValueExact();
      } catch (RuntimeException e) {
        InvalidProtocolBufferException ex =
            new InvalidProtocolBufferException("Not an int64 value: " + json);
        ex.initCause(e);
        throw ex;
      }
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2305-2321)
```java
    private static final BigDecimal MAX_UINT32 = new BigDecimal(0xFFFFFFFFL);
    private static final BigDecimal MAX_UINT64 =
        new BigDecimal(new BigInteger("FFFFFFFFFFFFFFFF", 16));

    // Maximum length for numeric strings passed to BigDecimal constructor.
    // BigDecimal(String) has O(N^2) time complexity for N-digit strings on JDK < 18,
    // allowing a DoS with a single long numeric JSON value. Valid protobuf numeric
    // values never exceed ~350 characters, so 1000 is a generous upper bound.
    private static final int MAX_NUMERIC_STRING_LENGTH = 1000;

    private static BigDecimal parseBigDecimal(String value) throws InvalidProtocolBufferException {
      if (value.length() > MAX_NUMERIC_STRING_LENGTH) {
        throw new InvalidProtocolBufferException(
            "Numeric value is too long: " + value.length() + " characters");
      }
      return new BigDecimal(value);
    }
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L2323-2348)
```java
    private long parseUint64(JsonElement json) throws InvalidProtocolBufferException {
      try {
        return Long.parseUnsignedLong(json.getAsString());
      } catch (RuntimeException e) {
        // Fall through.
      }

      // JSON doesn't distinguish between integer values and floating point values so "1" and
      // "1.000" are treated as equal in JSON. For this reason we accept floating point values for
      // integer fields as well as long as it actually is an integer (i.e., round(value) == value).
      try {
        BigDecimal value = parseBigDecimal(json.getAsString());
        if (value.signum() < 0 || value.compareTo(MAX_UINT64) > 0) {
          throw new InvalidProtocolBufferException("Out of range uint64 value: " + json);
        }
        if (value.remainder(BigDecimal.ONE).signum() != 0) {
          throw new InvalidProtocolBufferException("Not an uint64 value: " + json);
        }
        return value.longValue();
      } catch (RuntimeException e) {
        InvalidProtocolBufferException ex =
            new InvalidProtocolBufferException("Not an uint64 value: " + json);
        ex.initCause(e);
        throw ex;
      }
    }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L800-821)
```java
  @Test
  public void testParserRejectOverlyLongNumericStrings() throws Exception {
    // A numeric string with 10,000 digits should be rejected quickly to prevent
    // O(N^2) BigDecimal parsing DoS.
    StringBuilder sb = new StringBuilder("1");
    for (int i = 0; i < 10000; i++) {
      sb.append('0');
    }
    String longNumber = sb.toString();
    String[] fields = {
      "optionalInt32", "optionalInt64", "optionalUint32", "optionalUint64", "optionalDouble"
    };
    for (String field : fields) {
      TestAllTypes.Builder builder = TestAllTypes.newBuilder();
      try {
        mergeFromJson("{\"" + field + "\":\"" + longNumber + "\"}", builder);
        assertWithMessage("Exception expected for " + field + " with long numeric string").fail();
      } catch (InvalidProtocolBufferException expected) {
        // Expected: rejected before expensive BigDecimal construction.
      }
    }
  }
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L998-1007)
```java
  /** Convert an unsigned 64-bit integer to a string. */
  public static String unsignedToString(final long value) {
    if (value >= 0) {
      return Long.toString(value);
    } else {
      // Pull off the most-significant bit so that BigInteger doesn't think
      // the number is negative, then set it again using setBit().
      return BigInteger.valueOf(value & 0x7FFFFFFFFFFFFFFFL).setBit(63).toString();
    }
  }
```

**File:** upb/json/decode.c (L326-345)
```c
  }

parse:
  /* Having verified the syntax of a JSON number, use strtod() to parse
   * (strtod() accepts a superset of JSON syntax). */
  errno = 0;
  {
    // Copy the number into a null-terminated scratch buffer since strtod
    // expects a null-terminated string.
    char nullz[64];
    ptrdiff_t len = d->ptr - start;
    if (len > (ptrdiff_t)(sizeof(nullz) - 1)) {
      jsondec_err(d, "excessively long number");
    }
    memcpy(nullz, start, len);
    nullz[len] = '\0';

    char* end;
    double val = strtod(nullz, &end);
    UPB_ASSERT(end - nullz == len);
```

**File:** src/google/protobuf/json/internal/parser.cc (L193-237)
```text
template <typename T>
absl::StatusOr<LocationWith<T>> ParseIntInner(JsonLexer& lex, double lo,
                                              double hi) {
  absl::StatusOr<JsonLexer::Kind> kind = lex.PeekKind();
  RETURN_IF_ERROR(kind.status());

  LocationWith<T> n;
  switch (*kind) {
    case JsonLexer::kNum: {
      absl::StatusOr<LocationWith<MaybeOwnedString>> x = lex.ParseRawNumber();
      RETURN_IF_ERROR(x.status());
      n.loc = x->loc;
      if (absl::SimpleAtoi(x->value.AsView(), &n.value)) {
        break;
      }

      RETURN_IF_ERROR(ParseFloatStringAsInt<T>(*x, &n.value, lo, hi));
      break;
    }
    case JsonLexer::kStr: {
      absl::StatusOr<LocationWith<MaybeOwnedString>> str = lex.ParseUtf8();
      RETURN_IF_ERROR(str.status());

      n.loc = str->loc;

      // SimpleAtoi will ignore leading and trailing whitespace, so we need
      // to check for it ourselves.
      for (char c : str->value.AsView()) {
        if (absl::ascii_isspace(c)) {
          return lex.Invalid("non-number characters in quoted number");
        }
      }
      if (absl::SimpleAtoi(str->value.AsView(), &n.value)) {
        break;
      }

      RETURN_IF_ERROR(ParseFloatStringAsInt<T>(*str, &n.value, lo, hi));
      break;
    }
    default:
      return lex.Invalid("expected number or string");
  }

  return n;
}
```

**File:** php/src/Google/Protobuf/Internal/GPBUtil.php (L89-113)
```php
    public static function checkInt32(&$var)
    {
        if (is_numeric($var)) {
            $var = intval($var);
        } else {
            throw new \Exception("Expect integer.");
        }
    }

    public static function checkUint32(&$var)
    {
        if (is_numeric($var)) {
            if (PHP_INT_SIZE === 8) {
                $var = intval($var);
                $var |= ((-(($var >> 31) & 0x1)) & ~0xFFFFFFFF);
            } else {
                if (bccomp($var, 0x7FFFFFFF) > 0) {
                    $var = bcsub($var, "4294967296");
                }
                $var = (int) $var;
            }
        } else {
            throw new \Exception("Expect integer.");
        }
    }
```
